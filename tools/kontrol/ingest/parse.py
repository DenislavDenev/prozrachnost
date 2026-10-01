"""Strict source adapters. Source labels remain separate from inferred fields."""
import datetime as dt
import hashlib
import re
from urllib.parse import urljoin, unquote
from bs4 import BeautifulSoup

class ShapeError(ValueError):
    pass

def soup(raw):
    if not raw or b'CSRF verification failed' in raw or b'403 Forbidden' in raw:
        raise ShapeError('Празен или отказан отговор')
    try:
        text = raw.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError as e:
        raise ShapeError('Непроверена или повредена кодировка') from e
    s = BeautifulSoup(text, 'html.parser')
    text = s.get_text(' ', strip=True)
    if 'CSRF проверката се провали' in text or 'Грешка! Моля, опитайте по-късно!' in text:
        raise ShapeError('Източникът съобщава грешка')
    return s

def date(text):
    m = re.search(r'\b(\d{2})\.(\d{2})\.(\d{4})\b', text)
    if not m:
        return None
    try:
        return dt.date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    except ValueError as e:
        raise ShapeError('Невалидна източникова дата') from e

def base(key, source, title, url):
    return dict(id=key, source=source, title=title, url=url, published_on=None,
                publication_year=None, completed_on=None, report_period=None,
                kind=None, sector=None, auditee=None, eik=None, unp=None,
                recommendation_text=None, categories=[source])

def nao(raw, source, url, year=None):
    s = soup(raw)
    links = s.select('a.download_file[href]')
    if not links:
        # A valid empty filtered page has the source container and filter form.
        if s.select_one('#boxes-container') and s.select_one('form.filters'):
            return []
        raise ShapeError('Липсва списъкът на Сметната палата')
    rows = []
    for a in links:
        m = re.search(r'/documents/(\d+)/', a['href'])
        h = a.select_one('.file-heading')
        if not m or not h or not h.get_text(strip=True):
            raise ShapeError('Променен идентификатор или заглавие на доклад')
        r = base('nao:' + m[1], source, h.get_text(' ', strip=True), urljoin(url, a['href']))
        r['publication_year'] = str(year) if year else None
        for p in a.select('.file-details p'):
            t = p.get_text(' ', strip=True)
            if t.startswith('Категория:'):
                r['sector'] = t.split(':', 1)[1].strip()
            elif t.startswith('Тип:'):
                r['kind'] = t.split(':', 1)[1].strip()
            else:
                raise ShapeError('Непознато поле в метаданните на доклад')
        desc = a.select_one('.description')
        r['recommendation_text'] = desc.get_text(' ', strip=True) if desc else None
        periods = re.findall(r'\d{2}\.\d{2}\.\d{4}', r['title'])
        r['report_period'] = ' до '.join(periods) if periods else None
        rows.append(r)
    # Some source lists repeat exactly the same document. Retain occurrences for
    # inventory reconciliation, and deduplicate only after checking all fields.
    canonical(rows)
    return rows

def adfi(raw, url):
    s = soup(raw)
    tables = [t for t in s.select('table') if 'Дата на публикуване' in t.get_text()]
    if not tables:
        raise ShapeError('Липсва регистърът на АДФИ')
    rows = []
    for table in tables:
        headers = [x.get_text(' ', strip=True) for x in table.select('thead th')]
        if headers != ['Доклад от финансова инспекция', 'Обект', 'Основание за възлагане', 'Дата на публикуване']:
            raise ShapeError('Променени колони на АДФИ')
        for tr in table.select('tbody > tr'):
            cells = tr.find_all('td', recursive=False)
            if len(cells) != 4 or not cells[0].select_one('a[href]'):
                raise ShapeError('Непълен ред на АДФИ')
            a = cells[0].select_one('a[href]')
            doc = urljoin(url, a['href'])
            ident = re.search(r'/upload/(\d+)/', doc)
            if not ident or not doc.lower().endswith('.pdf'):
                raise ShapeError('Невалидна връзка на АДФИ')
            r = base('adfi:' + ident[1], 'adfi', a.get_text(' ', strip=True), doc)
            r.update(auditee=cells[1].get_text(' ', strip=True), kind='Публикуван доклад от финансова инспекция',
                     basis=cells[2].get_text(' ', strip=True), published_on=date(cells[3].get_text()))
            if not r['published_on']:
                raise ShapeError('Липсва дата на публикация на АДФИ')
            r['publication_year'] = r['published_on'][:4]
            # The stable upload ID identifies a document; rewritten bytes are versions.
            r['updated_label'] = 'актуализиран' in r['auditee'].lower()
            rows.append(r)
    if len({r['id'] for r in rows}) != len(rows):
        raise ShapeError('Дублиран документ на АДФИ')
    return rows

