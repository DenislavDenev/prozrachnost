"""The landing zone, the clean tables and the serving layer, against a real Postgres 17 (SOBSTVENOST_TEST_DSN)."""
import datetime as dt
import shutil

import pytest

from ingest import parse, pipeline, run, store

from . import seed

T = dt.datetime(2026, 10, 2, 1, 6, 40, tzinfo=dt.timezone.utc)


def count(conn, sql, *params):
    return conn.execute(sql, params or None).fetchone()[0]


def test_catalogue_unchanged_changes_and_versions(conn):
    rows, manifest = seed.enterprises()
    with pipeline.run(conn, 'sobstvenost', 'appk') as r:
        assert store.load_catalogue(conn, r, 'appk', 'catalogue', rows, manifest).status == 'loaded'
        n = count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'sobstvenost'")
        assert store.load_catalogue(conn, r, 'appk', 'catalogue', rows, manifest).status == 'unchanged'
        assert count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'sobstvenost'") == n
        changed = [dict(x) for x in rows]
        changed[0]['name'] += ' (ново име)'
        assert store.load_catalogue(conn, r, 'appk', 'catalogue', changed, manifest).status == 'loaded'
        assert count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'sobstvenost' AND cause = 'rewritten'") == 1
        same, total = store.build(conn, r)
    # every landing row rebuilds the record the parser gave, to the same sha256 the old database versioned it by
    assert same == total == len(rows)
    assert [x for x in store.catalogue(conn, 'appk')] == sorted(changed, key=lambda x: x['id'])
    assert count(conn, "SELECT count(*) FROM sobstvenost.enterprise WHERE name LIKE '%(ново име)'") == 1
    # the published percentage is kept as written, and as a share from 0 to 1 beside it
    assert conn.execute("SELECT share_pct::text, share FROM sobstvenost.enterprise WHERE appk_id = '105'").fetchone() == ('100', 1)


def test_a_shorter_read_waits_a_day(conn):
    rows, manifest = seed.enterprises()
    with pipeline.run(conn, 'sobstvenost', 'appk') as r:
        store.load_catalogue(conn, r, 'appk', 'catalogue', rows, manifest, now=T)
        shorter = rows[1:]
        assert store.load_catalogue(conn, r, 'appk', 'catalogue', shorter, manifest, now=T + dt.timedelta(hours=1)).status == 'held'
        assert store.load_catalogue(conn, r, 'appk', 'catalogue', shorter, manifest, now=T + dt.timedelta(hours=2)).status == 'held'
        assert len(store.catalogue(conn, 'appk')) == len(rows)
        assert store.load_catalogue(conn, r, 'appk', 'catalogue', shorter, manifest, now=T + dt.timedelta(days=2)).status == 'confirmed'
    assert len(store.catalogue(conn, 'appk')) == len(rows) - 1


def test_an_incomplete_or_unknown_read_writes_nothing(conn):
    rows, manifest = seed.enterprises()
    with pipeline.run(conn, 'sobstvenost', 'appk') as r:
        with pytest.raises(parse.ShapeError):
            store.load_catalogue(conn, r, 'appk', 'catalogue', rows + rows[:1], manifest)
        odd = [dict(x) for x in rows]
        odd[0]['unexpected'] = 'x'
        with pytest.raises(parse.ShapeError, match='Непознато'):
            store.load_catalogue(conn, r, 'appk', 'catalogue', odd, manifest)
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'sobstvenost'") == 0
    with pytest.raises(parse.ShapeError):
        store.reconcile(rows, len(rows) + 1)


def enterprise(conn, r, change=None, now=None):
    pages = seed.answers()
    profile, reports = run.read_enterprise(seed.fetch_from(pages), dict(id='105', source_url=seed.PROFILE))
    if change:
        change(profile, reports)
    return store.load_enterprise(conn, r, profile, reports, now=now)


