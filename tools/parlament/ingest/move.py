"""The move from the database of its own (until 13.11.2026) into the shared one: replay and parity.

replay  Every table the readers wrote (the sittings, items, votes, MPs, profiles, memberships, absences, penalties,
        stenograms, bills) is copied from one snapshot of the old database (REPEATABLE READ, read only), with the state
        of every read; the old ledger goes into the shared ops (runs, changes, raw files by sha256 with each read in
        ops.fetch, holds). Then what is built is built here, not copied: the groups' one code, every group's line, the
        statistics of the MPs, the links of the roll call, of the speakers, of the same person across assemblies, of the
        group bodies and of the bills to their votes (the same functions the daily steps run). The old database is only
        read. The relational tables are copied, not parsed again from raw/ (ARCHITECTURE.md, section 15: an old
        relational tool is moved as it is): 39 GB of raw answers would take about a day through the parsers.
parity  The old database against the clean tables and against the serving layer: every table by the number of rows
        and by a hash of its content, independent of the order (the sum of the md5 of every row); the votes and the
        results by assembly. Each comparison is a result of a rule of kind `parity` in ops.check_result.
"""
import os

import psycopg

from . import bills, pipeline, people, stats
from .config import OLD_DSN, TOOL

# (table, the columns copied): the old table in schema live has the same name and columns; speech leaves out its
# generated tsv (the serving layer indexes the expression)
TABLES = [
    ("assembly", 'no, kind, name, start, "end", api_id'),
    ("sitting", "id, date, assembly, heading, gv, iv, gv_sha, iv_sha, note, video, pdf, steno_sha"),
    ("item", "sitting, no, kind, at, topic, yes, no_, abstain, voted, present, listed, mismatch"),
    ("item_group", "sitting, no, grp, yes, no_, abstain, voted, present, listed"),
    ("mp", "assembly, no, name, profile"),
    ("vote", "sitting, item, mp, grp, code"),
    ("roster", "assembly, profile, name, grp_name, district, since"),
    ("body", "id, assembly, kind, name, since, until, n_start, n_end, n_total, grp"),
    ("profile", "id, assembly, name, district, list, profession, languages, person, read_at"),
    ("profile_past", "profile, assembly"),
    ("membership", "id, profile, body, body_name, body_kind, role, since, until"),
    ("absence", "id, date, profile, name, body, body_name, kind, first_seen"),
    ("penalty", "id, date, profile, name, kind, note, by_name, what, first_seen"),
    ("speech", "sitting, no, role, name, note, grp, profile, text"),
    ("bill", "id, sign, date, title, final_title, assembly, session, withdrawn, adopted, dv_issue, dv_year, government, sha, read_at"),
    ("bill_sponsor", "bill, pos, profile, name"),
    ("bill_committee", "bill, committee, name, role"),
    ("bill_step", "bill, id, date, sitting, committee, committee_name, what, stage"),
    ("bill_item", "bill, sitting, item, reading"),
]
# built from the tables above, compared with the old ones by parity
BUILT = [
    ("line", "sitting, item, grp, line, voters, with_line"),
    ("mp_stat", "assembly, mp, name, grp, votes, voted, yes, no_, abstain, regs, present, with_line, against_line, first, last, profile, district"),
]
STATE = "source, ref, last_read, last_ok, last_change, status, error, rows"
BATCH = 1000


def _old(old_dsn):
    old = psycopg.connect(old_dsn, autocommit=True)
    old.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")   # one snapshot for everything copied
    return old


def _copy(old, conn, source_sql, target, cols):
    """COPY from a query of the old database straight into a table here. -> rows."""
    n = 0
    with old.cursor().copy(f"COPY ({source_sql}) TO STDOUT") as src, \
            conn.cursor().copy(f"COPY {target} ({cols}) FROM STDIN") as dst:
        for block in src:
            dst.write(block)
    n = conn.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    return n


def replay(conn, r, old_dsn=OLD_DSN, old_live="live", old_ops="ops"):
    """The old database into the shared one, from one snapshot; then the builds. Refused when the tables are not empty:
    the move is done once (a test database is emptied by its fixtures)."""
    if conn.execute("SELECT EXISTS (SELECT FROM parlament.sitting) OR EXISTS (SELECT FROM parlament_src.read_state)").fetchone()[0]:
        raise RuntimeError("parlament вече има данни: replay се пуска веднъж, в празни таблици")
    conn.execute("SET synchronous_commit = off")
    old = _old(old_dsn)
    try:
        with conn.transaction():
            for table, cols in TABLES:
                r.stats.setdefault("rows", {})[table] = _copy(old, conn, f"SELECT {cols} FROM {old_live}.{table}",
                                                              f"parlament.{table}", cols)
            r.stats["rows"]["read_state"] = _copy(old, conn, f"SELECT {STATE} FROM {old_ops}.source_state",
                                                  "parlament_src.read_state", STATE)
            ledger(old, conn, r, old_ops)
        old.execute("COMMIT")
    finally:
        old.close()
    build(conn, r)


