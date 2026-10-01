"""Strict parsers for the observed APPK v2 and NCR public search responses."""
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from bs4 import BeautifulSoup

APPK = 'https://reports.appk.government.bg'
NCR = 'https://ncr.government.bg'
class ShapeError(ValueError): pass

def soup(raw):
    if not raw or not raw.strip(): raise ShapeError('empty response')
    s=BeautifulSoup(raw,'html.parser')
    if s.title and any(x in s.title.get_text().lower() for x in ['error','грешка','unavailable']): raise ShapeError('error page')
    return s

def text(node): return node.get_text(' ',strip=True) if node else ''

def share(value):
    original=value.strip()
    if original in ('','\u2014','-'): return dict(share_pct=None,owner=None,share_text=original,participation='unknown')
    m=re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*%\s*(.*)',original)
    if not m:
        # Capital counts and prose are not a verified percentage.
        return dict(share_pct=None,owner=None,share_text=original,participation='unknown')
    percent=Decimal(m[1].replace(',','.'))
    if not 0 <= percent <= 100: raise ShapeError('participation outside 0..100')
    owner=m[2].strip() or None
    # Published owners do not prove their state ownership chain.
    participation='direct' if owner and owner.casefold().startswith(('държавна собственост','държавно участие')) else 'owner_reported' if owner and owner not in ('Активно','Неактивно') else 'unspecified'
    return dict(share_pct=str(percent),owner=owner,share_text=original,participation=participation)

def valid_eik(value):
    if not re.fullmatch(r'\d{9}',value or ''): return False
    digits=list(map(int,value))
    check=sum(a*b for a,b in zip(digits[:8],range(1,9)))%11
    if check==10: check=sum(a*b for a,b in zip(digits[:8],range(3,11)))%11
    if check==10: check=0
    return check==digits[8]

def pages(s, param='page'):
    vals=[]
    for a in s.select('.pagination a[href]'):
        if a.find_parent(class_='disabled'):continue
        q=parse_qs(urlparse(a['href']).query)
        vals.extend(int(v) for v in q.get(param,[]) if v.isdigit())
    return max(vals,default=1)

def companies(raw):
    s=soup(raw); rows=[]
    for a in s.select('a[href*="/CompanyDetails/"]'):
        m=re.fullmatch(r'/[Pp]ublic/Public/CompanyDetails/(\d+)',a['href'])
        if not m: raise ShapeError('unexpected enterprise href')
        card=a.find_parent('div',class_='p-4')
        if not card: raise ShapeError('enterprise card missing')
        values=card.select('div.h6')
        if len(values)!=2: raise ShapeError('enterprise card schema changed')
        status=text(card.select_one('span.badge'))
        if status not in ('Активно','Неактивно'): raise ShapeError('unknown status '+status)
        share_text=text(values[1])
        published_percentages=[Decimal(x.replace(',','.')) for x in re.findall(r'(\d+(?:[.,]\d+)?)\s*%',share_text)]
        warning='Публикуваните дялове надхвърлят 100%; пазим оригинала, без да поправяме стойностите.' if len(published_percentages)>1 and sum(published_percentages)>Decimal('100') else None
        rows.append(dict(id=m[1],name=text(values[0]),status=status,source_url=APPK+a['href'],source_warning=warning,**share(share_text)))
    if not rows: raise ShapeError('no enterprise cards')
    if len({r['id'] for r in rows}) != len(rows): raise ShapeError('duplicate enterprise')
    return rows,pages(s)

def ministries(raw):
    s=soup(raw);rows=[]
    for a in s.select('a[href*="/MinistryDetails/"]'):
        card=a.find_parent('div',class_='p-4')
        values=card.select('h6') if card else []
        if not values: raise ShapeError('ministry schema')
        rows.append(dict(id=a['href'].rsplit('/',1)[1],name=text(values[0]),status=text(card.select_one('span.badge'))))
    if not rows: raise ShapeError('empty ministry list')
    return rows,pages(s)

PROFILE_FIELDS={'ЕИК/Булстат':'eik_original','Вид':'kind','Правна форма':'legal_form','Седалище и адрес на управление':'seat','Орган, упражняващ правата на държавата':'principal','Членове на органите на управление и контрол':'board','КИД 2008':'kid','Категория на предприятието по смисъла на ЗС':'category','Предмет на дейност':'subject','Счетоводните стандарти, които предприятието прилага':'standards'}

def profile(raw):
    s=soup(raw);result={}
    for card in s.select('div.p-3'):
        label=card.select_one('div.text-body-secondary')
        value=card.select_one('div.h6')
        if label and value:
            name=text(label)
            if name in ('Телефон:','E-mail:','Уеб сайт:'): continue
            if name not in PROFILE_FIELDS: raise ShapeError('unknown profile field '+name)
            result[PROFILE_FIELDS[name]]=', '.join(text(li) for li in value.select('li')) if name.startswith('Членове') and value.select('li') else text(value)
    if set(result)!=set(PROFILE_FIELDS.values()): raise ShapeError('missing profile fields '+str(set(PROFILE_FIELDS.values())-set(result)))
    result['eik']=result['eik_original'] if valid_eik(result['eik_original']) else None
    # Split at the source country marker, not at commas inside names.
    result['board_original']=result['board']
    members=re.findall(r'([^:]+?),\s*Държава:\s*([^,]+)(?:,\s*|$)',result['board_original'])
    result['board']=[dict(name=name.strip(' ,'),country=country.strip()) for name,country in members]
    if not result['board'] and result['board_original'].strip() not in ('','-','\u2014'):
        raise ShapeError('nonempty board list is not parseable')
    if re.sub(r'([^:]+?),\s*Държава:\s*([^,]+)(?:,\s*|$)','',result['board_original']).strip(' ,') not in ('','-','\u2014'):
        raise ShapeError('board list has unclassified content')
    result['report_links']=sorted({APPK+a['href'] for a in s.select('a[href]') if re.search(r'/CompanyDetails(?:Annual|Quarterly)Report/\d+$',a['href'])})
    result['report_pages']=sorted({APPK+a['href'] for a in s.select('.pagination a[href]') if 'disabled' not in a.parent.get('class',[]) and 'active' not in a.parent.get('class',[])})
    return result

