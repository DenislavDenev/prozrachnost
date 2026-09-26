"""Fetch public source snapshots into an isolated local database.

Each successful source replaces only its own rows. A failed fetch leaves the last
known snapshot available and marks its status as stale.
"""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from db import connect

TRAFFIC = "https://bgtoll.bg/index.php/traffic_passes/data"
WEATHER = "https://bgtoll.bg/index.php/mto/data"
CATALOG = "https://datasheet.api.bg/?lang=bg&g={group}&c={code}"
ARCGIS = "https://services6.arcgis.com/GLbanbtRQ5XHYhZ4/arcgis/rest/services/Network_Wide_Road_Safety_Assessment/FeatureServer"
MVR = "https://www.mvr.bg/PTPShapeResult/ptp.zip"


def fetch(url, timeout=40):
    request = urllib.request.Request(url, headers={"User-Agent": "Prozrachnost/0.1 (+https://github.com/DenislavDenev)", "Accept": "application/json,text/html,application/xml,*/*"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(db, key, label, url, count=None, error=None):
    previous = db.execute("SELECT updated,count FROM sources WHERE key=?", (key,)).fetchone()
    db.execute("REPLACE INTO sources VALUES(?,?,?,?,?,?)", (key,label,url, now() if error is None else (previous["updated"] if previous else None), count if error is None else (previous["count"] if previous else 0), error))


def load_toll(db):
    for key, label, url, table in [("traffic", "БГТОЛ · измерен трафик", TRAFFIC, "traffic"), ("weather", "БГТОЛ · метеостанции", WEATHER, "weather")]:
        try:
            data = json.loads(fetch(url))
            if not isinstance(data, list) or not data:
                raise ValueError("Empty or unexpected response")
            rows = []
            for item in data:
                try:
                    lat, lon = float(item["lat"]), float(item["lon"])
                    if not 40.5 <= lat <= 44.5 or not 21.5 <= lon <= 29:
                        continue
                    if table == "traffic":
                        rows.append((str(item["scp"]),item.get("name"),lat,lon,item.get("count15min"),item.get("count1Hour"),item.get("average_speed_15min"),item.get("time")))
                    else:
                        rows.append((str(item["scp"]),item.get("name"),lat,lon,item.get("air"),item.get("surface"),item.get("humidity"),item.get("wind"),item.get("time")))
                except (KeyError, ValueError, TypeError):
                    continue
            if not rows:
                raise ValueError("No valid station coordinates")
            with db:
                db.execute(f"DELETE FROM {table}")
                db.executemany(f"INSERT INTO {table} VALUES({','.join('?' for _ in rows[0])})", rows)
                record(db,key,label,url,len(rows))
            print(f"{key}: {len(rows)}")
        except Exception as exc:
            with db:
                record(db,key,label,url,error=str(exc)[:220])
            print(f"{key}: ERROR {exc}")


def parse_datex(raw, kind, source_url):
    # Some LIMA files declare UTF-16 while actually returning UTF-8 bytes.
    source = re.sub(r'^<\?xml[^>]+\?>', '', raw.decode("utf-8-sig", "replace"), count=1)
    root = ET.fromstring(source)
    ns = {"d": "http://datex2.eu/schema/2/2_0"}
    result = []
    for situation in root.findall(".//d:situation", ns):
        rec = situation.find("d:situationRecord", ns)
        if rec is None:
            continue
        coords = rec.find(".//d:pointCoordinates", ns)
        if coords is None:
            continue
        try:
            lat = float(coords.findtext("d:latitude", namespaces=ns))
            lon = float(coords.findtext("d:longitude", namespaces=ns))
        except (TypeError, ValueError):
            continue
        values = [v.text.strip() for v in rec.findall(".//d:generalPublicComment/d:comment/d:values/d:value", ns) if v.text and v.attrib.get("lang") == "bg"]
        title = values[0] if values else kind
        detail = values[1] if len(values) > 1 else title
        result.append((str(rec.attrib.get("id") or situation.attrib.get("id")),kind,title,detail,lat,lon,rec.findtext(".//d:overallStartTime", namespaces=ns),rec.findtext(".//d:overallEndTime", namespaces=ns),source_url))
    return result


def load_events(db):
    for group, code, kind, label in [("roadworks","r01","Затворен път","АПИ ЛИМА · затворени пътища"),("roadworks","r02","Затворена лента","АПИ ЛИМА · затворени ленти"),("danger","d01","Опасност","АПИ ЛИМА · инциденти")]:
        key = f"lima_{code}"
        url = CATALOG.format(group=group,code=code)
        try:
            catalog = fetch(url).decode("utf-8", "replace")
            match = re.search(r'/files/[^"\'<> ]+\.xml', catalog)
            if not match:
                raise ValueError("No XML link in catalog")
            xml_url = urllib.parse.urljoin("https://datasheet.api.bg",match.group(0))
            rows = parse_datex(fetch(xml_url),kind,xml_url)
            with db:
                db.execute("DELETE FROM events WHERE kind=?", (kind,))
                db.executemany("INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?)", rows)
                record(db,key,label,xml_url,len(rows))
            print(f"{key}: {len(rows)}")
        except Exception as exc:
            with db:
                record(db,key,label,url,error=str(exc)[:220])
            print(f"{key}: ERROR {exc}")


def load_risk(db):
    all_rows = []
    try:
        for layer in range(6):
            url = f"{ARCGIS}/{layer}/query"
            offset = 0
            while True:
                query = urllib.parse.urlencode({"where":"1=1","outFields":"*","returnGeometry":"true","outSR":4326,"resultOffset":offset,"resultRecordCount":1000,"f":"json"})
                response = json.loads(fetch(url + "?" + query, timeout=90))
                if "error" in response:
                    raise ValueError(response["error"])
                features = response.get("features", [])
                for feature in features:
                    attr = feature.get("attributes") or {}
                    paths = (feature.get("geometry") or {}).get("paths") or []
                    points = [point for path in paths for point in path]
                    if not points:
                        continue
                    lats = [p[1] for p in points]
                    lons = [p[0] for p in points]
                    road = attr.get("National_r") or attr.get("Section_ID") or "Пътен участък"
                    all_rows.append((f"{layer}:{attr.get('FID')}",layer,str(road),str(attr.get("Risk_level") or ""),str(attr.get("Road_Condi") or ""),str(attr.get("Referance_") or ""),str(attr.get("District") or ""),min(lats),max(lats),min(lons),max(lons),json.dumps(paths,separators=(",", ":"))))
                offset += len(features)
                if len(features) < 1000:
                    break
            print(f"risk layer {layer}: {offset}")
        if len(all_rows) < 10000:
            raise ValueError(f"Incomplete risk snapshot: {len(all_rows)}")
        with db:
            db.execute("DELETE FROM risk")
            db.executemany("INSERT INTO risk VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",all_rows)
            record(db,"risk","ДАБДП · оценка на пътния риск",ARCGIS,len(all_rows))
        print(f"risk: {len(all_rows)}")
    except Exception as exc:
        with db:
            record(db,"risk","ДАБДП · оценка на пътния риск",ARCGIS,error=str(exc)[:220])
        print(f"risk: ERROR {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sources",nargs="*",choices=["toll","events","risk","mvr"],default=["toll","events","risk","mvr"])
    args = parser.parse_args()
    db = connect()
    if "toll" in args.sources: load_toll(db)
    if "events" in args.sources: load_events(db)
    if "risk" in args.sources: load_risk(db)
    if "mvr" in args.sources:
        # The published URL currently responds 403 to automated retrieval.
        # Keep this explicit instead of showing an invented or incomplete crash layer.
        with db:
            record(db,"crashes","МВР · катастрофи",MVR,error="Официалният ZIP източник отказва автоматично изтегляне (HTTP 403).")
        print("crashes: source unavailable (HTTP 403)")


if __name__ == "__main__": main()