def adfi_history(raw, url):
    s = soup(raw)
    rows = []
    for a in s.select('a[href]'):
        title = a.get_text(' ', strip=True)
        if not title.startswith('Приключили финансови инспекции през'):
            continue
        m = re.search(r'(първото|второто|третото|четвъртото) тримесечие на (\d{4})', title)
        if not m or not a['href'].lower().endswith('.pdf'):
            raise ShapeError('Променен исторически списък на АДФИ')
        quarter = ['първото', 'второто', 'третото', 'четвъртото'].index(m[1]) + 1
        r = base('adfi-list:' + m[2] + '-Q' + str(quarter), 'adfi-history', title, urljoin(url, a['href']))
        r.update(report_period=m[2] + '-Q' + str(quarter), kind='Тримесечен списък', publication_year=None)
        rows.append(r)
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ShapeError('Непълен или дублиран исторически каталог')
    return rows

def postback_form(raw):
    s = soup(raw)
    d = {e['name']: e.get('value', '') for e in s.select('input[name]') if e.get('type') not in ('submit', 'image')}
    if '__VIEWSTATE' not in d:
        raise ShapeError('Липсва ASP.NET състояние')
    for e in s.select('select[name]'):
        option = e.select_one('option[selected]') or e.select_one('option')
        if not option:
            raise ShapeError('Празен ASP.NET избор')
        d[e['name']] = option.get('value', '')
    return d

def event_target(href):
    m = re.search(r"__doPostBack\('([^']+)'", href) or re.search(r'PostBackOptions\("([^"]+)"', href)
    if not m:
        raise ShapeError('Променена postback връзка')
    return m[1]

