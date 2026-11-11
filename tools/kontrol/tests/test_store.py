"""The landing zone, the clean tables, the serving layer and the move, against a real Postgres 17 (KONTROL_TEST_DSN)."""
import datetime as dt
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from ingest import parse, pipeline, run, store
from ingest.privacy import MASK

F = Path(__file__).parent / 'fixtures'
NAO = 'https://www.bulnao.government.bg/bg/oditna-dejnost/dokladi/'


def nao(source='nao'):
    return parse.nao((F / ('recommendations.html' if source.startswith('rec') else 'nao.html')).read_bytes(), source, NAO, '2026')


def adfi():
    return parse.adfi((F / 'adfi.html').read_bytes(), 'https://www.adfi.minfin.bg/bg/34')


def cpc():
    rows = parse.cpc((F / 'cpc.html').read_bytes(), 'https://reg.cpc.bg/Search.aspx')[0]
    for r in rows:
        r.pop('pdf_event'), r.pop('document_event')
    return rows


def manifest(rs, **kw):
    return {**dict(complete=True, occurrence_count=len(rs), source_count=None, pages=1, scope='Тестов фрагмент'), **kw}


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_load_unchanged_changes_and_versions(conn):
    rs = nao()
    with pipeline.run(conn, 'kontrol', 'nao') as r:
        assert store.load(conn, r, 'nao', rs, manifest(rs)).status == 'loaded'
        assert store.load(conn, r, 'nao', rs, manifest(rs)).status == 'unchanged'
        changed = [dict(x) for x in rs]
        changed[0]['title'] += ' (обновено)'
        assert store.load(conn, r, 'nao', changed, manifest(changed)).status == 'loaded'
    assert count(conn, 'SELECT count(*) FROM kontrol.record_version') == 4
    assert count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'kontrol' AND cause = 'rewritten'") == 1
    # every landing row rebuilds the record it was stored from
    with pipeline.run(conn, 'kontrol', 'build') as r:
        same, n = store.build(conn, r)
    assert same == n == 3
    assert count(conn, "SELECT count(*) FROM kontrol.audit_report WHERE title LIKE '%(обновено)'") == 1


def test_an_incomplete_read_writes_nothing(conn):
    rs = nao()
    with pipeline.run(conn, 'kontrol', 'nao') as r:
        with pytest.raises(parse.ShapeError):
            store.load(conn, r, 'nao', rs[:1], dict(manifest(rs[:1]), source_count=3))
        with pytest.raises(parse.ShapeError):
            store.load(conn, r, 'nao', rs, dict(manifest(rs), complete=False))
        with pytest.raises(parse.ShapeError, match='поле'):
            store.load(conn, r, 'nao', [dict(rs[0], unknown='x')], manifest(rs[:1]))
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'kontrol'") == 0
    assert count(conn, 'SELECT count(*) FROM kontrol_src.audit_report') == 0


def test_a_shorter_read_waits_a_day(conn):
    rs = nao()
    now = dt.datetime.now(dt.timezone.utc)
    with pipeline.run(conn, 'kontrol', 'nao') as r:
        store.load(conn, r, 'nao', rs, manifest(rs), now=now)
        assert store.load(conn, r, 'nao', rs[:1], manifest(rs[:1]), now=now).status == 'held'
        emptied = [dict(x) for x in rs]
        emptied[0]['sector'] = None
        assert store.load(conn, r, 'nao', emptied, manifest(emptied), now=now).status == 'held'
        assert store.load(conn, r, 'nao', rs[:2], manifest(rs[:2]), now=now + dt.timedelta(hours=1)).status == 'held'
        assert store.load(conn, r, 'nao', rs[:2], manifest(rs[:2]), now=now + dt.timedelta(hours=23)).status == 'held'
        assert store.load(conn, r, 'nao', rs[:2], manifest(rs[:2]), now=now + dt.timedelta(days=2)).status == 'confirmed'
    assert count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'kontrol' AND cause = 'confirmed'") == 1
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'kontrol' AND is_current") == 1