def ledger(old, conn, r, old_ops):
    """The old ledger into ops: the runs (marked from_old_database in their params), the change log, the raw files (once per sha256, in place; each old row is
    a read in ops.fetch with its ref and time), the holds. A raw file not on disk with its size is counted."""
    runs = old.execute(f"""SELECT step, started_at, finished_at, status, code_sha, inputs, stats, error
                           FROM {old_ops}.job_run ORDER BY id""").fetchall()
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO ops.run (tool, step, started_at, finished_at, status, code_sha, params, stats, error)
                           VALUES ('parlament', %s, %s, %s, %s, %s, %s, %s, %s)""",
                        [(s, a, b, st, sha, pipeline.Jsonb({**(i or {}), "from_old_database": True}), pipeline.Jsonb(x or {}), e)
                         for s, a, b, st, sha, i, x, e in runs])
    r.stats["runs"] = len(runs)
    changes = old.execute(f"SELECT detected_at, source, ref, field, old, new, cause FROM {old_ops}.change_log ORDER BY id").fetchall()
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO ops.change_log (detected_at, tool, source, ref, field, old, new, cause)
                           VALUES (%s, 'parlament', %s, %s, %s, %s, %s, %s)""", changes)
    r.stats["changes"] = len(changes)
    files = old.execute(f"SELECT source, ref, path, sha256, bytes, fetched_at FROM {old_ops}.raw_file ORDER BY id").fetchall()
    missing = 0
    for i in range(0, len(files), BATCH):
        part = files[i:i + BATCH]
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO ops.raw_file (sha256, bytes, path, first_seen_at) VALUES (%s, %s, %s, %s)
                               ON CONFLICT (sha256) DO NOTHING""", [(sha, size, path, at) for _, _, path, sha, size, at in part])
            cur.executemany("""INSERT INTO ops.fetch (run_id, tool, source, ref, raw_file_id, fetched_at)
                               SELECT %s, 'parlament', %s, %s, raw_file_id, %s FROM ops.raw_file WHERE sha256 = %s""",
                            [(r.run_id, source, ref, at, sha) for source, ref, _, sha, _, at in part])
        for _, _, path, _, size, _ in part:
            try:
                missing += os.stat(path).st_size != size
            except OSError:
                missing += 1
    r.stats.update(raw_files=len(files), raw_missing=missing)
    held = old.execute(f"SELECT source, ref, sha256, rows, first_at FROM {old_ops}.held").fetchall()
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO ops.hold (tool, source, ref, content_sha256, rows, reason, first_at)
                           VALUES ('parlament', %s, %s, %s, %s, 'пренесено от старата база', %s)""", held)
    r.stats["held"] = len(held)


def build(conn, r):
    """What the steps build, built here from the copied tables: the groups, the lines and the MPs' statistics of every
    assembly with a roll call, the links of the MPs, the speakers, the same person, the group bodies, the bills."""
    voted = [a for a, in conn.execute("SELECT DISTINCT assembly FROM parlament.sitting WHERE iv_sha IS NOT NULL ORDER BY 1")]
    stats.rebuild(conn, voted)
    with conn.transaction():
        r.stats["people"] = people.link(conn)
        stats.link_speakers(conn, assemblies=[a for a, in conn.execute("SELECT DISTINCT assembly FROM parlament.profile")])
        stats.link_bodies(conn)
    r.stats["bill_votes"] = bills.link(conn)
    r.stats["built"] = {t: conn.execute(f"SELECT count(*) FROM parlament.{t}").fetchone()[0] for t, _ in BUILT}


# ---------------------------------------------------------------- parity

# the hash of a table's content: the sum of the first 60 bits of the md5 of every row, as text with its columns in this
# order; the same rows in any order give the same sum
def _digest(cols, relation):
    return (f"SELECT count(*), coalesce(sum(('x' || substr(md5(ROW({cols})::text), 1, 15))::bit(60)::bigint::numeric), 0) "
            f"FROM {relation}")


