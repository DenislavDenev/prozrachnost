"""Vendored front-end libraries (app/static/vendor): check cdnjs for new versions and update.

    python tools/vendor.py            # report: JSON per library (current, latest, kind = same|minor|major)
    python tools/vendor.py --update   # take minor/patch updates (file checked against the cdnjs SRI hash);
                                      # a major version is only reported, it needs a person to look at it

Source: cdnjs (api.cdnjs.com), the same files the pages used to load from it. Licences are kept next to
the files (LICENSE-*.txt). Stdlib only.
"""
import base64
import hashlib
import json
import pathlib
import sys
import urllib.request

DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "vendor"
UA = {"User-Agent": "tender.denev.work vendor check"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def kind(cur, new):
    a, b = [list(map(int, v.split(".")[:3])) for v in (cur, new)]
    return "same" if a == b else "major" if b[0] != a[0] else "minor" if b > a else "same"


def main(update=False):
    vs = json.loads((DIR / "versions.json").read_text())
    out = []
    for lib, v in vs.items():
        latest = json.loads(get(f"https://api.cdnjs.com/libraries/{lib}?fields=version"))["version"]
        row = {"lib": lib, "current": v["version"], "latest": latest, "kind": kind(v["version"], latest)}
        if update and row["kind"] == "minor":
            name = v["file"]
            body = get(f"https://cdnjs.cloudflare.com/ajax/libs/{lib}/{latest}/{name}")
            sri = json.loads(get(f"https://api.cdnjs.com/libraries/{lib}/{latest}?fields=sri"))["sri"][name]
            got = "sha512-" + base64.b64encode(hashlib.sha512(body).digest()).decode()
            if got != sri:
                raise SystemExit(f"{lib} {latest}: hash {got} does not match cdnjs {sri}")
            (DIR / name).write_bytes(body)
            v["version"] = latest
            row["updated"] = True
        out.append(row)
    if update:
        (DIR / "versions.json").write_text(json.dumps(vs, indent=2) + "\n")
    print(json.dumps(out))


def _check():
    assert kind("3.34.3", "3.34.3") == "same" and kind("3.34.3", "3.35.0") == "minor"
    assert kind("6.1.0", "7.0.0") == "major" and kind("6.1.0", "6.0.9") == "same"


if __name__ == "__main__":
    _check()
    main("--update" in sys.argv)
