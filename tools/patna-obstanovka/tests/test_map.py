import gzip
import json
import tempfile
import unittest
from pathlib import Path

import db
from app import risk_payload, road_conditions, crash_summary, dangerous_sections
from traffic_bearing import refresh_bearings


class CompleteRiskTests(unittest.TestCase):
    def test_complete_geojson_has_every_assessed_section(self):
        original = db.DB
        connection = None
        with tempfile.TemporaryDirectory() as directory:
            try:
                db.DB = Path(directory) / "test.db"
                connection = db.connect()
                with connection:
                    connection.executemany(
                        "INSERT INTO risk VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            ("5:1",5,"I-1","HIGH","","2023","",42,43,23,24,"[[[23,42],[24,43]]]",1.2),
                            ("5:2",5,"I-2","LOW","","2023","",42,43,24,25,"[[[24,42],[25,43]]]",2.3),
                        ],
                    )
                connection.close()
                risk_payload.cache_clear()
                plain, compressed = risk_payload("fixture")
                features = json.loads(plain)["features"]
                self.assertEqual(len(features), 2)
                self.assertEqual({f["properties"]["road"] for f in features}, {"I-1", "I-2"})
                self.assertEqual(gzip.decompress(compressed), plain)
            finally:
                risk_payload.cache_clear()
                db.DB = original


class MapDataTests(unittest.TestCase):
    def test_length_shares_severity_and_direction_bearings(self):
        original = db.DB
        with tempfile.TemporaryDirectory() as directory:
            try:
                db.DB = Path(directory) / "map.db"
                connection = db.connect()
                with connection:
                    connection.executemany("INSERT INTO risk VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                        ("5:1",5,"I-1","HIGH","GOOD","2023","",42.4,42.6,23.5,23.5,"[[[23.5,42.4],[23.5,42.6]]]",10),
                        ("5:2",5,"I-2","LOW","BAD","2023","",42.5,42.5,23.4,23.6,"[[[23.4,42.5],[23.6,42.5]]]",1),
                    ])
                    connection.executemany("INSERT INTO traffic VALUES(?,?,?,?,?,?,?,?,?)", [
                        ("12341","Станция",42.45,23.5,4,16,None,"2026-09-27T08:00:00+00:00",None),
                        ("12342","Станция",42.45,23.5,5,20,None,"2026-09-27T08:00:00+00:00",None),
                        ("56781","Друга",42.5,23.55,6,24,None,"2026-09-27T08:00:00+00:00",None),
                    ])
                    connection.executemany("INSERT INTO crashes VALUES(?,?,?,?,?,?,?)", [
                        ("a","2026-09-26",42.45,23.5,"ПТП",1,0),
                        ("b","2026-09-26",42.4501,23.5001,"ПТП",0,2),
                        ("c","2026-09-26",42.4502,23.5002,"ПТП",0,0),
                    ])
                    connection.executemany("INSERT INTO crash_matches VALUES(?,?,?)", [("a","5:1",2),("b","5:1",3),("c","5:2",4)])
                self.assertEqual(refresh_bearings(connection), 3)
                bearings = {row[0]: row[1] for row in connection.execute("SELECT scp,bearing FROM traffic")}
                self.assertLess(min(bearings["12341"],360-bearings["12341"]), 15)
                self.assertLess(abs(bearings["12342"]-180), 15)
                self.assertLess(abs(bearings["56781"]-90), 15)
                stats = road_conditions()
                self.assertEqual(stats["total_km"], 11)
                self.assertEqual(stats["conditions"][0]["condition"], "GOOD")
                summary = crash_summary(zoom=7,period="all",south=42.4,north=42.6,west=23.4,east=23.6)
                self.assertEqual(summary["total"], 3)
                self.assertEqual(sum(feature["properties"]["fatal"] for feature in summary["features"]), 1)
                self.assertEqual(sum(feature["properties"]["injury"] for feature in summary["features"]), 1)
                self.assertEqual(len(dangerous_sections("all", "GOOD")["features"]), 1)
                connection.close()
                connection = None
            finally:
                if connection is not None:
                    connection.close()
                db.DB = original


class FeedbackTests(unittest.TestCase):
    def test_feedback_last_in_header_and_support_last_in_footer(self):
        from app import home
        html = home()
        self.assertNotIn("{{", html)
        self.assertTrue(html.split("</header>")[0].endswith("</script></div>"))
        self.assertTrue(html.split("</footer>")[0].endswith('href="http://localhost:8001/podkrepi">Подкрепи проекта</a>'))


if __name__ == "__main__":
    unittest.main()
