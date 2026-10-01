"""GRAO published tables: validate every municipality before returning any rows."""
import datetime as dt
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


class ShapeError(ValueError):
    pass


def norm(s):
    return re.sub(r'\s+', ' ', s.strip().upper()).replace('ОБЛ. ', '')


def catalog(raw):
    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                href = dict(attrs).get('href', '')
                url = urljoin('https://www.grao.bg/tables.html', href)
                if urlparse(url).hostname == 'www.grao.bg' and re.fullmatch(r'/tna/(?:t41(?:ob|nm)-\d{2}-\d{2}-\d{4}_\d|tadr-?\d{4})\.txt', urlparse(url).path):
                    urls.add(url)
    urls = set()
    Links().feed(raw.decode('cp1251'))
    if not urls:
        raise ShapeError('Липсват връзките към таблиците на ГРАО')
    return sorted(urls)


def parse(raw, today=None):
    """Handle modern pipe and historic exclamation tables, retaining original names.

    Returns municipality totals and settlement rows. Historic identity is not guessed.
    Regional totals are checked on permanent/current (their internal columns differ).
    """
    try:
        text = raw.decode('cp1251').replace('\ufeff', '')
    except UnicodeError as e:
        raise ShapeError('Невалидна кодировка') from e
    if '<html' in text.lower() or 'ПОСТОЯНЕН' not in text.upper() or 'НАСТОЯЩ' not in text.upper():
        raise ShapeError('Не е таблица на ГРАО')
    dates = set(re.findall(r'ДАТА\s+(\d{2}\.\d{2}\.\d{4})', text.upper()))
    if len(dates) != 1:
        raise ShapeError('Липсваща или различна дата')
    try:
        date = dt.datetime.strptime(dates.pop(), '%d.%m.%Y').date()
    except ValueError as e:
        raise ShapeError('Невалидна дата') from e
    if date > (today or dt.date.today()):
        raise ShapeError('Бъдеща дата')
    muni, places, regional, subdivisions = [], [], [], []
    oblast = municipality = None
    pending = []
    monthly = 'БРОЙ' in text.upper() and 'НАС.' in text.upper() and 'НАСЕЛЕНО МЯСТО' not in text.upper()
    seen = set()
    region_seen = set()
    for line in text.splitlines():
        s = line.strip().upper()
        if not s or s == '\x1a' or set(s) <= {'-', '|', '!'}:
            continue
        heading = re.search(r'ОБЛАСТ\s+(.+?)\s+ОБЩИНА\s+(.+)$', s)
        if heading:
            if pending:
                raise ShapeError('Липсва общински сбор преди следващата таблица')
            oblast, municipality = map(norm, heading.groups())
            continue
        old_ob = re.match(r'ОБЛАСТ:\s*(.+?)(?:\s{2,}|$)', s)
        if old_ob:
            oblast = norm(old_ob[1]); municipality = None
            continue
        old_m = re.match(r'ОБЩИНА:\s*(.+?)(?:\s{2,}|$)', s)
        if old_m:
            if pending:
                raise ShapeError('Липсва общински сбор')
            municipality = norm(old_m[1]); continue
        ob = re.fullmatch(r'ОБЛАСТ\s+(.+)', s)
        if ob:
            oblast = norm(ob[1]); municipality = None; continue
        if not s.startswith(('|', '!')):
            if re.match(r'ДАТА\s|СТР\.\s+\d+$|ТАБЛИЦА|Т А Б Л И Ц А|НА НАСЕЛЕНИЕТО', s):
                continue
            raise ShapeError('Непознат ред: ' + s[:100])
        cells = [c.strip() for c in s.split(s[0])[1:-1]]
        if not cells:
            continue
        name = cells[0]
        if not name or name in ('1', 'ОБЩИНА', 'НАСЕЛЕНО МЯСТО'):
            continue
        if not oblast:
            raise ShapeError('Ред без област')
        if name.startswith('В Т.Ч.'):
            if len(cells) not in (4, 9) or not cells[1].isdigit():
                raise ShapeError('Невалиден район')
            subdivisions.append(dict(oblast=oblast, municipality=municipality, name=name))
            continue
        if ('КМЕТСТВО' in name or 'Р-Н' in name or name.startswith('ОБЩИНСКИ ЦЕНТ')) and all(c == '0' for c in cells[1:]):
            subdivisions.append(dict(oblast=oblast, municipality=municipality, name=name))
            continue
        if monthly:
            if len(cells) != 10:
                raise ShapeError('Променени колони на общинската таблица')
            if name.startswith('ВСИЧКО ЗА ОБЛАСТТА'):
                nums = (int(cells[2]), int(cells[6]))
            else:
                if not all(c.isdigit() for c in cells[1:]):
                    raise ShapeError('Невалидна стойност: ' + name)
                v = list(map(int, cells[2:]))
                if v[0] != sum(v[1:4]) or v[4] != sum(v[5:8]) or v[1] != v[5]:
                    raise ShapeError('Несъвпадащи колони: ' + name)
                muni.append(dict(oblast=oblast, municipality=name, permanent=v[0], current=v[4], both=v[1]))
                continue
        else:
            if len(cells) not in (4, 9) or not all(c.isdigit() for c in cells[1:]):
                raise ShapeError('Променени колони или празно число: ' + name)
            v = list(map(int, cells[1:]))
            if len(v) == 8:
                if v[0] != sum(v[1:4]) or v[4] != sum(v[5:8]) or v[1] != v[5]:
                    raise ShapeError('Несъвпадащи колони: ' + name)
                v = [v[0], v[4], v[1]]
            if v[2] > min(v[:2]):
                raise ShapeError('Общият брой адреси е по-голям от общия брой лица')
            if name.startswith('ВСИЧКО ЗА ОБЛАСТТА'):
                nums = tuple(v[:2])
            elif name.startswith('ВСИЧКО ЗА ОБЩИНАТА'):
                if not pending or [sum(p[k] for p in pending) for k in ('permanent', 'current', 'both')] != v:
                    raise ShapeError('Общинският сбор не съвпада: ' + str(municipality))
                muni.append(dict(oblast=oblast, municipality=municipality, permanent=v[0], current=v[1], both=v[2]))
                places.extend(pending); pending = []; continue
            else:
                if not municipality or not re.match(r'(ГР\.|С\.|МАН\.|К\.|КК\.|К\.К\.|ПЛОВДИВ \d|СОФИЯ \d)', name):
                    raise ShapeError('Непознато населено място: ' + name)
                key = (oblast, municipality, name)
                seen.add(key)
                pending.append(dict(oblast=oblast, municipality=municipality, name=name, permanent=v[0], current=v[1], both=v[2])); continue
        if oblast in region_seen:
            raise ShapeError('Дублиран областен сбор')
        region_seen.add(oblast)
        if tuple(sum(m[k] for m in muni if m['oblast'] == oblast) for k in ('permanent', 'current')) != nums:
            raise ShapeError('Областният сбор не съвпада: ' + oblast)
        regional.append(oblast)
    if pending or len(muni) < 260:
        raise ShapeError('Отрязана или празна таблица')
    keys = [(m['oblast'], m['municipality']) for m in muni]
    if len(keys) != len(set(keys)):
        raise ShapeError('Дублирана община')
    if monthly and set(m['oblast'] for m in muni) != set(regional):
        raise ShapeError('Липсва областен сбор')
    return dict(date=date.isoformat(), kind='municipalities' if monthly else 'places', municipalities=muni, places=places, subdivisions=subdivisions)
