"""Text-layer only extraction; no OCR and no automatic conclusion generation."""
import io
import re
import pdfplumber
from .parse import ShapeError, base
from .store import digest

def inspection_index(tables, document, sha256):
    rows = []
    for page, table in tables:
        if not table or len(table[0]) != 2:
            raise ShapeError('Променена таблица на приключилите инспекции')
        header = table[0][0] == '№' and 'Име на инспект' in (table[0][1] or '')
        if not header and (not rows or not str(table[0][0]).isdigit()):
            raise ShapeError('Променена таблица на приключилите инспекции')
        for cells in table[1:] if header else table:
            if len(cells) != 2 or not isinstance(cells[0], str) or not cells[0].isdigit() or not cells[1]:
                raise ShapeError('Непълен ред на историческа инспекция')
            number = int(cells[0])
            if number != len(rows) + 1:
                raise ShapeError('Пропуснат или повторен източников пореден номер')
            auditee = ' '.join(cells[1].split())
            r = base(document['id'] + ':inspection:' + str(number), 'adfi-history', auditee, document['url'])
            r.update(kind='Приключила финансова инспекция', auditee=auditee,
                     report_period=document['report_period'], source_row=number,
                     document_id=document['id'], document_page=page, document_sha256=sha256,
                     result_text=None, violations_count=None, damage_amount=None, currency=None,
                     excerpt=cells[1], excerpt_page=page)
            rows.append(r)
    if not rows:
        raise ShapeError('Няма надежден текстов индекс на инспекциите')
    return rows

def historical(raw, document):
    if not raw.startswith(b'%PDF-'):
        raise ShapeError('Връзката не върна PDF')
    tables = []
    details = []
    ended = False
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages):
            # Removing identical overlapping glyphs preserves source text, not OCR.
            table = page.dedupe_chars().extract_table()
            is_index = table and len(table[0]) == 2 and (table[0][0] == '№' and 'Име на инспект' in (table[0][1] or '') or tables and str(table[0][0]).isdigit())
            if not is_index:
                if tables:
                    text = page.dedupe_chars().extract_text() or ''
                    if 'Проверени задачи' not in text and 'Доклад №' not in text:
                        raise ShapeError('Не е доказан край на индекса на инспекциите')
                    ended = True
                    break
                raise ShapeError('Само документ: липсва надежден текстов индекс')
            tables.append((i + 1, table))
        rows = inspection_index(tables, document, digest(raw))
        if not ended:
            raise ShapeError('Отрязан документ: няма подробни записи след индекса')
        # Detailed results are extracted only when source sequence numbers and
        # report headers identify the same index rows without name matching.
        for i in range(len(tables), len(pdf.pages)):
            text = pdf.pages[i].dedupe_chars().extract_text() or ''
            for m in re.finditer(r'(?m)^(\d+)\s+(.+?)Доклад №\s*([^\n]+)', text):
                number = int(m[1])
                if 1 <= number <= len(rows):
                    tail = text[m.end():]
                    nxt = re.search(r'(?m)^\d+\s+.+?Доклад №', tail)
                    if nxt:
                        tail = tail[:nxt.start()]
                    v = re.search(r'Установени нарушения\s*\(общо брой\s*(\d+)\)', tail)
                    if v:
                        details.append((number, m[3].strip(), int(v[1]), i + 1))
        if len({v[0] for v in details}) == len(rows) and len(details) == len(rows):
            for number, report_no, violations, page in details:
                rows[number-1].update(report_no=report_no, violations_count=violations,
                                      result_text='Установени нарушения (общо брой ' + str(violations) + ')', result_page=page)
        else:
            for row in rows:
                row['detail_status'] = 'Индексът е прочетен; пълно съпоставяне на подробните резултати не е доказано'
    return rows, tables

def title_excerpt(raw):
    if not raw.startswith(b'%PDF-'):
        raise ShapeError('Връзката не върна PDF')
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        text = pdf.pages[0].dedupe_chars().extract_text() or ''
    # Only the source's audit subject/period is eligible for public short excerpt.
    # Names of auditors, signatures, contacts and later document text stay private.
    m = re.search(r'(?im)^(за извършен одит[^\n]*(?:\n[^\n]*){0,4}?\nза периода[^\n]*)(?:\n|$)', text)
    excerpt = m[1].strip() if m else None
    if excerpt and len(excerpt.split()) > 60:
        excerpt = None
    if excerpt and re.search(r'\b\d{10}\b|@|тел\.|комисия|одитор|подпис', excerpt, re.I):
        excerpt = None
    return dict(text_available=bool(text.strip()), excerpt=excerpt, excerpt_page=1 if excerpt else None,
                document_sha256=digest(raw), text_status='проверен източников откъс' if excerpt else 'само документ, няма надежден кратък откъс')

def cpc_document(raw):
    if not raw.startswith(b'%PDF-'):
        raise ShapeError('КЗК: изтеглянето не върна PDF')
    from .parse import verified_unp
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        first=pdf.pages[0].dedupe_chars().extract_text() or ''
        texts=[first]+[p.dedupe_chars().extract_text() or '' for p in pdf.pages[1:]]
    unp=verified_unp('\n'.join(texts))
    # Decision heading/number/date only. Commission members and body stay private.
    m=re.search(r'(?m)^Р\s*Е\s*Ш\s*Е\s*Н\s*И\s*Е\s*\n№\s*\d+\s*\n\d{2}\.\d{2}\.\d{4}[^\n]*',first)
    excerpt=m[0] if m else None
    return dict(document_sha256=digest(raw),text_available=any(t.strip() for t in texts),
                excerpt=excerpt,excerpt_page=1 if excerpt else None,unp=unp,
                text_status='източниково заглавие, номер и дата' if excerpt else 'само документ, няма надежден откъс')
