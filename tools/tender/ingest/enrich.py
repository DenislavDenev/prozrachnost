"""Enrichment (plan 8): public-figure candidates from Wikidata and news-article candidates from GDELT.

Nothing here is published by itself: every row lands in ed.* as `candidate` and becomes visible
only after a person confirms it in /admin/review. A name match is a candidate, never proof.
"""
import json
import time
import urllib.parse

from . import registry as R
from .http import get

WIKIDATA = "https://query.wikidata.org/sparql"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_PAUSE = 6  # GDELT asks for no more than one request every ~5 seconds

# Bulgarian citizens who hold or held a public position, or are politicians, with a Bulgarian label.
SPARQL = """
SELECT ?p ?label ?desc ?img WHERE {
  ?p wdt:P31 wd:Q5; wdt:P27 wd:Q219.
  { ?p wdt:P39 ?pos } UNION { ?p wdt:P106 wd:Q82955 }
  ?p rdfs:label ?label. FILTER(LANG(?label) = "bg")
  OPTIONAL { ?p schema:description ?desc. FILTER(LANG(?desc) = "bg") }
  OPTIONAL { ?p wdt:P18 ?img }
}"""


def wikidata_people():
    url = WIKIDATA + "?" + urllib.parse.urlencode({"query": SPARQL, "format": "json"})
    data = json.loads(get(url, accept="application/sparql-results+json", timeout=150, retries=2, delay=30))  # ~90 s query
    out = {}
    for b in data["results"]["bindings"]:
        qid = b["p"]["value"].rsplit("/", 1)[-1]
        out[qid] = {"qid": qid, "label": b["label"]["value"], "desc": b.get("desc", {}).get("value"),
                    "img": b.get("img", {}).get("value")}
    return list(out.values())


def first_last(name):
    parts = R.person_name_key(R.without_title(name)).split()
    return (parts[0], parts[-1]) if len(parts) >= 2 else None


def public_figure_candidates(conn, stats):
    """Match Wikidata labels ("Име Фамилия") to register names ("ИМЕ ПРЕЗИМЕ ФАМИЛИЯ") by first and last name."""
    people = wikidata_people()
    stats["wikidata_people"] = len(people)
    by_key = {}
    for p in people:
        k = first_last(p["label"])
        if k:
            by_key.setdefault(k, []).append(p)
    n = 0
    for pid, name in conn.execute("SELECT id, name FROM tr.person").fetchall():
        k = first_last(name)
        for p in by_key.get(k, []) if k else []:
            photo = urllib.parse.unquote(p["img"].rsplit("/", 1)[-1]) if p["img"] else None
            n += conn.execute(
                "INSERT INTO ed.public_figure(person_id, wikidata_qid, label, description, photo_file) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (person_id, wikidata_qid) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description, "
                "photo_file = EXCLUDED.photo_file", (pid, p["qid"], p["label"], p["desc"], photo)).rowcount
    stats["candidates"] = n


def commons_license(conn, stats):
    """Licence and author of confirmed photos (Wikimedia Commons imageinfo API)."""
    rows = conn.execute("SELECT person_id, wikidata_qid, photo_file FROM ed.public_figure "
                        "WHERE status = 'confirmed' AND photo_file IS NOT NULL AND photo_license IS NULL").fetchall()
    for pid, qid, f in rows:
        q = {"action": "query", "titles": "File:" + f, "prop": "imageinfo", "iiprop": "extmetadata", "format": "json"}
        data = json.loads(get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(q)))
        meta = next(iter(data["query"]["pages"].values())).get("imageinfo", [{}])[0].get("extmetadata", {})
        lic = (meta.get("LicenseShortName") or {}).get("value")
        author = (meta.get("Artist") or {}).get("value")
        conn.execute("UPDATE ed.public_figure SET photo_license = %s, photo_author = %s WHERE person_id = %s AND wikidata_qid = %s",
                     (lic, author, pid, qid))
    stats["licenses"] = len(rows)


def gdelt(query, maxrecords=50):
    q = {"query": query, "mode": "artlist", "format": "json", "maxrecords": maxrecords, "sort": "datedesc", "timespan": "3months"}
    try:
        raw = get(GDELT + "?" + urllib.parse.urlencode(q), timeout=30, retries=1)
    except RuntimeError:  # rate limited or slow: skip this target, the next weekly run tries again
        return []
    try:
        return json.loads(raw).get("articles", [])
    except ValueError:  # GDELT answers plain text for queries it rejects (too short, too common)
        return []


def articles(conn, stats, budget_s=1800, companies=60):
    """Article candidates for confirmed public figures and the largest contractors (by value)."""
    deadline = time.monotonic() + budget_s
    targets = [("person", pid, f'"{label}"', "name")
               for pid, label in conn.execute("SELECT person_id, label FROM ed.public_figure WHERE status = 'confirmed'")]
    targets += [("company", key, f'"{name}"', "name")
                for key, name in conn.execute(
                    """SELECT c.key, coalesce(d.name, c.name) FROM live.company c JOIN live.company_stats s USING (key)
                       LEFT JOIN tr.deed d ON d.eik = left(c.eik, 9)
                       WHERE length(coalesce(d.name, c.name)) >= 6 ORDER BY s.amount_eur DESC NULLS LAST LIMIT %s""", (companies,))]
    found = 0
    for kind, ref, query, basis in targets:
        if time.monotonic() > deadline:
            break
        for a in gdelt(query + " sourcelang:bulgarian"):
            seen = a.get("seendate")
            found += conn.execute(
                "INSERT INTO ed.article(entity_type, entity_id, url, title, source, published_at, match_basis) "
                "VALUES (%s,%s,%s,%s,%s,to_timestamp(%s, 'YYYYMMDD\"T\"HH24MISS\"Z\"'),%s) ON CONFLICT DO NOTHING",
                (kind, ref, a["url"], a.get("title") or a["url"], a.get("domain"), seen, basis)).rowcount
        time.sleep(GDELT_PAUSE)
    stats["article_candidates"] = found
    stats["targets"] = len(targets)