def load_all(conn, r, private=False):
    a = adfi()
    if private:
        a[0]['auditee'] = 'ЕТ Иван Петров, гр. Кнежа'
        a[1]['title'] = 'Доклад, контакт ivan@example.test'
    for source, rows in (('nao', nao()), ('recommendations', nao('recommendations')), ('adfi', a)):
        assert store.load(conn, r, source, rows, manifest(rows)).status == 'loaded'
    rows = cpc()
    store.load(conn, r, 'cpc', rows, manifest(rows, source_count=len(rows)))


def test_build_merges_the_lists_and_publishes_only_public_text(conn):
    with pipeline.run(conn, 'kontrol', 'refresh') as r:
        load_all(conn, r, private=True)
        assert run.finish(conn, r) == []
        assert r.stats['publication']
    # a report in both lists is one record with both categories
    merged = {x for (x,) in conn.execute("SELECT record_id FROM kontrol.audit_report WHERE cardinality(categories) = 2")}
    assert merged == {x['id'] for x in nao()} & {x['id'] for x in nao('recommendations')}
    # the source text is on the ingest side; the serving layer has the mask
    a = adfi()
    assert conn.execute('SELECT auditee, private_fields FROM kontrol.adfi_report WHERE record_id = %s', (a[0]['id'],)).fetchone() == \
        ('ЕТ Иван Петров, гр. Кнежа', ['auditee'])
    assert conn.execute('SELECT auditee FROM pub_kontrol.adfi_report WHERE record_id = %s', (a[0]['id'],)).fetchone()[0] == MASK
    assert conn.execute('SELECT title FROM pub_kontrol.adfi_report WHERE record_id = %s', (a[1]['id'],)).fetchone()[0] == MASK
    assert count(conn, "SELECT count(*) FROM pub_kontrol.adfi_report WHERE auditee LIKE '%%Иван%%' OR title LIKE '%%@%%'") == 0
    for name in ('every_record_built', 'clean_has_no_stray_record', 'read_unique_count', 'cpc_source_count', 'landing_keeps_the_record',
                 'pub_records_complete', 'no_private_text_published', 'org_unmatched'):
        assert conn.execute("SELECT ok FROM ops.check_latest WHERE tool = 'kontrol' AND name = %s", (name,)).fetchone()[0], name
    assert count(conn, 'SELECT count(*) FROM core.serving_violation') == 0
    assert count(conn, "SELECT count(*) FROM core.person_column_undecided WHERE schema_name LIKE '%%kontrol%%'") == 0


def test_fewer_records_than_published_is_a_warning_more_is_not(conn):
    def drop():
        pipeline.run_checks(conn, r, ['drop'])
        return conn.execute("SELECT actual, ok FROM ops.check_latest WHERE tool = 'kontrol' AND name = 'records_not_fewer'").fetchone()
    with pipeline.run(conn, 'kontrol', 'refresh') as r:
        rs = nao()
        store.load(conn, r, 'nao', rs[:2], manifest(rs[:2]))
        assert run.finish(conn, r) == []
        store.load(conn, r, 'nao', rs, manifest(rs))
        store.build(conn, r)
        assert drop() == (0, True)          # one more record than published
        conn.execute('DELETE FROM kontrol.audit_report WHERE record_id = %s', (rs[0]['id'],))
        conn.execute('DELETE FROM kontrol.audit_report WHERE record_id = %s', (rs[1]['id'],))
        assert drop() == (1, False)         # one record left in the clean tables, two published