def cpc(raw, url):
    s = soup(raw)
    counter = re.search(r'Резултати\s+(\d+)\s*-\s*(\d+)\s*от\s*\((\d+)\)', s.get_text(' ', strip=True))
    if not counter:
        raise ShapeError('КЗК: няма доказан брояч; началният GET не е празен регистър')
    rows = []
    for a in s.find_all('a', id=re.compile(r'_lblDecNumber$')):
        prefix = a['id'].removesuffix('lblDecNumber')
        row = a
        while row and not row.find('input', id=prefix + 'hidden'):
            row = row.parent
        if row is None:
            raise ShapeError('Липсва контекстът на акт на КЗК')
        hidden = row.find('input', id=prefix + 'hidden')
        text = row.get_text(' ', strip=True)
        case = row.find('a', id=prefix + 'lnkDossNumber')
        when = row.find(id=prefix + 'lblDecisionDate')
        pub = row.find(id=prefix + 'lblDateCreated')
        # Source uses different date label IDs; the date-labelled text is authoritative.
        decision = re.search(r'Дата на решение:\s*(\d{2}\.\d{2}\.\d{4})', text)
        if not hidden or not case or not decision or not pub:
            raise ShapeError('Непълен идентификатор, производство или дата на КЗК')
        act_no = a.get_text(' ', strip=True)
        dossier = hidden.get('value') or None
        if dossier is not None and not dossier.isdigit():
            raise ShapeError('Невалиден идентификатор на производство')
        act_date = date(decision[1])
        key = 'cpc:' + (dossier or 'missing-dossier') + ':decision:' + act_no + ':' + act_date
        r = base(key, 'cpc', act_no, urljoin(url, 'Dossier.aspx?DossID=' + dossier) if dossier else url)
        case_no = case.get_text(' ', strip=True)
        r.update(case_no=case_no if case_no != '-' else None, source_case_id=dossier, act_date=act_date, act_no=act_no,
                 kind='Решение', published_on=date(pub.get_text()), outcome_text=None)
        r['court_result'] = None
        r['complaint_status'] = None
        r['source_gap'] = 'Източникът не посочва производство' if dossier is None else None
        r['publication_year'] = r['published_on'][:4] if r['published_on'] else None
        law = re.search(r'Вид производство:\s*(.*?)\s*Предмет', text)
        r['law'] = law[1] if law else None
        result = re.search(r'Произнасяне:\s*(.*?)\s*Правно основание:', text)
        # Outcomes can name natural persons. Keep only the label, not private party details.
        r['outcome_text'] = result[1].split(' - ')[0] if result else None
        r['unp'] = verified_unp(text)
        pdf = row.find('a', id=prefix + 'docTypes_linkBtnPDF')
        r['pdf_event'] = event_target(pdf['href']) if pdf else None
        document = pdf or row.find('a', id=prefix + 'docTypes_linkBtnDoc') or row.find('a', id=prefix + 'docTypes_linkBtnODF')
        r['document_event'] = event_target(document['href']) if document else None
        rows.append(r)
    first, last, total = map(int, counter.groups())
    nxt = s.find('a', id=re.compile(r'_lnkButtonNext$'))
    # Verified source pager prints a full 30-row range on its shorter final page.
    # This exception never relaxes the authoritative total or actual row count.
    terminal_style = (nxt.get('style','').replace(' ','').rstrip(';') == 'color:black') if nxt else True
    padded_final = last > total and last == first + 29 and len(rows) == total - first + 1 and (not nxt or not nxt.get('href') or terminal_style)
    actual_last = total if padded_final else last
    if len(rows) != actual_last - first + 1 or (last > total and not padded_final) or first < 1 or len({r['id'] for r in rows}) != len(rows):
        raise ShapeError('КЗК: несъответствие на страница и брояч')
    if padded_final:
        rows[0]['source_pager_discrepancy'] = dict(first=first,printed_end=last,total=total,actual_rows=len(rows),next_link_style=nxt.get('style') if nxt else None)
    return rows, (first, actual_last, total), event_target(nxt['href']) if nxt and nxt.get('href') and actual_last < total else None

def verified_unp(text):
    values = set(re.findall(r'(?<!\d)\d{5}-\d{4}-\d{4}(?!\d)', text))
    return next(iter(values)) if len(values) == 1 else None

def canonical(rows):
    out = {}
    import json
    # One deterministic merge order; verified values fill unknowns only.
    for r in sorted(rows, key=lambda r: (r['id'], json.dumps(r, sort_keys=True, ensure_ascii=False))):
        key = r['id']
        if key not in out:
            out[key] = dict(r)
        else:
            old = out[key]
            for field in ('title', 'url'):
                if old.get(field) is not None and r.get(field) is not None and old[field] != r[field]:
                    raise ShapeError('Противоречиви версии/категории за ' + key)
            for field in ('publication_year', 'kind', 'sector'):
                vals = set(old.get('source_' + field + '_values', [])) | set(r.get('source_' + field + '_values', []))
                vals |= {v for v in (old.get(field), r.get(field)) if v is not None}
                if len(vals) > 1:
                    old['source_' + field + '_values'] = sorted(vals)
                    old[field] = None
                    old['source_discrepancy'] = 'Един документ има различни източникови класификации; не е избрана една от тях'
                elif vals:
                    old[field] = next(iter(vals))
            for field,value in r.items():
                if field not in ('source','categories','publication_year','kind','sector') and old.get(field) is None and value is not None:
                    old[field] = value
            old['categories'] = sorted(set(old['categories'] + r['categories']))
    return [out[key] for key in sorted(out)]
