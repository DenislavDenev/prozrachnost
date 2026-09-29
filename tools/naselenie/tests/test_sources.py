import copy
import datetime as dt
from pathlib import Path

import pytest

from ingest.parse import ShapeError, catalog, parse
from ingest.reference import ekatte, enrich

FIX = Path(__file__).parent/'fixtures'
NOW = dt.date(2026,9,29)


def sample():
    return (FIX/'grao/t41nm-15-09-2026_2.txt').read_bytes()


def test_actual_source_totals():
    # GRAO 15.09.2026, first table; checked against the downloaded source, not a generated fixture.
    d=parse(sample(),NOW)
    assert len(d['municipalities'])==265
    assert len(d['places'])==5138
    assert (d['places'][0]['permanent'],d['places'][0]['current'],d['places'][0]['both'])==(9943,9190,7745)
    assert d['municipalities'][0]['current']==12795
    assert sum(r['current'] for r in d['municipalities'])==7393257
    assert any(r['permanent']==0 and r['current']==6 for r in d['places'])


def test_actual_sources_are_different_and_must_stay_separate():
    a=parse(sample(),NOW)
    b=parse((FIX/'grao/t41ob-15-09-2026_1.txt').read_bytes(),NOW)
    assert a['municipalities'][0]==b['municipalities'][0]
    assert a['municipalities'][15]['current']==218576
    assert b['municipalities'][15]['current']==218590


def test_historic_real_formats():
    assert len(parse((FIX/'grao/tadr2019.txt').read_bytes(),NOW)['municipalities'])==265
    assert len(parse((FIX/'grao/tadr-2005.txt').read_bytes(),NOW)['municipalities'])==264


@pytest.mark.parametrize('raw',[b'',b'<html>error</html>',sample()[:3000],sample().replace(b'9943',b'9944',1),sample().replace(b'9943',b'    ',1)],ids=['empty','html','truncated','wrong-total','missing-number'])
def test_invalid_never_publishes(raw):
    with pytest.raises(ShapeError): parse(raw,NOW)


def test_duplicate_row_breaks_reconciliation():
    raw=sample(); lines=raw.splitlines(keepends=True)
    row=next(x for x in lines if b'9943' in x)
    with pytest.raises(ShapeError):parse(raw.replace(row,row+row,1),NOW)


def test_future_date():
    with pytest.raises(ShapeError):parse(sample(),dt.date(2026,9,14))


def test_reference_never_guesses_ambiguous_names():
    d=enrich(parse(sample(),NOW),ekatte((FIX/'ekatte.zip').read_bytes()))
    assert d['unmatched']==25
    assert d['places'][0]['ekatte']=='02676'
    ambiguous=[r for r in d['places'] if r['name']=='С.ВЪЛЧОВЦИ' and r['municipality']=='ЕЛЕНА']
    assert len(ambiguous)==2 and all(r['ekatte'] is None for r in ambiguous)
    assert len({m['code'] for m in d['municipalities']})==265


def test_catalog_real_links_and_html_error():
    urls=catalog((FIX/'grao/tables.html').read_bytes())
    assert 'https://www.grao.bg/tna/tadr2019.txt' in urls
    assert len(urls)==len(set(urls))
    with pytest.raises(ShapeError):catalog(b'<html>error</html>')
