import csv
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

import pipeline
from ingest import ekatte, run
from pipeline import ShapeError

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'tests' / 'fixtures' / 'ekatte.zip'   # the real archive of НСИ, downloaded on 29.09.2026


def one(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def rezip(change):
    """The real archive with one file changed by change(name, rows) -> rows."""
    out = io.BytesIO()
    with zipfile.ZipFile(FIXTURE) as z, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as w:
        for name in z.namelist():
            data = z.read(name)
            if name in ekatte.FILES:
                data = json.dumps(change(name, json.loads(data)), ensure_ascii=False).encode()
            w.writestr(name, data)
    return out.getvalue()


@pytest.fixture
def archive(tmp_path):
    (tmp_path / 'ekatte').mkdir()
    shutil.copy(FIXTURE, tmp_path / 'ekatte' / '2026-09-29.002862bf0655.zip')
    return tmp_path


def step(conn, archive, tmp_path):
    with pipeline.run(conn, 'danni', 'place') as r:
        return run.place(conn, r, archive=archive, raw_dir=tmp_path / 'raw')


def test_real_archive_counts_and_three_rows():
    # checked against the files inside the archive of 29.09.2026
    d = ekatte.parse(FIXTURE.read_bytes())
    assert {k: len(v) for k, v in d.items()} == {'ek_reg1.json': 2, 'ek_reg2.json': 6, 'ek_obl.json': 28,
                                                 'ek_obst.json': 265, 'ek_atte.json': 5256}
    assert d['ek_atte.json']['02676'] == dict(ekatte='02676', t_v_m='гр.', name='Банско', name_en='Bansko', oblast='BLG',
                                              obshtina='BLG01', kmetstvo='BLG01-00', kind=1, category=3,
                                              altitude=d['ek_atte.json']['02676']['altitude'],
                                              document=d['ek_atte.json']['02676']['document'], nuts3='BG413')
    assert d['ek_atte.json']['00014']['obshtina'] == 'BLG52' and d['ek_atte.json']['00014']['t_v_m'] == 'с.'
    assert d['ek_obst.json']['SOF46'] == dict(obshtina='SOF46', ekatte='68134', name='Столична', name_en='Stolichna',
                                              nuts3='BG411', category=0)


@pytest.mark.parametrize('raw', [
    b'', b'<html>error</html>', FIXTURE.read_bytes()[:20000],
    rezip(lambda n, rows: rows[:100] if n == 'ek_atte.json' else rows),                                   # truncated list
    rezip(lambda n, rows: [{**rows[0], 'new_field': 1}] + rows[1:] if n == 'ek_obst.json' else rows),      # unknown field
    rezip(lambda n, rows: [{k: v for k, v in rows[0].items() if k != 'obshtina'}] + rows[1:] if n == 'ek_atte.json' else rows),
    rezip(lambda n, rows: [rows[0]] + rows if n == 'ek_atte.json' else rows),                              # duplicate key
    rezip(lambda n, rows: [{**rows[0], 'obshtina': 'XXX99'}] + rows[1:] if n == 'ek_atte.json' else rows),  # dangling code
    rezip(lambda n, rows: [{**rows[0], 'category': '3'}] + rows[1:] if n == 'ek_atte.json' else rows),     # wrong type
], ids=['empty', 'html', 'cut-zip', 'truncated', 'unknown-field', 'missing-field', 'duplicate', 'dangling', 'type'])
def test_invalid_archive_is_refused(raw):
    with pytest.raises(ShapeError):
        ekatte.parse(raw)


def test_reference_has_every_municipality_once_with_a_valid_eik():
    ref = run.municipality_ref((ROOT / 'db' / 'ref' / 'municipality.csv').read_bytes())
    assert len(ref) == 265 == len({r['eik'] for r in ref.values()})
    assert ref['BLG01']['eik'] == '000024663' and ref['DOB15']['name_bg'] == 'Добричка'
    assert set(ref) == set(ekatte.parse(FIXTURE.read_bytes())['ek_obst.json'])
    bad = (ROOT / 'db' / 'ref' / 'municipality.csv').read_text(encoding='utf-8').replace('000024663', '000024664')
    with pytest.raises(ShapeError):
        run.municipality_ref(bad.encode())


def test_eik_rule_is_the_same_in_python_and_sql(conn):
    eiks = [r['eik'] for r in csv.DictReader((ROOT / 'db' / 'ref' / 'municipality.csv').open(encoding='utf-8'))]
    assert all(run.eik_valid(e) for e in eiks)   # 265 real ones
    samples = eiks + [e[:8] + str((int(e[8]) + 1) % 10) for e in eiks[:50]] + [
        '', '0', '000000000', '12345678', '1234567890', 'abcdefghi', '1213334440', '٠٠٠٠٢٤٦٦٣',
        '0000246630016', '0000246630010', '1754064260019', '1311296980001']
    samples += [e + f'{i:04d}' for e in eiks[:3] for i in range(0, 200, 7)]   # 13 digits, most with a wrong tail
    got = conn.execute('SELECT e, core.eik_valid(e) FROM unnest(%s::text[]) e', (samples,)).fetchall()
    assert [(e, run.eik_valid(e)) for e in samples] == got
    assert sum(ok for _, ok in got) > 265 and sum(not ok for _, ok in got) > 60


def test_place_builds_core_and_reconciles(conn, archive, tmp_path):
    assert set(step(conn, archive, tmp_path).values()) == {'loaded'}
    counts = [one(conn, f'SELECT count(*) FROM core.{t}') for t in ('nuts', 'oblast', 'municipality', 'settlement')]
    assert counts == [37, 28, 265, 5256]
    assert conn.execute("SELECT level, count(*) FROM core.nuts GROUP BY 1 ORDER BY 1").fetchall() == [(0, 1), (1, 2), (2, 6), (3, 28)]
    assert conn.execute('''SELECT s.kind_prefix, s.name_bg, m.name_bg, m.eik, o.name_bg, n3.nuts_code, n2.name_bg, n1.nuts_code
        FROM core.settlement s JOIN core.municipality m USING (municipality_code) JOIN core.oblast o USING (oblast_code)
        JOIN core.nuts n3 ON n3.nuts_code = o.nuts3_code JOIN core.nuts n2 ON n2.nuts_code = n3.parent_code
        JOIN core.nuts n1 ON n1.nuts_code = n2.parent_code WHERE s.ekatte = '02676' ''').fetchone() == (
        'гр.', 'Банско', 'Банско', '000024663', 'Благоевград', 'BG413', 'Югозападен', 'BG4')
    assert one(conn, "SELECT center_ekatte FROM core.municipality WHERE municipality_code = 'SOF46'") == '68134'
    assert conn.execute("SELECT nuts_code, label_bg FROM core.nuts WHERE level = 1 OR nuts_code IN ('BG412', 'BG31') ORDER BY 1").fetchall() == [
        ('BG3', 'Северна и Югоизточна България'), ('BG31', 'Северозападен'), ('BG4', 'Югозападна и Южна централна България'),
        ('BG412', 'София област')]
    assert conn.execute("SELECT oblast_code, name_bg, label_bg FROM core.oblast WHERE name_bg <> label_bg OR oblast_code = 'SOF' ORDER BY 1").fetchall() == [
        ('SFO', 'София', 'София област'), ('SOF', 'София (столица)', 'София (столица)')]
    with pipeline.run(conn, 'danni', 'check') as r:
        assert pipeline.run_checks(conn, r) == []
    # four municipalities have their seat in a town outside their own territory; that is the source, not an error
    assert [x[0] for x in conn.execute('''SELECT m.name_bg FROM core.municipality m JOIN core.settlement s ON s.ekatte = m.center_ekatte
        WHERE s.municipality_code <> m.municipality_code ORDER BY 1''')] == ['Добричка', 'Марица', 'Родопи', 'Тунджа']
    # the same archive under a new name (НСИ changes the bytes every day): nothing is written
    shutil.copy(FIXTURE, archive / 'ekatte' / '2026-09-30.aaaaaaaaaaaa.zip')
    log = one(conn, 'SELECT count(*) FROM ops.change_log')
    assert set(step(conn, archive, tmp_path).values()) == {'unchanged'}
    assert one(conn, 'SELECT count(*) FROM ops.change_log') == log
    assert one(conn, 'SELECT count(*) FROM danni_src.ekatte_settlement') == 5256
    assert one(conn, 'SELECT count(*) FROM ops.raw_file') == 2   # the archive and the reference, each once


def test_a_closed_settlement_is_held_then_kept_with_gone_at(conn, archive, tmp_path):
    step(conn, archive, tmp_path)
    smaller = rezip(lambda n, rows: [r for r in rows if r.get('ekatte') != '00014'] if n == 'ek_atte.json' else rows)
    (archive / 'ekatte' / '2026-10-01.bbbbbbbbbbbb.zip').write_bytes(smaller)
    assert step(conn, archive, tmp_path)['ek_atte.json'] == 'held'
    assert one(conn, "SELECT gone_at FROM core.settlement WHERE ekatte = '00014'") is None
    conn.execute("UPDATE ops.hold SET first_at = first_at - interval '2 days'")
    assert step(conn, archive, tmp_path)['ek_atte.json'] == 'confirmed'
    assert one(conn, "SELECT gone_at IS NOT NULL FROM core.settlement WHERE ekatte = '00014'") is True
    assert one(conn, 'SELECT count(*) FROM core.settlement') == 5256   # nothing is deleted
    assert one(conn, "SELECT cause FROM ops.change_log WHERE field = '00014'") == 'removed'
    # a renamed settlement: the field is logged and core follows
    renamed = rezip(lambda n, rows: [{**r, 'name': 'Ново име'} if r.get('ekatte') == '02676' else r for r in rows
                                     if r.get('ekatte') != '00014'] if n == 'ek_atte.json' else rows)
    (archive / 'ekatte' / '2026-10-05.cccccccccccc.zip').write_bytes(renamed)
    assert step(conn, archive, tmp_path)['ek_atte.json'] == 'loaded'
    assert one(conn, "SELECT name_bg FROM core.settlement WHERE ekatte = '02676'") == 'Ново име'
    assert conn.execute("SELECT old, new, cause FROM ops.change_log WHERE field = '02676/name'").fetchone() == ('Банско', 'Ново име', 'rewritten')


def test_a_new_municipality_without_eik_stops_the_step(conn, archive, tmp_path):
    step(conn, archive, tmp_path)
    extra = rezip(lambda n, rows: rows[:1] + [{**rows[0], 'obshtina': 'BLG99'}] + rows[1:] if n == 'ek_obst.json' else rows)
    (archive / 'ekatte' / '2026-10-02.dddddddddddd.zip').write_bytes(extra)
    with pytest.raises(ShapeError, match='BLG99'):
        step(conn, archive, tmp_path)
    assert one(conn, 'SELECT count(*) FROM core.municipality') == 265
    assert one(conn, "SELECT status FROM ops.run WHERE tool = 'danni' ORDER BY run_id DESC LIMIT 1") == 'failed'


def test_org_from_tender_keeps_only_valid_eik(conn, dsn):
    conn.execute('CREATE SCHEMA tender')
    conn.execute('''CREATE TABLE tender.company (key text, eik text, name text, legal_form text, seat text, registered_on date,
        tr_status text, is_public_body boolean)''')
    conn.execute('CREATE TABLE tender.buyer (eik text, eik_valid boolean, name text)')
    conn.execute('''INSERT INTO tender.company VALUES
        ('eik:000024663', '000024663', 'Община Банско', NULL, 'гр. Банско', NULL, 'absent', true),
        ('eik:000093442', '000093442', ' Фирма ООД ', 'OOD', 'гр. Варна', '2008-06-27', 'ok', false),
        ('eik:000056814', '000056814', 'ЕТ Иван Иванов', 'ET', NULL, NULL, 'ok', false),
        ('eik:000000000', '000000000', 'Невалиден', NULL, NULL, NULL, 'absent', false),
        ('eik:000024664', '000024664', 'Грешна контролна цифра', 'OOD', NULL, NULL, 'ok', false),
        ('eik:000565537', '000565537', '  ', 'OOD', NULL, NULL, 'ok', false),
        ('name:x', NULL, 'Без ЕИК', NULL, NULL, NULL, NULL, false)''')
    conn.execute('''INSERT INTO tender.buyer VALUES ('000024663', true, 'Община Банско'), ('000852932', true, 'Община Добрич'),
        ('123', false, 'Счупен')''')

    def step():
        with pipeline.run(conn, 'danni', 'org') as r:
            return run.org(conn, r, tender_dsn=dsn), r.stats
    status, stats = step()
    assert status == 'loaded' and stats['orgs'] == stats['in_core'] == 4 and stats['invalid'] == 2 and stats['unnamed'] == 1
    assert conn.execute('SELECT eik, name, is_buyer, is_public_body, is_person_named, source FROM core.org ORDER BY eik').fetchall() == [
        ('000024663', 'Община Банско', True, True, False, 'eop'),
        ('000056814', 'ЕТ Иван Иванов', False, False, True, 'tr'),
        ('000093442', 'Фирма ООД', False, False, False, 'tr'),
        ('000852932', 'Община Добрич', True, False, False, 'eop')]
    checks = dict(conn.execute("SELECT name, ok FROM ops.check_latest WHERE tool = 'danni' AND name LIKE 'org%'").fetchall())
    assert checks == {'org_count_matches_tender': True, 'org_invalid_eik': False, 'org_buyer_valid_matches_tender': True}
    assert step()[0] == 'unchanged'
    # a renamed company is updated in place; one that left is held first, then kept with gone_at
    conn.execute("UPDATE tender.company SET name = 'Фирма АД' WHERE eik = '000093442'")
    assert step()[0] == 'loaded' and one(conn, "SELECT name FROM core.org WHERE eik = '000093442'") == 'Фирма АД'
    assert one(conn, 'SELECT count(*) FROM danni_src.org_tender') == 4   # landing policy latest: one version
    conn.execute("DELETE FROM tender.company WHERE eik = '000056814'")
    assert step()[0] == 'held' and one(conn, "SELECT gone_at FROM core.org WHERE eik = '000056814'") is None
    conn.execute("UPDATE ops.hold SET first_at = first_at - interval '2 days'")
    assert step()[0] == 'confirmed'
    assert one(conn, "SELECT gone_at IS NOT NULL FROM core.org WHERE eik = '000056814'") is True
    assert one(conn, 'SELECT count(*) FROM core.org') == 4
    with pytest.raises(Exception):   # the constraint of core.org refuses an ЕИК with a wrong control digit
        conn.execute("INSERT INTO core.org (eik, name, source) VALUES ('000024664', 'x', 'tr')")


def test_licence_list_is_the_only_switch(conn, monkeypatch, tmp_path):
    # every entry of the list is applied; counted from the file, which grows with every tool that moves in
    import tomllib
    entries = tomllib.loads((Path(run.__file__).resolve().parent.parent / 'db' / 'licences.toml').read_text(encoding='utf-8'))['licence']
    assert run.licences(conn) == len(entries) >= 13
    assert conn.execute('SELECT dataset, is_distributable FROM core.dataset WHERE is_displayable ORDER BY 1').fetchall() == [
        ('danni.catalog', True), ('danni.ekatte', False), ('danni.municipality', False)]   # the datasets of naselenie and partii are not registered here
    conn.execute("UPDATE core.dataset SET is_distributable = true, licence_checked_on = '2026-01-01' WHERE dataset = 'danni.ekatte'")
    conn.execute("UPDATE core.dataset SET is_displayable = true WHERE dataset = 'danni.org'")
    run.licences(conn)   # not in the list: closed again
    assert one(conn, "SELECT is_distributable FROM core.dataset WHERE dataset = 'danni.ekatte'") is False
    assert one(conn, "SELECT is_displayable FROM core.dataset WHERE dataset = 'danni.org'") is False
    with pytest.raises(Exception):   # what is handed out is also shown
        conn.execute("UPDATE core.dataset SET is_distributable = true, is_displayable = false, licence_checked_on = '2026-01-01' WHERE dataset = 'danni.ekatte'")
    (tmp_path / 'db').mkdir()
    (tmp_path / 'db' / 'licences.toml').write_text('[[licence]]\ndataset = "danni.org"\nchecked_on = 2026-11-10\ndisplay = true\n', encoding='utf-8')
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    with pytest.raises(RuntimeError, match='names'):   # a dataset with names of persons cannot even be shown
        run.licences(conn)


def publish(conn, step='check'):
    with pipeline.run(conn, 'danni', step) as r:
        return pipeline.publish(conn, r, ROOT)


def test_places_are_published_whole_with_a_version_or_not_at_all(conn, archive, tmp_path):
    assert publish(conn).publication_id == 1   # the catalogue of the datasets alone
    step(conn, archive, tmp_path)
    first = publish(conn)
    assert first.problems == [] and first.publication_id == 2
    assert first.changes['pub.settlement'] == {'added': 5256, 'removed': 0} and first.changes['pub.municipality']['added'] == 265
    assert conn.execute('''SELECT s.name_bg, m.eik, o.label_bg, n.parent_code FROM pub.settlement s JOIN pub.municipality m USING (municipality_code)
        JOIN pub.oblast o USING (oblast_code) JOIN pub.nuts n ON n.nuts_code = o.nuts3_code WHERE s.ekatte = '02676' ''').fetchone() == (
        'Банско', '000024663', 'Благоевград', 'BG41')
    again = publish(conn)
    assert again == pipeline.Published(None, {}, [])   # nothing changed: no new version, the cache key stays
    assert one(conn, 'SELECT max(publication_id) FROM pub.publication') == 2
    # one renamed settlement: one row out, one row in, a new version
    conn.execute("UPDATE core.settlement SET name_bg = 'Ново име' WHERE ekatte = '02676'")
    third = publish(conn)
    assert third.publication_id == 3 and third.changes == {'pub.settlement': {'added': 1, 'removed': 1}}
    assert conn.execute('SELECT tool, changes FROM api_v1.publication ORDER BY publication_id DESC LIMIT 1').fetchone() == ('danni', third.changes)
    # a rule of kind publish fails against the new content: the whole publication rolls back, the evidence stays
    conn.execute("UPDATE core.settlement SET name_bg = 'Трето име' WHERE ekatte = '02676'")
    conn.execute("UPDATE ops.check_rule SET sql = 'SELECT 1, count(*) FROM pub.settlement' WHERE name = 'published_places_match_core'")
    refused = publish(conn)
    assert refused.publication_id is None and refused.changes == {} and 'очаквано 1, получено 5256' in refused.problems[0]
    assert one(conn, "SELECT name_bg FROM pub.settlement WHERE ekatte = '02676'") == 'Ново име'
    assert one(conn, 'SELECT max(publication_id) FROM pub.publication') == 3
    assert conn.execute("SELECT ok FROM ops.check_latest WHERE tool = 'danni' AND name = 'published_places_match_core'").fetchone() == (False,)
    assert any('Публикуваните общини' in x for x in pipeline.freshness(conn, 'danni'))


def test_a_reader_sees_the_old_version_or_the_new_one_never_a_mix(conn, dsn, archive, tmp_path):
    import psycopg
    step(conn, archive, tmp_path)
    publish(conn)
    with psycopg.connect(dsn) as reader:
        reader.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        reader.read_only = True
        before = reader.execute('SELECT max(publication_id) FROM pub.publication').fetchone()[0]
        conn.execute("UPDATE core.settlement SET name_bg = 'Ново име' WHERE ekatte = '02676'")
        conn.execute("UPDATE core.oblast SET label_bg = 'Друго' WHERE oblast_code = 'BLG'")
        after = publish(conn).publication_id
        assert after == before + 1
        # the reader started before the publication: every later statement of its request still reads that version
        assert reader.execute("SELECT name_bg FROM pub.settlement WHERE ekatte = '02676'").fetchone()[0] == 'Банско'
        assert reader.execute("SELECT label_bg FROM pub.oblast WHERE oblast_code = 'BLG'").fetchone()[0] == 'Благоевград'
        assert reader.execute('SELECT max(publication_id) FROM pub.publication').fetchone()[0] == before
    with psycopg.connect(dsn) as reader:
        assert reader.execute("SELECT s.name_bg, (SELECT max(publication_id) FROM pub.publication) FROM pub.settlement s WHERE ekatte = '02676'").fetchone() == ('Ново име', after)


def test_serving_layer_cannot_reach_outside_or_hold_what_may_not_be_shown(conn, archive, tmp_path):
    step(conn, archive, tmp_path)
    assert publish(conn).problems == []
    assert one(conn, 'SELECT count(*) FROM core.serving_violation') == 0
    conn.execute('CREATE SCHEMA pub_demo')
    cases = [
        ('CREATE TABLE pub_demo.loose (x int PRIMARY KEY)', 'pub_demo.loose', 'таблицата не е записана към набор'),
        ("CREATE TABLE pub_demo.org (eik text PRIMARY KEY); INSERT INTO core.dataset_table VALUES ('pub_demo', 'org', 'danni.org')",
         'pub_demo.org', 'наборът съдържа имена на физически лица'),
        ("CREATE TABLE pub_demo.fk (m text PRIMARY KEY REFERENCES core.municipality); INSERT INTO core.dataset_table VALUES ('pub_demo', 'fk', 'danni.ekatte')",
         'pub_demo.fk', 'външен ключ извън слоя за показване: core.municipality'),
        ('CREATE VIEW pub_demo.peek AS SELECT ekatte FROM core.settlement', 'pub_demo.peek', 'изгледът чете извън слоя за показване: core.settlement'),
        ('CREATE VIEW api_v1.peek AS SELECT load_id FROM ops.load', 'api_v1.peek', 'изгледът чете извън слоя за показване: ops.load'),
    ]
    for sql, relation, reason in cases:
        conn.execute(sql)
        assert conn.execute('SELECT relation, reason FROM core.serving_violation').fetchall() == [(relation, reason)], sql
        conn.execute("UPDATE core.settlement SET name_en = name_en || 'x' WHERE ekatte = '02676'")
        refused = publish(conn)   # nothing is published while the layer is broken, whoever broke it
        assert refused.publication_id is None and refused.problems == [f'{relation}: {reason}']
        conn.execute(f"DROP {'VIEW' if 'VIEW' in sql else 'TABLE'} {relation}; DELETE FROM core.dataset_table WHERE schema_name = 'pub_demo'")
    # a view inside the layer over the layer is fine; a dataset that lost its right to be shown is not
    conn.execute('CREATE VIEW pub_demo.ok AS SELECT ekatte FROM pub.settlement')
    assert one(conn, 'SELECT count(*) FROM core.serving_violation') == 0
    conn.execute("UPDATE core.dataset SET is_displayable = false WHERE dataset = 'danni.ekatte'")
    assert sorted(x[0] for x in conn.execute('SELECT relation FROM core.serving_violation')) == ['pub.nuts', 'pub.oblast', 'pub.settlement']
    with pipeline.run(conn, 'danni', 'check') as r:
        assert len(pipeline.run_checks(conn, r, kinds=['serving'])) == 1


def test_names_are_masked_to_the_first_name_and_the_type_refuses_anything_else(conn):
    q = 'SELECT core.mask_name(%s)::text'
    assert [one(conn, q, x) for x in ('Иван Петров Георгиев', '  Мария   Иванова ', 'Иван', '', None, 'Jean-Paul Belmondo')] == [
        'Иван ***', 'Мария ***', '***', None, None, 'Jean-Paul ***']
    conn.execute('CREATE SCHEMA pub_demo; CREATE TABLE pub_demo.donor (id int PRIMARY KEY, donor_name pub.masked_name)')
    conn.execute("INSERT INTO pub_demo.donor VALUES (1, core.mask_name('Иван Петров')), (2, NULL)")
    for full in ('Иван Петров', 'Иван П.', 'Иван *** Петров', '*** Петров'):
        with pytest.raises(Exception):   # the column cannot hold a name that is not masked, whoever writes it
            conn.execute('INSERT INTO pub_demo.donor VALUES (3, %s)', (full,))
    assert one(conn, 'SELECT count(*) FROM pub_demo.donor') == 2


def test_no_name_of_a_person_reaches_the_serving_layer_without_a_decision(conn, archive, tmp_path):
    step(conn, archive, tmp_path)
    assert publish(conn).problems == []
    conn.execute("CREATE SCHEMA pub_demo; CREATE SCHEMA demo; INSERT INTO ops.source (tool, source, title_bg, channel) VALUES ('demo', 's', 'С', 'file')")

    def table(columns):
        conn.execute('DROP TABLE IF EXISTS pub_demo.t')
        conn.execute(f'CREATE TABLE pub_demo.t (id int PRIMARY KEY, {columns})')
        conn.execute("INSERT INTO core.dataset_table VALUES ('pub_demo', 't', 'danni.ekatte') ON CONFLICT DO NOTHING")
        return [x[0] for x in conn.execute("SELECT reason FROM core.serving_violation WHERE relation = 'pub_demo.t'")]

    def decide(column, kind, shown_as):
        conn.execute("""INSERT INTO core.person_column VALUES ('pub_demo', 't', %s, %s, %s, 'test', '2026-11-10')
            ON CONFLICT (schema_name, table_name, column_name) DO UPDATE SET kind = EXCLUDED.kind, shown_as = EXCLUDED.shown_as""",
                     (column, kind, shown_as))
        return [x[0] for x in conn.execute("SELECT reason FROM core.serving_violation WHERE relation = 'pub_demo.t'")]

    # the full name, by its type: never, whatever the decision says
    assert table('who core.person_name') == ['пълно име на човек в слоя за показване: who']
    assert decide('who', 'public-office', 'full') == ['пълно име на човек в слоя за показване: who']
    refused = publish(conn)
    assert refused.publication_id is None and refused.problems == ['pub_demo.t: пълно име на човек в слоя за показване: who']
    # a plain text column whose name says it is a person: stopped until someone decides
    assert table('donor_name text') == ['колона с име на човек без решение: donor_name']
    assert decide('donor_name', 'registered', 'masked') == ['колоната е решена като masked, а е обикновен текст: donor_name']
    assert decide('donor_name', 'not-a-person', 'full') == []
    # masked: the type and the decision must agree
    assert table('donor_name pub.masked_name') == ['маскирано име, а решението за колоната е друго: donor_name']
    assert decide('donor_name', 'registered', 'masked') == []
    # a full name in a public role: only with that decision
    assert table('mp pub.role_name') == ['колона с име на човек без решение: mp']
    assert decide('mp', 'registered', 'masked') == ['пълно име, а решението за колоната не е публична роля: mp']
    assert decide('mp', 'public-office', 'full') == []
    assert decide('mp', 'public-money', 'full') == []
    # the register itself refuses a full name for a private person, and keeping an incidental one
    for kind, shown_as in (('registered', 'full'), ('incidental', 'full'), ('incidental', 'masked')):
        with pytest.raises(Exception):
            decide('mp', kind, shown_as)
    # the review list sees a name on the ingest side too, by type or by the name of the column
    conn.execute('CREATE TABLE demo.raw (id int, donor core.person_name, signer_name text, donor_kind text, amount numeric)')
    assert sorted(x[0] for x in conn.execute("SELECT column_name FROM core.person_column_undecided WHERE schema_name = 'demo'")) == ['donor', 'signer_name']
    assert run.persons(conn) == 39   # the reviewed list is applied again: what is not in it loses its decision
    assert [x[0] for x in conn.execute("SELECT reason FROM core.serving_violation WHERE relation = 'pub_demo.t'")] == ['колона с име на човек без решение: mp']
