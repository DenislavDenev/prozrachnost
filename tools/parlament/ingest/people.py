"""The assemblies and their people, and the Assembly's lists that forget: absences and penalties.

- assemblies(): every assembly from 1879 from db/ref/assemblies.csv (no network); from the 39th the API's ids and dates.
- people(): for every assembly the API knows, its bodies (groups, committees ... with dates) and every MP's profile
  with their memberships; then the same person across assemblies: the Assembly lists the earlier assemblies of an
  MP, and the earlier profile is the one with the same full name in that assembly, only where that name is there
  once (the old number is not given). A profile read before is read again only in the current assembly.
- absences(): the official absences and the chair's penalties, kept: the Assembly shows only the last months.
"""
import csv
import json

from . import http, parse, stats
from .config import API, ROOT
from .load import date_sittings, save_raw, state


# the kinds of body in a membership (the numbers of coll-list/bg/<kind>)
KINDS = {1: "събрание", 2: "група", 3: "комисия", 4: "временна комисия", 6: "делегация", 9: "подкомисия"}


def assemblies(conn):
    """db/ref/assemblies.csv into live.assembly; rows the API refined keep the API's dates."""
    with open(ROOT / "db" / "ref" / "assemblies.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        conn.execute("""INSERT INTO live.assembly (no, kind, name, start, "end") VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (no) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name
                        """, (int(r["no"]), r["kind"], r["name"], r["start"], r["end"] or None))
    date_sittings(conn)
    return len(rows)


def people(conn, stats_, get=None, everyone=False):
    """-> stats. Every assembly of the API: bodies, then profiles (all of them the first time or with everyone=True,
    else only the current assembly's), then the links between the assemblies."""
    get = get or http.get
    raw = get(f"{API}/fn-assembly/bg")
    known = parse.assemblies(raw)
    save_raw(conn, "fn-assembly", "fn-assembly.json", raw)
    current = max(n for _, n in known)
    read, problems = 0, []
    for api_id, no in sorted(known, key=lambda x: x[1]):
        conn.execute("UPDATE live.assembly SET api_id = %s WHERE no = %s", (api_id, no))
        ref = f"assembly/{no}"
        try:
            if no != current:            # the current one answers 500 here; its bodies come with the memberships
                raw = get(f"{API}/archive/bg/{api_id}")
                a = parse.archive(raw)
                with conn.transaction():
                    save_raw(conn, ref, f"archive-{api_id}.json", raw)
                    conn.execute("""UPDATE live.assembly SET start = %s, "end" = %s WHERE no = %s""", (a["start"], a["end"], no))
                    for b in a["bodies"]:
                        conn.execute("""INSERT INTO live.body VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET
                                        assembly = EXCLUDED.assembly, kind = EXCLUDED.kind, name = EXCLUDED.name, since = EXCLUDED.since,
                                        until = EXCLUDED.until, n_start = EXCLUDED.n_start, n_end = EXCLUDED.n_end, n_total = EXCLUDED.n_total""",
                                     (b["id"], no, b["kind"], b["name"], b["since"], b["until"], b["n_start"], b["n_end"], b["n_total"]))
            raw = get(f"{API}/fn-mps/bg/{api_id}")
            ids = parse.mps(raw)
            have = {i for i, in conn.execute("SELECT id FROM live.profile WHERE assembly = %s", (no,))}
            for pid in ids:
                if pid in have and no != current and not everyone:
                    continue
                profile(conn, no, pid, get)
                read += 1
            state(conn, ref, status="ok", error=None, last_ok="now", rows=len(ids))
        except (http.Gone, parse.ShapeError) as e:
            state(conn, ref, status="invalid", error=str(e)[:2000])
            problems.append(f"Парламент: {no}-о Народно събрание: {e}")
    date_sittings(conn)
    linked = link(conn)
    # the roll calls and the stenograms get the profiles; what the pages read is built again
    stats.link_speakers(conn, assemblies=[n for n, in conn.execute("SELECT DISTINCT assembly FROM live.profile")])
    voted = [n for n, in conn.execute("SELECT DISTINCT assembly FROM live.sitting WHERE iv_sha IS NOT NULL")]
    if voted:
        stats.rebuild(conn, voted)
    stats_.update(assemblies=len(known), profiles=read, people=linked, problems=problems)
    return stats_


def profile(conn, assembly, pid, get):
    raw = get(f"{API}/mp-profile/bg/{pid}")
    p = parse.profile(raw)
    if p["id"] != pid:
        raise parse.ShapeError(f"asked for profile {pid}, got {p['id']}")
    past = [n for n, in conn.execute("SELECT no FROM live.assembly WHERE api_id = ANY(%s)", (p["past"],))]
    with conn.transaction():
        save_raw(conn, f"profile/{pid}", f"mp-profile-{pid}.json", raw)
        conn.execute("""INSERT INTO live.profile (id, assembly, name, district, list, profession, languages) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO UPDATE SET assembly = EXCLUDED.assembly, name = EXCLUDED.name, district = EXCLUDED.district,
                        list = EXCLUDED.list, profession = EXCLUDED.profession, languages = EXCLUDED.languages, read_at = now()""",
                     (pid, assembly, p["name"], p["district"], p["list"], p["profession"], p["languages"]))
        conn.execute("DELETE FROM live.profile_past WHERE profile = %s", (pid,))
        for n in past:
            conn.execute("INSERT INTO live.profile_past VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, n))
        conn.execute("DELETE FROM live.membership WHERE profile = %s", (pid,))
        for m in p["memberships"]:
            conn.execute("INSERT INTO live.membership VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                         (m["id"], pid, m["body"], m["body_name"], m["body_kind"], m["role"], m["since"], m["until"]))
            conn.execute("""INSERT INTO live.body (id, assembly, kind, name) VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                         (m["body"], assembly, KINDS.get(m["body_kind"]), m["body_name"]))


def link(conn):
    """live.profile.person: the smallest profile id of the same person. Two profiles are one person where the later
    one lists the earlier assembly and the full name is there once. -> the number of people."""
    edges = conn.execute("""SELECT pp.profile, min(o.id) FROM live.profile_past pp
                            JOIN live.profile p ON p.id = pp.profile
                            JOIN live.profile o ON o.assembly = pp.assembly AND o.name = p.name
                            GROUP BY pp.profile, pp.assembly HAVING count(*) = 1""").fetchall()
    boss = {i: i for i, in conn.execute("SELECT id FROM live.profile")}

    def root(x):
        while boss[x] != x:
            boss[x] = boss[boss[x]]
            x = boss[x]
        return x
    for a, b in edges:
        ra, rb = root(a), root(b)
        if ra != rb:
            boss[max(ra, rb)] = min(ra, rb)
    with conn.transaction():
        conn.execute("CREATE TEMP TABLE person_of (id integer PRIMARY KEY, person integer) ON COMMIT DROP")
        with conn.cursor().copy("COPY person_of FROM STDIN") as cp:
            for i in boss:
                cp.write_row((i, root(i)))
        conn.execute("""UPDATE live.profile p SET person = x.person FROM person_of x
                        WHERE x.id = p.id AND p.person IS DISTINCT FROM x.person""")
    return len({root(i) for i in boss})


def absences(conn, stats_, get=None):
    """The official absences and penalties the Assembly shows now, added to what we keep (never removed here)."""
    get = get or http.post
    new = {}
    for ref, url, parser, table, cols in (
            ("absences", f"{API}/mp-absense/bg", parse.absences, "live.absence",
             ("id", "date", "profile", "name", "body", "body_name", "kind")),
            ("penalties", f"{API}/mp-penalty", parse.penalties, "live.penalty",
             ("id", "date", "profile", "name", "kind", "note", "by", "what"))):
        raw = get(url, json.dumps({}).encode())
        rows = parser(raw)
        with conn.transaction():
            save_raw(conn, ref, f"{ref}.json", raw)
            n = 0
            for r in rows:
                n += conn.execute(f"""INSERT INTO {table} ({', '.join(c if c != 'by' else 'by_name' for c in cols)})
                                      VALUES ({', '.join(['%s'] * len(cols))}) ON CONFLICT (id) DO NOTHING""",
                                  tuple(r[c] for c in cols)).rowcount
            state(conn, ref, status="ok", error=None, last_ok="now", rows=len(rows), **({"last_change": "now"} if n else {}))
        new[ref] = n
    stats_.update(new=new)
    return stats_

