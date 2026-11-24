import pytest

from ingest import documents, parse, pipeline, run

from . import seed


def complete(conn, r):
    """The catalogues and a complete pass over the profiles and the concessions, then the clean tables."""
    seed.load_all(conn, r)
    for source, full in (('appk', 'appk-full'), ('ncr', 'ncr-full')):
        from ingest import store
        store.load_full(conn, r, full, 'details', pipeline.utcnow(), 1)
    run.finish(conn, r)


def test_selects_only_actual_latest_annual_year(conn):
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        seed.load_all(conn, r)
        run.finish(conn, r)
        with pytest.raises(parse.ShapeError, match='complete'):
            documents.selected(conn, 'appk')
        complete(conn, r)
    rows = documents.selected(conn, 'appk')
    assert rows and {x['year'] for x in rows} == {2025} and {x['appk_id'] for x in rows} == {'105'}
    assert all(x['id'].startswith('105:annual:') for x in rows)
    assert len(rows) == conn.execute("""SELECT count(DISTINCT d.document_id || r.record_id) FROM sobstvenost.report r
        JOIN sobstvenost.report_document d USING (appk_id, record_id) WHERE r.kind = 'annual'""").fetchone()[0]
    files = documents.selected(conn, 'ncr')
    assert [x['ncr_id'] for x in files] == [seed.CONCESSION]


def test_attachment_versions_and_unknown_formats(conn):
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        complete(conn, r)
    body = {'value': b'%PDF-1.7\noriginal\n%%EOF'}

    class Client:
        deadline = None

        def get(self, url, data=None):
            return body['value']
    with pipeline.run(conn, 'sobstvenost', 'pdfs') as r:
        fetch = run.fetcher(conn, r, Client())
        results, problems = documents.archive(conn, r, lambda s, u: fetch(s, u, keep=True))
        assert problems == [] and results['ncr'] == dict(completed=1, total=1)
        n = results['appk']['total']
        documents.archive(conn, r, lambda s, u: fetch(s, u, keep=True))
        assert conn.execute('SELECT count(*) FROM sobstvenost.attachment').fetchone()[0] == n + 1
        body['value'] = b'%PDF-1.7\nchanged\n%%EOF'
        documents.archive(conn, r, lambda s, u: fetch(s, u, keep=True))
        assert conn.execute('SELECT count(*) FROM sobstvenost.attachment').fetchone()[0] == 2 * (n + 1)
        assert conn.execute("SELECT count(DISTINCT sha256) FROM sobstvenost.attachment").fetchone()[0] == 2
        run.finish(conn, r)
    assert conn.execute("SELECT status FROM pub_sobstvenost.source_state WHERE source = 'ncr' AND scope = 'pdfs'").fetchone()[0] == 'ok'
    # a file of an unknown format is refused for its attachment; the pass goes on and is not complete
    body['value'] = b'<html>Error</html>'
    with pipeline.run(conn, 'sobstvenost', 'pdfs') as r:
        results, problems = documents.archive(conn, r, lambda s, u: fetch(s, u, keep=True))
    assert problems and 'непознат формат' in problems[0]
    assert conn.execute("""SELECT count(*) FROM ops.load WHERE tool = 'sobstvenost' AND source = 'appk-files' AND ref = '#full'""").fetchone()[0] == 3
    with pytest.raises(parse.ShapeError):
        documents.media_type(b'<html>Error</html>')
