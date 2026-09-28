"""Bulgarian names of the HICP groups (coicop18) for db/ref/labels.csv, from the official text: Annex I of
Commission Delegated Regulation (EU) 2024/3159 (ECOICOP version 2 = UN COICOP 2018), in Bulgarian and English
from the Publications Office. The English text is only used to check that each code is the group Eurostat
names (its label in live.dim_label); the special aggregates (TOTAL, NRG, SERV …) are not in the regulation
and stay as written in labels.csv by hand. Run by hand when the classification changes:

    python tools/build_coicop_labels.py <eurostat labels: code|English per line>

Writes the coicop18 rows of db/ref/labels.csv again (the other rows are kept) and prints the codes whose
English name differs from Eurostat's, for a look.
"""
import csv
import html
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://publications.europa.eu/resource/celex/32024R3159"
KIND = re.compile(r"\s*\((НДТ|ПДТ|ДТ|У|ND|SD|D|S)\)\s*$")   # the goods/services mark after each name


def annex(lang):
    req = urllib.request.Request(URL, headers={"Accept": "application/xhtml+xml", "Accept-Language": lang,
                                               "User-Agent": "Prozrachnost/ikonomika (+https://github.com/DenislavDenev/prozrachnost)"})
    s = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
    cells = [html.unescape(re.sub(r"\s+", " ", c)).strip() for c in re.split(r"<[^>]+>", s)]
    cells = [c for c in cells if c]
    out = {}
    for code, name in zip(cells, cells[1:]):
        if re.fullmatch(r"\d\d(\.\d){0,3}", code) and not re.fullmatch(r"[\d.]+", name):
            out.setdefault("CP" + code.replace(".", ""), KIND.sub("", name))
    return out


def main(eurostat_txt):
    bg, en = annex("bul"), annex("eng")
    assert len(bg) > 300 and bg.keys() == en.keys(), (len(bg), len(en))
    ours = dict(line.split("|", 1) for line in Path(eurostat_txt).read_text(encoding="utf-8").splitlines() if "|" in line)
    norm = lambda s: re.sub(r"[^a-z]", "", s.lower())
    differ = [(c, en[c], ours[c]) for c in ours if c in en and norm(en[c]) != norm(KIND.sub("", ours[c]))]
    missing = sorted(c for c in ours if c.startswith("CP") and c not in bg)
    path = ROOT / "db" / "ref" / "labels.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8", newline="")))
    hand = {r["code"]: r for r in rows if r["dim"] == "coicop18" and r["code"] not in bg}   # by hand: see docs/sources.md
    keep = [r for r in rows if r["dim"] != "coicop18"]
    new = [*hand.values(), *({"dim": "coicop18", "code": c, "label": bg[c]} for c in sorted(bg) if c in ours)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["dim", "code", "label"], lineterminator="\n")
        w.writeheader()
        w.writerows([*new, *keep])
    print(len(new) - len(hand), "groups named from the regulation;", len(missing), "not in it (named by hand in labels.csv):", missing)
    assert all(c in hand for c in missing), [c for c in missing if c not in hand]
    for c, a, b in differ:
        print(f"{c}: regulation {a!r} / Eurostat {b!r}")


if __name__ == "__main__":
    main(sys.argv[1])