def test_a_text_unmasked_in_the_serving_layer_stops_the_publication(conn, tmp_path):
    """A publication whose SELECT forgets the mask is refused inside its transaction: nothing changes."""
    import shutil
    shutil.copytree(run.ROOT / 'db' / 'publish', tmp_path / 'db' / 'publish')
    f = tmp_path / 'db' / 'publish' / '02_pub_kontrol.adfi_report.sql'
    f.write_text(re.sub(r"CASE WHEN 'auditee'.*? END AS auditee", 'auditee', f.read_text(encoding='utf-8')), encoding='utf-8')
    with pipeline.run(conn, 'kontrol', 'refresh') as r:
        load_all(conn, r, private=True)
        store.build(conn, r)
        published = pipeline.publish(conn, r, tmp_path)
    assert published.publication_id is None and any('лични данни' in p for p in published.problems)
    assert count(conn, 'SELECT count(*) FROM pub_kontrol.adfi_report') == 0


def test_an_eik_not_in_core_stays_empty_and_is_counted(conn):
    rows = adfi()
    rows[0]['eik'] = '000000000'
    with pipeline.run(conn, 'kontrol', 'adfi') as r:
        store.load(conn, r, 'adfi', rows, manifest(rows))
        store.build(conn, r)
        pipeline.run_checks(conn, r, ['unmatched'])
    assert conn.execute('SELECT eik FROM kontrol.adfi_report WHERE record_id = %s', (rows[0]['id'],)).fetchone()[0] is None
    assert conn.execute("SELECT actual FROM ops.check_latest WHERE tool = 'kontrol' AND name = 'org_unmatched'").fetchone()[0] == 1


def test_document_files_are_versions_of_the_record(conn):
    r0 = nao()[0]
    with pipeline.run(conn, 'kontrol', 'documents') as r:
        store.load(conn, r, 'nao', [r0], manifest([r0]))
        first, second = b'%PDF-original', b'%PDF-changed'

        def meta(raw):
            return dict(document_sha256=store.digest(raw), text_available=False, excerpt=None, excerpt_page=None, text_status='само документ')
        assert store.save_document(conn, r, r0, first, meta(first)).status == 'loaded'
        assert store.save_document(conn, r, r0, first, meta(first)).status == 'unchanged'
        assert store.save_document(conn, r, r0, second, meta(second)).status == 'loaded'
        assert run.finish(conn, r) == []
    assert count(conn, 'SELECT count(*) FROM pub_kontrol.document_version') == 2
    assert conn.execute('SELECT document_sha256 FROM pub_kontrol.document').fetchone()[0] == store.digest(second)


def test_documents_step_fetches_archives_and_extracts(conn, monkeypatch):
    from ingest import documents
    r0 = nao()[0]
    raw = b'%PDF-actual-response'

    class Client:
        def get(self, url):
            assert url == r0['url']
            return raw
    monkeypatch.setattr(documents, 'title_excerpt', lambda data: dict(document_sha256=store.digest(data), text_available=True,
                                                                       excerpt='Източников предмет', excerpt_page=1, text_status='проверен'))
    with pipeline.run(conn, 'kontrol', 'documents') as r:
        store.load(conn, r, 'nao', [r0], manifest([r0]))
        store.build(conn, r)
        report = documents.refresh(conn, r, Client(), 'nao')
        assert report['stored'] == 1 and report['problems'] == []
        again = documents.refresh(conn, r, Client(), 'nao')
        assert again['stored'] == 0 and again['unchanged'] == 1
        store.build(conn, r)
    assert conn.execute('SELECT document_sha256, excerpt_page FROM kontrol.document').fetchone() == (store.digest(raw), 1)


def test_a_soft_error_never_becomes_a_document(conn):
    from ingest import documents
    r0 = dict(nao()[0], url='https://official.example/old.doc')

    class Client:
        def get(self, url):
            return b'<html>captcha soft error</html>'
    with pipeline.run(conn, 'kontrol', 'documents') as r:
        store.load(conn, r, 'nao', [r0], manifest([r0]))
        store.build(conn, r)
        report = documents.refresh(conn, r, Client(), 'nao')
    assert report['stored'] == 0 and len(report['problems']) == 1
    assert count(conn, 'SELECT count(*) FROM kontrol_src.document') == 0