SERVING = {   # the columns the serving layer keeps of each table (the times of a read stay on the ingest side)
    "profile": "id, assembly, name, district, list, profession, languages, person",
    "absence": "id, date, profile, name, body, body_name, kind",
    "penalty": "id, date, profile, name, kind, note, by_name, what",
    "bill": "id, sign, date, title, final_title, assembly, session, withdrawn, adopted, dv_issue, dv_year, government",
}
BY_ASSEMBLY = {   # (name, SQL on a schema {s}): numbers by assembly, compared one by one
    "votes": "SELECT s.assembly, count(*) FROM {s}.vote v JOIN {s}.sitting s ON s.id = v.sitting GROUP BY 1",
    "items": ("SELECT s.assembly, count(*), sum(i.yes), sum(i.no_), sum(i.abstain), sum(i.voted), sum(i.present) "
              "FROM {s}.item i JOIN {s}.sitting s ON s.id = i.sitting GROUP BY 1"),
    "speeches": "SELECT s.assembly, count(*), sum(length(sp.text)) FROM {s}.speech sp JOIN {s}.sitting s ON s.id = sp.sitting GROUP BY 1",
}


def parity(conn, r, old_dsn=OLD_DSN, old_live="live", old_ops="ops"):
    """The old database against the clean tables and the serving layer, table by table."""
    old = _old(old_dsn)
    differ, detail = 0, []
    try:
        def compare(name, old_sql, new_sql):
            nonlocal differ
            a, b = old.execute(old_sql).fetchone(), conn.execute(new_sql).fetchone()
            pipeline.record_check(conn, r, f"parity_{name}_rows", a[0], b[0])
            r.stats.setdefault("rows", {})[name] = [a[0], b[0]]
            if a != b:
                differ += 1
                detail.append(f"{name}: {a[0]} / {b[0]} реда, съдържанието " + ("еднакво" if a[1] == b[1] else "различно"))

        for table, cols in TABLES + BUILT:
            compare(table, _digest(cols, f"{old_live}.{table}"), _digest(cols, f"parlament.{table}"))
            if table not in ("roster", "profile_past"):   # the pages do not read these two
                c = SERVING.get(table, cols)
                compare(f"pub_{table}", _digest(c, f"{old_live}.{table}"), _digest(c, f"pub_parlament.{table}"))
        compare("read_state", _digest(STATE, f"{old_ops}.source_state"), _digest(STATE, "parlament_src.read_state"))
        compare("pub_read_state", _digest(STATE, f"{old_ops}.source_state"), _digest(STATE, "pub_parlament.read_state"))
        # the old ledger in ops: what the replay brought (the runs before it, the changes without a run, its reads)
        moved = "(SELECT max(run_id) FROM ops.run WHERE tool = 'parlament' AND step = 'replay' AND status = 'ok')"
        compare("ledger_runs", _digest("step, started_at, finished_at, status, error", f"{old_ops}.job_run"),
                _digest("step, started_at, finished_at, status, error", "ops.run WHERE tool = 'parlament' AND params ? 'from_old_database'"))
        compare("ledger_changes", _digest("detected_at, source, ref, field, old, new, cause", f"{old_ops}.change_log"),
                _digest("detected_at, source, ref, field, old, new, cause", "ops.change_log WHERE tool = 'parlament' AND run_id IS NULL"))
        compare("ledger_reads", _digest("source, ref, sha256, fetched_at", f"{old_ops}.raw_file"),
                _digest("f.source, f.ref, x.sha256, f.fetched_at",
                        f"ops.fetch f JOIN ops.raw_file x USING (raw_file_id) WHERE f.tool = 'parlament' AND f.run_id = {moved}"))
        for name, sql in BY_ASSEMBLY.items():
            a = {x[0]: x[1:] for x in old.execute(sql.format(s=old_live))}
            b = {x[0]: x[1:] for x in conn.execute(sql.format(s="pub_parlament"))}
            off = sorted((k for k in a.keys() | b.keys() if a.get(k) != b.get(k)), key=lambda k: (k is None, k))
            r.stats.setdefault("by_assembly", {})[name] = len(a)
            if off:
                differ += 1
                detail.append(f"{name} по събрание: различни {off[:10]}")
        old.execute("COMMIT")
    finally:
        old.close()
    pipeline.record_check(conn, r, "parity_differ", 0, differ, "; ".join(detail)[:2000] or None)
    r.stats.update(differ=differ, detail=detail)