def test_board_loss_is_held_and_people_are_not_merged(conn):
    with pipeline.run(conn, 'sobstvenost', 'appk-details') as r:
        assert enterprise(conn, r, now=T).status == 'loaded'

        def other(profile, reports):     # the same people in another enterprise: two boards, never one person
            profile['id'] = '51'
            for x in reports:
                x['appk_id'] = '51'
        assert enterprise(conn, r, other, now=T).status == 'loaded'

        def empty(profile, reports):
            profile['board'] = []
        assert enterprise(conn, r, empty, now=T + dt.timedelta(hours=1)).status == 'held'
        assert enterprise(conn, r, empty, now=T + dt.timedelta(days=2)).status == 'confirmed'
        store.build(conn, r)
    assert count(conn, "SELECT count(*) FROM sobstvenost.profile_version WHERE appk_id = '105'") == 2
    assert count(conn, "SELECT count(*) FROM sobstvenost.board_entry WHERE appk_id = '51'") == 5
    assert count(conn, "SELECT count(DISTINCT appk_id) FROM sobstvenost.board_entry WHERE member_name = 'Иван Атанасов Кунев'") == 2


def test_an_identity_that_becomes_empty_is_held(conn):
    with pipeline.run(conn, 'sobstvenost', 'appk-details') as r:
        enterprise(conn, r, now=T)

        def no_eik(profile, reports):
            profile['eik'] = None
        assert enterprise(conn, r, no_eik, now=T + dt.timedelta(hours=1)).status == 'held'
    assert count(conn, "SELECT eik FROM sobstvenost_src.profile p JOIN ops.load l USING (load_id) WHERE l.is_current") == '831646048'


def test_a_share_that_disappears_is_held():
    old = {'105': dict(id='105', share_pct='100', share_text='100% Държавна собственост', participation='direct')}
    assert store.lost(old, {'105': dict(id='105', share_pct=None, share_text='—', participation='unknown')}) == 1
    assert store.lost(old, {'105': dict(old['105'], name='ново')}) == 0


def test_a_revised_report_keeps_its_history(conn):
    with pipeline.run(conn, 'sobstvenost', 'appk-details') as r:
        enterprise(conn, r, now=T)

        def revised(profile, reports):
            reports[0]['documents'][0]['title'] = 'Преработен документ'
        assert enterprise(conn, r, revised, now=T + dt.timedelta(hours=1)).status == 'held'
        assert enterprise(conn, r, revised, now=T + dt.timedelta(days=2)).status == 'confirmed'
        store.build(conn, r)
    first = conn.execute("SELECT record_id FROM sobstvenost.report WHERE appk_id = '105' ORDER BY record_id LIMIT 1").fetchone()[0]
    assert count(conn, 'SELECT count(*) FROM sobstvenost.report_version WHERE record_id = %s', first) == 2
    assert count(conn, "SELECT count(*) FROM sobstvenost.report WHERE appk_id = '105'") == 16


def test_build_publishes_and_hides_a_person_concessionaire(conn):
    pages = seed.answers()
    pages[seed.NOTICE] = pages[seed.NOTICE].replace('"КАРИЕРИ ПАЗАРДЖИК" ООД'.encode(), 'ЕТ "Мария Димитрова"'.encode())
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        seed.load_all(conn, r, pages)
        assert run.finish(conn, r) == []
    assert r.stats['publication']
    assert conn.execute('SELECT concessionaire_name, concessionaire_is_person FROM sobstvenost.notice').fetchone() == ('ЕТ "Мария Димитрова"', True)
    assert conn.execute('SELECT concessionaire_name FROM pub_sobstvenost.notice').fetchone()[0].startswith('Физическо лице')
    assert count(conn, 'SELECT count(*) FROM pub_sobstvenost.board_entry') == 5
    assert count(conn, 'SELECT count(*) FROM pub_sobstvenost.report_document') == count(conn, 'SELECT count(*) FROM sobstvenost.report_document') > 0
    assert count(conn, "SELECT count(*) FROM core.serving_violation WHERE relation LIKE 'pub_sobstvenost.%%'") == 0
    # a publication that changes nothing gets no number
    problems, again = seed.published(conn)
    assert problems == [] and again.stats['publication'] is None