def test_cpc_budget_checkpoint_keeps_the_catalogue(conn, monkeypatch):
    from ingest import documents, sources
    rows = parse.cpc((F / 'cpc.html').read_bytes(), 'https://official.example/')[0]
    with pipeline.run(conn, 'kontrol', 'cpc') as r:
        store.load(conn, r, 'cpc', [{k: v for k, v in x.items() if k not in ('pdf_event', 'document_event')} for x in rows],
                   manifest(rows, source_count=len(rows)))
        store.build(conn, r)
        before = count(conn, "SELECT content_sha256 FROM ops.load WHERE tool = 'kontrol' AND source = 'cpc' AND is_current")
        monkeypatch.setattr(documents.time, 'monotonic', iter([0, 2]).__next__)

        def crawl(client, save, document_callback):
            document_callback(rows, b'<input name="__VIEWSTATE" value="state"><input name="__EVENTVALIDATION" value="validation">',
                              'https://official.example/')
        monkeypatch.setattr(sources, 'cpc', crawl)
        report = documents.cpc(conn, r, None, max_seconds=1)
    assert report['checkpoint'] and report['pending'] == len(rows) and report['problems'] == []
    assert count(conn, "SELECT content_sha256 FROM ops.load WHERE tool = 'kontrol' AND source = 'cpc' AND is_current") == before


@pytest.fixture
def site(conn, dsn, monkeypatch):
    from app import queries
    from app.main import app
    monkeypatch.setattr(queries, 'DSN', dsn)
    queries._cache.clear()
    return TestClient(app)


def test_pages_read_the_serving_layer_with_its_version(conn, site):
    with pipeline.run(conn, 'kontrol', 'refresh') as r:
        load_all(conn, r)
        assert run.finish(conn, r) == []
    pid = r.stats['publication']
    r0 = adfi()[0]
    for url in ('/', '/oditi', '/preporaki', '/inspekcii', '/kzk', '/sources', '/how', '/legal', '/healthz',
                '/oditi/' + r0['id'], '/export.csv?view=kzk'):
        page = site.get(url)
        assert page.status_code == 200, url
        assert page.headers['X-Publication'] == str(pid), url
    detail = site.get('/oditi/' + r0['id']).text
    assert 'гр. Кнежа' in detail and conn.execute('SELECT sha256 FROM pub_kontrol.record_version WHERE record_id = %s',
                                                    (r0['id'],)).fetchone()[0] in detail
    assert len(site.get('/export.csv?view=kzk').text.splitlines()) == len(cpc()) + 1
    assert 'Сметна палата' in site.get('/sources').text
    # a publication that changes nothing gets no number; the pages keep the one they show
    with pipeline.run(conn, 'kontrol', 'check') as r:
        store.build(conn, r)
        assert pipeline.publish(conn, r, run.ROOT).publication_id is None


def test_the_pages_read_nothing_but_the_serving_layer():
    code = (run.ROOT / 'app' / 'queries.py').read_text(encoding='utf-8') + (run.ROOT / 'app' / 'main.py').read_text(encoding='utf-8')
    schemas = set(re.findall(r'\b(?:FROM|JOIN)\s+([a-z_]+)\.', code))
    assert schemas <= {'pub', 'pub_kontrol'}, schemas
    assert 'ingest import db' not in code and 'live.' not in code


# ---------------------------------------------------------------- the move

