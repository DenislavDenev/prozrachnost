"""What may not reach the serving layer. The source text stays on the ingest side; the clean tables name the fields
that are replaced (private_fields) and the publication puts MASK in their place."""
import re

MASK = 'Източников запис: лични данни не се преиздават'
FIELDS = ('title', 'auditee', 'recommendation_text', 'excerpt', 'outcome_text')
PERSONAL = re.compile(r'\b\d{10}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+359|00359)\s*\d')
SOLE_TRADER = re.compile(r'\bЕТ\s')


def is_private(value):
    """A text that holds a personal number, an e-mail or a phone, or names a sole trader (a person's name)."""
    return bool(value) and bool(PERSONAL.search(value) or SOLE_TRADER.search(value))


def private_fields(row):
    return [f for f in FIELDS if is_private(row.get(f))]


def public_row(row):
    r = dict(row)
    for field in private_fields(r):
        r[field] = MASK
    if r.get('eik') and not re.fullmatch(r'\d{9}|\d{13}', r['eik']):
        r['eik'] = None
    return r
