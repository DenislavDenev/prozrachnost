"""The landing zone into the clean tables: the table that stands for (year, family), its rows and observations, and the
reconciliations that let it in.

For a year and a family the candidates are the current tables of the sets of that year. Two versions of one year (2019
has two sets) are not both used: the fuller one (more rows) is, and the note of the table says so. The table is taken
only when its gating reconciliations are equal; otherwise the previous one stays and the difference is a problem. Rows
whose structure the list does not know go to sigurnost.unmatched and stop the tables that are read by structure. The
serving layer follows by a publication at the end of the step (db/publish/*.sql).
"""
import csv
import datetime as dt
import re

from . import checks, config, load, templates

FAMILY_ORDER = ["types", "structures", "main", "types_by_structure", "econ_by_structure"]


def _key(s):
    return re.sub(r"\s+", " ", s.replace("„", '"').replace("“", '"').replace("”", '"').lower()).strip()


def load_reference(c):
    """The structures (an oblast is keyed by its NUTS 3 code in core.nuts), their spellings, the families and the
    indicators, from db/ref. One transaction."""
    with c.transaction():
        _load_reference(c)


def _load_reference(c):
    c.execute("DELETE FROM sigurnost.structure_alias")
    with open(config.ROOT / "db/ref/structure.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            c.execute("""INSERT INTO sigurnost.structure (code, name, kind, oblast, nuts_code, sort) VALUES (%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind, oblast = EXCLUDED.oblast,
                           nuts_code = EXCLUDED.nuts_code, sort = EXCLUDED.sort""",
                      (r["code"], r["name"], r["kind"], r["oblast"] or None, r["code"] if r["kind"] == "oblast" else None, int(r["sort"])))
            for a in r["aliases"].split("|"):
                c.execute("INSERT INTO sigurnost.structure_alias (alias, code) VALUES (%s,%s) ON CONFLICT DO NOTHING", (_key(a), r["code"]))
    for i, fam in enumerate(FAMILY_ORDER, 1):
        c.execute("INSERT INTO sigurnost.family (code, title, sort) VALUES (%s,%s,%s) ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title, sort = EXCLUDED.sort",
                  (fam, templates.FAMILIES[fam], i))
    with open(config.ROOT / "db/ref/indicators.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            c.execute("""INSERT INTO sigurnost.indicator (code, title, unit, kind, definition, example, formula, sort) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title, unit = EXCLUDED.unit, kind = EXCLUDED.kind,
                         definition = EXCLUDED.definition, example = EXCLUDED.example, formula = EXCLUDED.formula, sort = EXCLUDED.sort""",
                      (r["code"], r["title"], r["unit"], r["kind"], r["definition"], r["example"] or None, r["formula"] or None, int(r["sort"])))


def alias_map():
    """{normalised name: structure code} from db/ref/structure.csv (the same list sigurnost.structure_alias is loaded from)."""
    out = {}
    with open(config.ROOT / "db/ref/structure.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            for a in r["aliases"].split("|"):
                out.setdefault(_key(a), r["code"])
    return out


def resolver(c=None):
    alias = alias_map()
    return lambda name: alias.get(_key(name))


def candidates(c, year, fam):
    skip = set(checks.isolated())
    return [r for r in _candidates(c, year, fam) if r[0] not in skip]


def _candidates(c, year, fam):
    """(resource_uri, raw sha256, set_uri, template, rows, issues, set updated, load_id) of the current tables, fullest first."""
    return c.execute(
        """SELECT r.resource_uri, r.raw_sha256, r.set_uri, r.template, r.n_rows, r.issues, d.source_updated, r.load_id
           FROM sigurnost_src.current_resource r JOIN sigurnost.dataset d USING (set_uri)
           WHERE d.year = %s AND d.kind = 'police' AND r.family = %s AND r.kind = 'table'
             AND (r.title_year IS NULL OR r.title_year = d.year)
           ORDER BY r.n_rows DESC, d.source_updated DESC NULLS LAST, r.resource_uri""", (year, fam)).fetchall()


def _parents(rows):
    """Each row's parent by the level: the nearest earlier row with a lower level (a total row has none, nor has a point)."""
    stack, out = [], {}
    for r in rows:
        if r.total:
            out[r.no] = None
            continue
        while stack and stack[-1].level >= r.level:
            stack.pop()
        out[r.no] = stack[-1].no if stack else None
        stack.append(r)
    return out


def _results_for(c, year, picked, resolve):
    """The Results of all the families that are picked for the year (each is (uri, sha, ...))."""
    blocks = {fam: load.blocks_of(c, p[7]) for fam, p in picked.items()}
    res = {fam: [] for fam in picked}
    for fam, bl in blocks.items():
        res[fam].append(checks.clearance_formula(bl, fam))
        res[fam].append(checks.solved_le_registered(bl, fam))
        if fam == "structures":
            res[fam] += checks.structures_sum(bl[0])
        elif fam in ("types", "main"):
            res[fam] += checks.point_sums(bl[0], fam)
        else:       # the block of the whole country is the one whose sums are told about
            res[fam] += checks.point_sums(next((b for b in bl if resolve(b.structure) == "BG"), bl[0]), fam)
    if "types" in blocks and "structures" in blocks:
        res["types"] += checks.types_vs_structures(blocks["types"][0], blocks["structures"][0])
    if "structures" in blocks:
        for fam in ("types_by_structure",):
            if fam in blocks:
                res[fam] += checks.blocks_vs_structures(blocks[fam], blocks["structures"][0], resolve, fam)
    known = checks.known_differences()
    for fam in res:
        checks.apply_known(res[fam], picked[fam][0], known)
    return blocks, res


def build(c, years=None, rebuild=False):
    """Builds gold for the years that have tables. Returns a report: published, held (with the differences), unchanged."""
    resolve = resolver(c)
    rep = {"published": [], "held": [], "unchanged": [], "problems": []}
    all_years = [r[0] for r in c.execute("SELECT DISTINCT d.year FROM sigurnost.dataset d WHERE d.kind = 'police' AND d.year IS NOT NULL ORDER BY 1")]
    for year in years or all_years:
        picked, notes = {}, {}
        for fam in FAMILY_ORDER:
            cand = candidates(c, year, fam)
            if not cand:
                continue
            picked[fam] = cand[0]
            if len(cand) > 1:      # the note of the table says it; the choice is made again from the tables at every build
                notes[fam] = f"{len(cand)} версии на годината: взета е по-пълната ({cand[0][4]} реда); другата има {cand[1][4]}"
        if not picked:
            continue
        have = {r[0]: r[1] for r in c.execute("SELECT family, load_id FROM sigurnost.source_table WHERE year = %s", (year,))}
        if not rebuild and all(have.get(f) == p[7] for f, p in picked.items()) and len(have) == len(picked):
            rep["unchanged"].append(year)
            continue
        blocks, res = _results_for(c, year, picked, resolve)
        bad = {fam: [r for r in rs if r.gating and r.status == checks.DIFFERS] for fam, rs in res.items()}
        # a family that is read against another is held with it: a difference stops the tables it concerns
        stop = {fam for fam, b in bad.items() if b}
        if "structures" in stop:
            stop |= {f for f in ("types", "types_by_structure") if f in picked}
        if "types" in stop and "structures" in picked:
            stop.add("structures")
        for fam, p in picked.items():
            with c.transaction():       # the reconciliations of the table checked now replace those of the one checked before
                c.execute("DELETE FROM sigurnost.reconciliation WHERE year = %s AND family = %s", (year, fam))
                c.cursor().executemany(
                    """INSERT INTO sigurnost.reconciliation (year, family, n, check_id, scope, expected, got, diff, status, gating, detail,
                         resource_uri, raw_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(year, fam, n, r.check_id, r.scope, r.expected, r.got, r.diff, r.status, r.gating, r.detail, p[0], p[1])
                     for n, r in enumerate(res[fam], 1)])
            if fam in stop:
                msg = "; ".join(f"{r.check_id} {r.scope}: очаквано {r.expected}, получено {r.got}" for r in bad[fam][:3]) or "сверка с друга таблица на годината"
                rep["held"].append({"year": year, "family": fam, "resource": p[0], "why": msg})
                rep["problems"].append(f"Сигурност: {year} {fam} не е публикувана: {msg}")
                continue
            _publish(c, year, fam, p, blocks[fam], resolve, notes.get(fam))
            rep["published"].append({"year": year, "family": fam, "rows": p[4]})
    return rep


def _publish(c, year, fam, p, blocks, resolve, note):
    uri, sha, set_uri, tid, n_rows, issues, _, load_id = p
    inds = templates.indicators(fam, blocks[0].columns)
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    with c.transaction():
        for t in ("sigurnost.observation", "sigurnost.crime_row", "sigurnost.unmatched", "sigurnost.source_table"):
            c.execute(f"DELETE FROM {t} WHERE year = %s AND family = %s", (year, fam))
        c.execute("""INSERT INTO sigurnost.source_table (year, family, load_id, resource_uri, raw_sha256, set_uri, template, n_rows, issues, note)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (year, fam, load_id, uri, sha, set_uri, tid, n_rows, issues, note))
        rows_out, obs_out = [], []
        for b in blocks:
            parents = _parents(b.rows)
            block_code = resolve(b.structure) if b.structure else "BG"
            for r in b.rows:
                if fam == "structures":
                    code = resolve(r.text)
                else:
                    code = block_code
                if code is None:
                    c.execute("INSERT INTO sigurnost.unmatched (year, family, name, reason, resource_uri) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                              (year, fam, r.text if fam == "structures" else b.structure, "структурата не е в списъка", uri))
                    continue
                rows_out.append((year, fam, code, r.no, r.code, r.text, r.level, parents[r.no] if fam != "structures" else None, r.total))
                for col, text, value, issue in r.cells:
                    ind = inds.get(col)
                    if ind is not None:
                        obs_out.append((year, fam, code, r.no, ind, start, end, value, text, tid, uri, sha))
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO sigurnost.crime_row (year, family, structure_code, row_no, code, text, level, parent_row_no, is_total)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows_out)
            cur.executemany("""INSERT INTO sigurnost.observation (year, family, structure_code, row_no, indicator, period_kind, period_start, period_end,
                               value, value_text, template, resource_uri, raw_sha256) VALUES (%s,%s,%s,%s,%s,'year',%s,%s,%s,%s,%s,%s,%s)""",
                            [(y, f, s, n, i, ps, pe, v, t, tp, u, h) for (y, f, s, n, i, ps, pe, v, t, tp, u, h) in obs_out])
