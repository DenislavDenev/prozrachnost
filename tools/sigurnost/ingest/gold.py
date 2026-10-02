"""Silver into gold: the table that stands for (year, family), its rows and observations, and the checks that let it out.

For a year and a family the candidates are the current, built tables of the sets of that year. Two versions of one year
(2019 has two sets) are not both used: the fuller one (more rows) is, and the difference is written to the log of changes.
The table is published only when its gating checks are equal; otherwise the previous gold stays and the difference is a
problem. Rows whose structure the list does not know go to gold.unmatched and stop the publication of a family that is
read by structure.
"""
import csv
import datetime as dt
import re

from . import checks, config, load, templates

FAMILY_ORDER = ["types", "structures", "main", "types_by_structure", "econ_by_structure"]


def _key(s):
    return re.sub(r"\s+", " ", s.replace("„", '"').replace("“", '"').replace("”", '"').lower()).strip()


def load_reference(c):
    c.execute("TRUNCATE gold.structure_alias")
    with open(config.ROOT / "db/ref/structure.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            c.execute("""INSERT INTO gold.structure (code, name, kind, oblast, sort) VALUES (%s,%s,%s,%s,%s)
                         ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind, oblast = EXCLUDED.oblast, sort = EXCLUDED.sort""",
                      (r["code"], r["name"], r["kind"], r["oblast"] or None, int(r["sort"])))
            for a in r["aliases"].split("|"):
                c.execute("INSERT INTO gold.structure_alias (alias, code) VALUES (%s,%s) ON CONFLICT DO NOTHING", (_key(a), r["code"]))
    for i, fam in enumerate(FAMILY_ORDER, 1):
        c.execute("INSERT INTO gold.family (code, title, sort) VALUES (%s,%s,%s) ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title, sort = EXCLUDED.sort",
                  (fam, templates.FAMILIES[fam], i))
    with open(config.ROOT / "db/ref/indicators.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            c.execute("""INSERT INTO gold.indicator (code, title, unit, kind, definition, example, formula, sort) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title, unit = EXCLUDED.unit, kind = EXCLUDED.kind,
                         definition = EXCLUDED.definition, example = EXCLUDED.example, formula = EXCLUDED.formula, sort = EXCLUDED.sort""",
                      (r["code"], r["title"], r["unit"], r["kind"], r["definition"], r["example"] or None, r["formula"] or None, int(r["sort"])))


def alias_map():
    """{normalised name: structure code} from db/ref/structure.csv (the same list gold.structure_alias is loaded from)."""
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
    return c.execute(
        """SELECT r.resource_uri, r.sha256, r.set_uri, r.template, r.n_rows, r.issues, d.source_updated
           FROM silver.resource r JOIN silver.dataset d USING (set_uri)
           WHERE d.year = %s AND d.kind = 'police' AND r.family = %s AND r.is_current AND r.status = 'built'
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
    blocks = {fam: load.silver_blocks(c, p[0], p[1]) for fam, p in picked.items()}
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
    return blocks, res


def build(c, years=None, rebuild=False):
    """Builds gold for the years that have tables. Returns a report: published, held (with the differences), unchanged."""
    resolve = resolver(c)
    rep = {"published": [], "held": [], "unchanged": [], "problems": []}
    all_years = [r[0] for r in c.execute("SELECT DISTINCT d.year FROM silver.dataset d WHERE d.kind = 'police' AND d.year IS NOT NULL ORDER BY 1")]
    for year in years or all_years:
        picked, notes = {}, {}
        for fam in FAMILY_ORDER:
            cand = candidates(c, year, fam)
            if not cand:
                continue
            picked[fam] = cand[0]
            if len(cand) > 1:
                notes[fam] = f"{len(cand)} версии на годината: взета е по-пълната ({cand[0][4]} реда); другата има {cand[1][4]}"
                ref = f"{year}/{fam}"
                if not c.execute("SELECT 1 FROM ops.change_log WHERE source = 'egov' AND ref = %s AND cause = 'duplicate' AND new = %s", (ref, f"{cand[0][4]}")).fetchone():
                    load._log(c, ref, "rows", str(cand[1][4]), str(cand[0][4]), "duplicate")
        if not picked:
            continue
        have = {r[0]: (r[1], r[2]) for r in c.execute("SELECT family, resource_uri, sha256 FROM gold.source_table WHERE year = %s", (year,))}
        if not rebuild and all(have.get(f) == (p[0], p[1]) for f, p in picked.items()) and len(have) == len(picked):
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
            for r in res[fam]:
                c.execute("""INSERT INTO gold.check_result (year, family, check_id, scope, expected, got, diff, status, gating, detail, resource_uri, sha256)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (year, fam, r.check_id, r.scope, r.expected, r.got, r.diff, r.status, r.gating, r.detail, p[0], p[1]))
            if fam in stop:
                msg = "; ".join(f"{r.check_id} {r.scope}: очаквано {r.expected}, получено {r.got}" for r in bad[fam][:3]) or "сверка с друга таблица на годината"
                rep["held"].append({"year": year, "family": fam, "resource": p[0], "why": msg})
                rep["problems"].append(f"Сигурност: {year} {fam} не е публикувана: {msg}")
                continue
            _publish(c, year, fam, p, blocks[fam], resolve, notes.get(fam))
            rep["published"].append({"year": year, "family": fam, "rows": p[4]})
    return rep


def _publish(c, year, fam, p, blocks, resolve, note):
    uri, sha, set_uri, tid, n_rows, issues, _ = p
    inds = templates.indicators(fam)
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    with c.transaction():
        for t in ("gold.observation", "gold.crime_row", "gold.unmatched"):
            c.execute(f"DELETE FROM {t} WHERE year = %s AND family = %s", (year, fam))
        c.execute("DELETE FROM gold.source_table WHERE year = %s AND family = %s", (year, fam))
        c.execute("""INSERT INTO gold.source_table (year, family, resource_uri, sha256, set_uri, template, n_rows, issues, note)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (year, fam, uri, sha, set_uri, tid, n_rows, issues, note))
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
                    c.execute("INSERT INTO gold.unmatched (year, family, name, reason, resource_uri) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                              (year, fam, r.text if fam == "structures" else b.structure, "структурата не е в списъка", uri))
                    continue
                rows_out.append((year, fam, code, r.no, r.code, r.text, r.level, parents[r.no] if fam != "structures" else None, r.total))
                for col, text, value, issue in r.cells:
                    ind = inds.get(col)
                    if ind is not None:
                        obs_out.append((year, fam, code, r.no, ind, start, end, value, text, tid, uri, sha))
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO gold.crime_row (year, family, structure_code, row_no, code, text, level, parent_row_no, is_total)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows_out)
            cur.executemany("""INSERT INTO gold.observation (year, family, structure_code, row_no, indicator, period_start, period_end, value, value_text,
                               template, resource_uri, raw_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [(y, f, s, n, i, ps, pe, v, t, tp, u, h) for (y, f, s, n, i, ps, pe, v, t, tp, u, h) in obs_out])
