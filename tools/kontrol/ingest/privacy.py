import re

def public_row(row):
    r=dict(row)
    for field in ['title','auditee','recommendation_text','excerpt','outcome_text']:
        value=r.get(field)
        if value and (re.search(r'\b\d{10}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+359|00359)\s*\d',value) or re.search(r'\bЕТ\s',value)):
            r[field]='Източников запис: лични данни не се преиздават'
    if r.get('eik') and not re.fullmatch(r'\d{9}|\d{13}',r['eik']):
        r['eik']=None
    return r