def concessions(raw):
    s=soup(raw);table=s.select_one('#ConcessionsTable')
    if not table: raise ShapeError('NCR form/error is not search data')
    total=re.search(r'Общо:\s*(\d+)',text(s))
    if not total: raise ShapeError('NCR counter absent')
    rows=[]
    for tr in table.select('tbody tr'):
        cells=tr.find_all('td',recursive=False)
        if len(cells)!=12: raise ShapeError('NCR expected 12 columns')
        v=list(map(text,cells))
        if not re.fullmatch(r'[0-9a-f-]{36}',v[0]): raise ShapeError('invalid NCR source UUID')
        rows.append(dict(id=v[0],reg_number=v[1],status=v[2],procedure_status=v[3],name=v[4],kind=v[5],subject=v[6],area_extent=v[7],conceder=v[8],related_id=v[10] or None,related_reg=v[11] or None,source_url=NCR+'/ConcessionaireProcedures/ConcessionaireProcedureInfo/'+v[0],municipalities=[],concessionaire_eik=None))
    if not rows: raise ShapeError('empty NCR search')
    if len({r['id'] for r in rows})!=len(rows): raise ShapeError('duplicate concession')
    return rows,int(total[1]),pages(s)

def national_count(rows): return len({r['id'] for r in rows})

def report(raw,url):
    import datetime as dt
    from zoneinfo import ZoneInfo
    s=soup(raw)
    section=s.select_one('section.annual-report') or s.select_one('section.quarterly-report')
    if not section: raise ShapeError('report section absent')
    value=text(section)
    year=re.search(r'Година:\s*(\d{4})',value)
    if not year: raise ShapeError('report year absent')
    date=re.search(r'Дата на подаване:\s*(\d{2}\.\d{2}\.\d{4})',value)
    filed_on=dt.datetime.strptime(date[1],'%d.%m.%Y').date() if date else None
    if filed_on and filed_on>dt.datetime.now(ZoneInfo('Europe/Sofia')).date():raise ShapeError('report filing date is in the future')
    documents=[]
    for a in section.select('a[href]'):
        if '/Download/' not in a['href']:continue
        card=a.find_parent('div',class_='card')
        title=text(card.select_one('.fw-medium')) if card else ''
        if not title: raise ShapeError('document title absent')
        documents.append(dict(id=a['href'].rsplit('/',1)[1],title=title,url=APPK+a['href']))
    kind='annual' if 'Annual' in url else 'quarterly'
    quarter=next((n for word,n in [('Първо',1),('Второ',2),('Трето',3),('Четвърто',4)] if word.casefold() in value.casefold()),None) if kind=='quarterly' else None
    if kind=='quarterly' and quarter is None:raise ShapeError('quarterly report period absent')
    report_id=url.rsplit('/',1)[1]
    return dict(id=f'{kind}:{report_id}:{year[1]}:{quarter or "annual"}',report_id=report_id,kind=kind,year=int(year[1]),quarter=quarter,filed_on=filed_on.isoformat() if filed_on else None,title=text(section.select_one('h3')),documents=documents,source_url=url,financials=None)

def concession_detail(raw):
    s=soup(raw)
    if not s.find(string=lambda x:x and 'Информация за партида' in x):raise ShapeError('concession detail absent')
    return dict(documents=[dict(url=NCR+a['href'],title=text(a)) for a in s.select('a[href]') if a['href'].startswith('/File/Download/')],notice_links=sorted({NCR+a['href'] for a in s.select('a[href]') if a['href'].startswith('/Preview/AssignedConcession/')}))

def assigned_notice(raw):
    s=soup(raw);value=text(s)
    if '9.5. Основно място на изпълнение на концесията:' in value:
        location=value.split('9.5. Основно място на изпълнение на концесията:',1)[1].split('9.6.',1)[0]
        term=re.search(r'9.7.1.*?\(месецa\):\s*(\d+)\s*месеца',value)
    elif value.startswith('АКТУАЛНА ИНФОРМАЦИЯ ЗА КОНЦЕСИЯТА') and '7.3. Местонахождение на обекта на концесията:' in value:
        location=value.split('7.3. Местонахождение на обекта на концесията:',1)[1].split('7.4.',1)[0]
        term=re.search(r'7.5. Конкретен срок на концесията:\s*(\d+)\s*месеца',value)
    else:raise ShapeError('assigned notice schema absent')
    municipalities=re.findall(r'Община:\s*([^,]+)',location)
    eik=re.search(r'ЕИК \(друга приложима информация за регистрация\):\s*(\d+)',value)
    name=re.search(r'(?:6.1. )?Име/Наименование:\s*(.*?)\s*ЕИК',value)
    places=[dict(oblast=oblast.strip(),municipality=municipality.strip()) for oblast,municipality in re.findall(r'Област:\s*([^,]+),\s*Община:\s*([^,]+)',location)]
    return dict(municipality_names=municipalities,location_places=places,location_original=location,concessionaire_eik=eik[1] if eik and valid_eik(eik[1]) else None,concessionaire_name=name[1] if name else None,term_months=int(term[1]) if term else None,financials=None)
