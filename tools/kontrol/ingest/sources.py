"""Complete, counted source traversal. No page budget can publish a partial live."""
import json
from urllib.parse import urlencode
from .config import SOURCES
from . import parse

def nao(client, source, save, progress=lambda *a: None):
    url = SOURCES[source][1]
    raw = client.get(url)
    save(source, url, raw)
    s = parse.soup(raw)
    years = [o['value'] for o in s.select('select[name=year] option[value]') if o['value']]
    if not years:
        if source == 'nao-crim':
            years = [None]
        else:
            raise parse.ShapeError('Липсват източниковите години')
    out, pages, counts = [], 0, {}
    for year in years:
        filtered = url + '?' + urlencode({'year': year}) if year else url
        raw = client.get(filtered)
        save(source, filtered, raw)
        s = parse.soup(raw)
        rows = parse.nao(raw, source, filtered, year)
        token = s.select_one('[name=csrfmiddlewaretoken]')
        if not token:
            raise parse.ShapeError('Липсва CSRF токен')
        button = s.select_one('.load_more:not(.hidden)')
        page = 1
        while button is not None:
            page += 1
            raw = client.request('POST', filtered, data={'page': page, 'csrfmiddlewaretoken': token['value']},
                                 headers={'Referer': filtered, 'X-Requested-With': 'XMLHttpRequest'})
            save(source, filtered + '&page=' + str(page), raw)
            try:
                d = json.loads(raw)
            except ValueError as e:
                raise parse.ShapeError('Пагинацията не върна JSON') from e
            if set(d) != {'success', 'html', 'hide_load_button'} or d['success'] is not True or not isinstance(d['html'], str) or not isinstance(d['hide_load_button'], bool):
                raise parse.ShapeError('Променен отговор на пагинацията')
            chunk = parse.nao(d['html'].encode(), source, filtered, year)
            if not chunk:
                raise parse.ShapeError('Празна междинна страница')
            if len(rows) >= len(chunk) and [r['id'] for r in rows[-len(chunk):]] == [r['id'] for r in chunk]:
                raise parse.ShapeError('Повторена страница на Сметната палата')
            rows += chunk
            progress(source, year, page, len(rows))
            if d['hide_load_button']:
                break
        pages += page
        counts[year or 'не е посочена'] = len(rows)
        out += rows
        progress(source, year, page, len(rows))
    unique = parse.canonical(out)
    return out, dict(complete=True, occurrence_count=len(out), unique_count=len(unique), duplicate_occurrences=len(out)-len(unique),
                    source_count=None, pages=pages, year_counts=counts,
                    scope='Всички години на публикуване от източниковия филтър; терминален hide_load_button за всяка година')

def adfi(client, source, save, progress=lambda *a: None):
    url = SOURCES[source][1]
    raw = client.get(url)
    save(source, url, raw)
    rows = parse.adfi(raw, url) if source == 'adfi' else parse.adfi_history(raw, url)
    documents = list(rows)
    gaps, inspection_count = [], 0
    if source == 'adfi-history':
        from .pdf import historical
        for document in documents:
            data = client.get(document['url'])
            save(source, document['url'], data)
            try:
                inspections, _ = historical(data, document)
                document['inspection_count'] = len(inspections)
                document['inspection_status'] = 'Текстов индекс, последователни номера и край на таблицата проверени'
                rows += inspections
                inspection_count += len(inspections)
            except parse.ShapeError as e:
                from .store import digest
                document['inspection_count'] = None
                document['inspection_status'] = 'само документ: ' + str(e)
                gaps.append(dict(id=document['id'], url=document['url'], sha256=digest(data), error=str(e)))
            progress(source, document['report_period'], len(documents), inspection_count)
    progress(source, None, 1, len(rows))
    return rows, dict(complete=True, occurrence_count=len(rows), source_count=None, pages=1,
                     document_count=len(documents), inspection_count=inspection_count, inspection_gaps=gaps,
                     scope='Всички редове в единната HTML страница; брой на документи, не на инспекции' if source == 'adfi' else 'Всички тримесечни списъци в /bg/18; броят е на списъци, не на инспекции')

def cpc(client, save, progress=lambda *a: None, document_callback=None):
    source, url = 'cpc', SOURCES['cpc'][1]
    raw = client.get(url)
    save(source, url, raw)
    data = parse.postback_form(raw)
    data['ctl00$cntPlaceHldMain$serchForm$ddlSearchIn'] = '2'
    data['ctl00$cntPlaceHldMain$serchForm$btnBottomSearch'] = 'Търсене'
    raw = client.request('POST', url, data=data)
    out, pages, total, discrepancies = [], 0, None, []
    while True:
        pages += 1
        save(source, url + '?results_page=' + str(pages), raw)
        chunk, (first, last, count), nxt = parse.cpc(raw, url)
        if chunk and chunk[0].get('source_pager_discrepancy'):
            from .store import digest
            discrepancies.append(dict(url=url,sha256=digest(raw),**chunk[0]['source_pager_discrepancy']))
        if first != len(out) + 1 or total is not None and total != count:
            raise parse.ShapeError('КЗК: променен брояч или пропусната страница')
        total = count
        if {r['id'] for r in out} & {r['id'] for r in chunk}:
            raise parse.ShapeError('КЗК: повторени актове')
        if document_callback:
            document_callback(chunk,raw,url)
        for row in chunk:
            row.pop('pdf_event',None); row.pop('document_event',None)
        out += chunk
        progress(source, None, pages, len(out))
        if last == total:
            break
        if not nxt:
            raise parse.ShapeError('КЗК: прекъсната пагинация')
        data = parse.postback_form(raw)
        data.update(__EVENTTARGET=nxt, __EVENTARGUMENT='')
        raw = client.request('POST', url, data=data)
    if len(out) != total:
        raise parse.ShapeError('КЗК: общият брояч не съвпада')
    return out, dict(complete=True, occurrence_count=len(out), source_count=total, pages=pages,
                    source_discrepancies=discrepancies,
                    scope='Всички решения в търсенето на КЗК, всички закони; други видове актове не са решения')
