"""The oblast and the municipality of a crash, from the names МВР writes, matched to core. Never guessed: a name
that does not match exactly one municipality of its oblast stays without one."""
import csv
from collections import defaultdict

from .config import ROOT


def regions():
    """{name of the region in ptp.zip: oblast code} for the two names that differ from core: СОФИЯ is the capital
    and СОФИЙСКА is the oblast (db/ref/mvr_region.csv, with the evidence)."""
    with open(ROOT / 'db' / 'ref' / 'mvr_region.csv', encoding='utf-8', newline='') as f:
        return {r['region']: r['oblast_code'] for r in csv.DictReader(f)}


def aliases():
    """{(region, municipality): municipality code} for the names МВР writes differently from core
    (db/ref/mvr_municipality.csv, with the evidence)."""
    with open(ROOT / 'db' / 'ref' / 'mvr_municipality.csv', encoding='utf-8', newline='') as f:
        return {(r['region'], r['municipality']): r['municipality_code'] for r in csv.DictReader(f)}


def load(conn):
    """{(region, municipality) as МВР writes them: municipality code} for every pair that matches one municipality."""
    oblast = {name.upper(): code for code, name in conn.execute('SELECT oblast_code, name_bg FROM core.oblast')}
    fixed = regions()
    by_name = defaultdict(list)
    for code, oblast_code, name in conn.execute('SELECT municipality_code, oblast_code, name_bg FROM core.municipality WHERE gone_at IS NULL'):
        by_name[(oblast_code, name.upper())].append(code)
    out = {}
    for region in set(oblast) | set(fixed):
        code = fixed.get(region) or oblast.get(region)
        for (o, name), codes in by_name.items():
            if o == code and len(codes) == 1:
                out[(region, name)] = codes[0]
    known = {code for codes in by_name.values() for code in codes}
    out.update({k: v for k, v in aliases().items() if v in known})
    return out