def test_a_person_unmasked_in_the_serving_layer_stops_the_publication(conn, tmp_path):
    pages = seed.answers()
    pages[seed.NOTICE] = pages[seed.NOTICE].replace('"КАРИЕРИ ПАЗАРДЖИК" ООД'.encode(), 'ЗП МЕТОДИ БОРИСОВ ХРИСТОВ'.encode())
    root = tmp_path / 'tool'
    shutil.copytree(run.ROOT / 'db', root / 'db')
    sql = root / 'db' / 'publish' / '12_pub_sobstvenost.notice.sql'
    sql.write_text(sql.read_text(encoding='utf-8').replace('WHEN concessionaire_is_person', 'WHEN false'), encoding='utf-8')
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        seed.load_all(conn, r, pages)
        store.build(conn, r)
        refused = pipeline.publish(conn, r, root)
    assert refused.publication_id is None and any('не е юридическо лице' in p for p in refused.problems)
    assert count(conn, 'SELECT count(*) FROM pub_sobstvenost.notice') == 0


def test_an_eik_not_in_core_stays_unlinked_and_is_counted(conn):
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        seed.load_all(conn, r)
        store.build(conn, r)
        pipeline.run_checks(conn, r, ['unmatched'])
    assert conn.execute("SELECT eik, org_eik FROM sobstvenost.profile WHERE appk_id = '105'").fetchone() == ('831646048', None)
    assert conn.execute("SELECT actual FROM ops.check_latest WHERE tool = 'sobstvenost' AND name = 'org_unmatched'").fetchone()[0] == 2


def test_a_pass_with_a_refused_enterprise_is_not_complete(conn, tmp_path):
    rows, manifest = seed.enterprises()
    pages = seed.answers()
    broken = next(x for x in rows if x['id'] == '51')
    pages[broken['source_url']] = b'<html><head><title>Error</title></head></html>'
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        store.load_catalogue(conn, r, 'appk', 'catalogue', [x for x in rows if x['id'] in ('51', '105')], dict(manifest, source_count=2))
        result = run.details(conn, r, seed.fetch_from(pages), 'appk')
    assert result['refused'] == ['51'] and result['completed'] == 2
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'sobstvenost' AND source = 'appk-full'") == 0
    assert any('appk-details 51: невалиден отговор' in p for p in run.freshness(conn))
    # the next pass reads it well: complete, and the freshness of the profiles is clean
    pages[broken['source_url']] = pages[seed.PROFILE]
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        result = run.details(conn, r, seed.fetch_from(pages), 'appk')
    assert 'refused' not in result
    assert conn.execute("SELECT status, rows FROM sobstvenost.source_state WHERE source = 'appk' AND scope = 'details'").fetchone() == ('ok', 2)
    assert not [p for p in run.freshness(conn) if 'АППК, пълен прочит' in p or 'appk-details' in p]


def test_resume_uses_a_kept_answer_and_refuses_a_changed_file(conn):
    class Client:
        deadline = None
        calls = 0

        def get(self, url, data=None):
            self.calls += 1
            return b'original'
    client = Client()
    with pipeline.run(conn, 'sobstvenost', 'appk') as r:
        fetch = run.fetcher(conn, r, client)
        assert fetch('appk', 'https://example.test/x') == b'original'
        again = run.fetcher(conn, r, client, resume=True)
        assert again('appk', 'https://example.test/x') == b'original' and client.calls == 1
        path = conn.execute("SELECT path FROM ops.raw_file WHERE sha256 = encode(sha256('original'::bytea), 'hex')").fetchone()[0]
        open(path, 'wb').write(b'changed!')
        with pytest.raises(pipeline.CorruptRaw):
            again('appk', 'https://example.test/x')


def test_the_pages_read_nothing_but_the_serving_layer():
    import re
    code = (run.ROOT / 'app' / 'queries.py').read_text(encoding='utf-8') + (run.ROOT / 'app' / 'main.py').read_text(encoding='utf-8')
    schemas = set(re.findall(r'\b(?:FROM|JOIN)\s+([a-z_]+)\.', code))
    assert schemas <= {'pub', 'pub_sobstvenost'}, schemas
    assert 'ingest import db' not in code and 'live.' not in code and 'ops.' not in code