def old_database(conn, tmp_path):
    """The old database of Контрол in schemas live, src and old_ops of the test database, as its store wrote it: one
    read of the register of АДФИ, its raw file under its sha256, the version of every record, one document."""
    for sql in sorted((Path(run.ROOT) / 'db' / 'legacy').glob('*.sql')):
        conn.execute(sql.read_text(encoding='utf-8').replace('ops.', 'old_ops.').replace('SCHEMA IF NOT EXISTS ops', 'SCHEMA IF NOT EXISTS old_ops'))
    raw = (F / 'adfi.html').read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    (tmp_path / 'adfi').mkdir()
    path = tmp_path / 'adfi' / (sha + '.bin')
    path.write_bytes(raw)
    url = 'https://www.adfi.minfin.bg/bg/34'
    at = dt.datetime(2026, 10, 2, 1, 45, 41, tzinfo=dt.timezone.utc)
    conn.execute('INSERT INTO old_ops.raw_file VALUES (%s, %s, %s, %s, %s, %s)', ('adfi', url, str(path), sha, len(raw), at - dt.timedelta(seconds=1)))
    from ingest import sources

    class Page:
        def get(self, u):
            return raw
    rows, m = sources.adfi(Page(), 'adfi', lambda *a: None)
    rows = parse.canonical(rows)
    conn.execute('INSERT INTO live.snapshot VALUES (%s, %s, %s, %s)', ('adfi', Jsonb(dict(rows=rows, manifest=m)), 'x', at))
    conn.execute("INSERT INTO src.resource VALUES ('adfi', %s, %s, 'наред', NULL, %s, 1, NULL, %s)", (url, at, len(rows), m['scope']))
    for x in rows:
        conn.execute('INSERT INTO old_ops.version VALUES (%s, %s, %s, %s, %s)', ('adfi', x['id'], store.digest(store.encode(x)), Jsonb(x), at))
    doc = b'%PDF-old-document'
    dsha = hashlib.sha256(doc).hexdigest()
    (tmp_path / 'adfi' / (dsha + '.bin')).write_bytes(doc)
    later = at + dt.timedelta(minutes=5)
    conn.execute('INSERT INTO old_ops.raw_file VALUES (%s, %s, %s, %s, %s, %s)', ('adfi', rows[0]['url'], str(tmp_path / 'adfi' / (dsha + '.bin')), dsha, len(doc), later))
    payload = dict(url=rows[0]['url'], document_sha256=dsha, text_available=False, excerpt=None, excerpt_page=None,
                   text_status='само документ, няма надежден кратък откъс')
    conn.execute('INSERT INTO live.document VALUES (%s, %s, %s)', (rows[0]['id'], Jsonb(payload), later))
    conn.execute('INSERT INTO old_ops.document_version VALUES (%s, %s, %s, %s, %s)', (rows[0]['id'], dsha, rows[0]['url'], Jsonb(payload), later))
    return rows


def test_replay_and_parity_against_the_old_database(conn, dsn, tmp_path, monkeypatch):
    from ingest import move, pdf
    rows = old_database(conn, tmp_path)
    monkeypatch.setattr(pdf, 'title_excerpt', lambda data: dict(document_sha256=store.digest(data), text_available=False, excerpt=None,
                                                                 excerpt_page=None, text_status='само документ, няма надежден кратък откъс'))
    with pipeline.run(conn, 'kontrol', 'replay') as r:
        assert move.replay(conn, r, dsn, ops='old_ops') == []
        assert run.finish(conn, r) == []
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'kontrol' AND status = 'loaded'") == 2
    with pipeline.run(conn, 'kontrol', 'parity') as r:
        move.parity(conn, r, dsn, ops='old_ops')
    got = dict(conn.execute("SELECT name, actual FROM ops.check_latest WHERE tool = 'kontrol' AND kind = 'parity'").fetchall())
    want = dict(conn.execute("SELECT name, expected FROM ops.check_latest WHERE tool = 'kontrol' AND kind = 'parity'").fetchall())
    assert got['parity_rows_differ'] == 0 and got['parity_records_differ'] == 0
    for k in ('parity_rows', 'parity_records', 'parity_documents', 'parity_versions', 'parity_document_versions', 'parity_sources'):
        assert got[k] == want[k] and got[k] > 0, k
    # one field changed in the serving layer is one record that differs
    conn.execute('UPDATE pub_kontrol.adfi_report SET basis = basis || \'.\' WHERE record_id = %s', (rows[1]['id'],))
    with pipeline.run(conn, 'kontrol', 'parity') as r:
        move.parity(conn, r, dsn, ops='old_ops')
    assert count(conn, "SELECT actual FROM ops.check_latest WHERE tool = 'kontrol' AND name = 'parity_records_differ'") == 1
