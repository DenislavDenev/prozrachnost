"""The move from the database of its own (zakoni: silver, gold, ops) into the shared one, 24.11.2026.

replay  every resource the old database was built from is read again from the same file of the Наблюдател archive (the
        file of its ops.raw_file, checked by sha256), through the same parser, at the time the old database stored it.
        Nothing is copied from the old tables. The old database holds one read (02.10.2026, files of 28.09.2026) and no
        later version; replay refuses one that has history, since it would have to replay the versions in order.
parity  the old database against the shared one: the landing zone against the old silver row by row, the serving
        layer against the old gold row by row, the rows without a key, and counts and sums by year. Each is a result of a
        rule of kind `parity`.
"""
import psycopg

from . import archive, load, pipeline, ref
from .config import ARCHIVE, OLD_DSN
from .schema import TABLES

SILVER = list(TABLES)


def _old(old_dsn):
    o = psycopg.connect(old_dsn, autocommit=True)
    o.execute("SET default_transaction_read_only = on")
    return o


def accept_all(old, rows):
    """hold_when of the replay: the old database has already decided which answers it accepted."""
    return 0


def in_archive(path):
    """The old database names the file under the old path of the archive (/opt/tender/arhiv/egov/...)."""
    rel = path[path.index("/egov/") + 1:]
    return ARCHIVE / rel


def replay(conn, r, old_dsn=OLD_DSN):
    with _old(old_dsn) as o:
        history = sum(o.execute(f"SELECT count(*) FROM silver.{t} WHERE valid_to IS NOT NULL").fetchone()[0] for t in SILVER)
        if history:
            raise RuntimeError(f"старата база има {history} затворени версии: replay чете само една версия на ресурс")
        files = {x[0]: (x[1], x[2]) for x in o.execute("SELECT ref, path, sha256 FROM ops.raw_file WHERE source = 'egov'")}
        first = " UNION ALL ".join(f"SELECT min(valid_from) FROM silver.{t}" for t in SILVER)
        at = o.execute(f"SELECT min(m) FROM ({first}) x(m)").fetchone()[0]

    def pick(resource):
        if resource not in files:
            raise archive.ArchiveError(f"ресурсът {resource} липсва в старата база")
        path, sha = files[resource]
        got = archive.named(in_archive(path))
        if got[1] != sha:
            raise archive.ArchiveError(f"файлът {path} в архива не е този, от който е строена старата база")
        return got

    r.stats["replayed_at"] = at
    ref.load(conn, r, now=at)
    return load.read_all(conn, r, at, pick, force=True, hold_when=accept_all)


# ---------------------------------------------------------------- parity

def _rows(c, sql):
    return {tuple(x[:-1]): x[-1] for x in c.execute(sql)}


def _compare(conn, r, name, old, new):
    """Two {key: hash} maps: the count of the old against the new, and how many keys differ (missing, extra or changed)."""
    differ = len(old.keys() ^ new.keys()) + sum(1 for k in old.keys() & new.keys() if old[k] != new[k])
    pipeline.record_check(conn, r, f"parity_{name}", len(old), len(new))
    pipeline.record_check(conn, r, f"parity_{name}_differ", 0, differ)
    r.stats.setdefault("parity", {})[name] = dict(old=len(old), new=len(new), differ=differ)


SERVED = {   # table: (key columns, the old table, the new table)
    "act": ("pris_id", "gold.act", "pub_zakoni.act"),
    "act_institution": ("pris_id, institution_id", "gold.act_institution", "pub_zakoni.act_institution"),
    "act_relation": ("pris_id, ord", "gold.act_relation", "pub_zakoni.act_relation"),
    "consultation": ("reg_num", "gold.consultation", "pub_zakoni.consultation"),
    "strategy_doc": ("doc_key", "gold.strategy_doc", "pub_zakoni.strategy_doc"),
    "impact_contract": ("ic_key", "gold.impact_contract", "zakoni.impact_contract"),
}

PERIODS = [   # the same counts and sums by year on both sides: (SQL, how many columns are the group)
    ("SELECT year, act_type, count(*), count(gazette_number), sum(relations) FROM {act} GROUP BY 1, 2", 2),
    ("""SELECT extract(year FROM opened)::int, level, count(*), sum(days), sum(comment_count), count(*) FILTER (WHERE short_term_applies),
               count(*) FILTER (WHERE reason_given), count(municipality_id), sum(files) FROM {consultation} GROUP BY 1, 2""", 2),
    ("SELECT extract(year FROM accepted)::int, level, count(*), sum(files) FROM {strategy_doc} GROUP BY 1, 2", 2),
    ("SELECT extract(year FROM contract_date)::int, count(*), sum(price_bgn), sum(price_eur) FROM {impact_contract} GROUP BY 1", 1),
]


def parity(conn, r, old_dsn=OLD_DSN):
    with _old(old_dsn) as o:
        # the landing zone against the current rows of the old silver, every column the parser gives
        for t, (key, data) in TABLES.items():
            # a column the parser learned after the move (impact_contract.price_eur, 24.11.2026) is not in the old silver
            had = {x[0] for x in o.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'silver' "
                                           "AND table_name = %s", (t,))}
            data = [c for c in data if c in had]
            cols = ", ".join([*key, f"md5(ROW({', '.join(data)})::text)"])
            old = _rows(o, f"SELECT {cols} FROM silver.{t} WHERE valid_to IS NULL")
            new = _rows(conn, f"""SELECT {cols} FROM zakoni_src.{t}
                WHERE load_id IN (SELECT load_id FROM ops.load WHERE tool = 'zakoni' AND is_current)""")
            _compare(conn, r, f"src_{t}", old, new)
        # what the pages read against the old gold, whole rows
        for name, (key, old_t, new_t) in SERVED.items():
            alias = old_t.split(".")[1]
            old = _rows(o, f"SELECT {key}, md5(ROW({alias}.*)::text) FROM {old_t} {alias}")
            new = _rows(conn, f"SELECT {key}, md5(ROW({alias}.*)::text) FROM {new_t} {alias}")
            _compare(conn, r, name, old, new)
        old = _rows(o, "SELECT reg_num, ord, md5(ROW(kind, doc_date, link)::text) FROM silver.consultation_file WHERE valid_to IS NULL")
        new = _rows(conn, "SELECT reg_num, ord, md5(ROW(kind, doc_date, link)::text) FROM pub_zakoni.consultation_file")
        _compare(conn, r, "consultation_file", old, new)
        # the rows without a key, by table and field (the key of a relation to itself is pris_id/ord now)
        old = _rows(o, """SELECT tbl, field, count(*)::text FROM gold.unmatched
            WHERE build_id = (SELECT max(build_id) FROM gold.build WHERE ok) GROUP BY 1, 2""")
        new = _rows(conn, "SELECT tbl, field, count(*)::text FROM zakoni.unmatched GROUP BY 1, 2")
        _compare(conn, r, "unmatched", old, new)
        # counts and sums by year
        names = dict(act="gold.act", consultation="gold.consultation", strategy_doc="gold.strategy_doc", impact_contract="gold.impact_contract")
        mine = dict(act="pub_zakoni.act", consultation="pub_zakoni.consultation", strategy_doc="pub_zakoni.strategy_doc",
                    impact_contract="zakoni.impact_contract")
        old, new = {}, {}
        for n, (sql, group) in enumerate(PERIODS):
            for side, c, tables in ((old, o, names), (new, conn, mine)):
                for x in c.execute(sql.format(**tables)):
                    side[(n, *x[:group])] = str(x[group:])
        _compare(conn, r, "periods", old, new)
