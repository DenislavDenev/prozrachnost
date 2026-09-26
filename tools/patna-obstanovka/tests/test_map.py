import gzip
import json
import tempfile
import unittest
from pathlib import Path

import db
from app import risk_payload


class CompleteRiskTests(unittest.TestCase):
    def test_complete_geojson_has_every_assessed_section(self):
        original = db.DB
        with tempfile.TemporaryDirectory() as directory:
            try:
                db.DB = Path(directory) / "test.db"
                connection = db.connect()
                with connection:
                    connection.executemany(
                        "INSERT INTO risk VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            ("5:1",5,"I-1","HIGH","","2023","",42,43,23,24,"[[[23,42],[24,43]]]"),
                            ("5:2",5,"I-2","LOW","","2023","",42,43,24,25,"[[[24,42],[25,43]]]"),
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


if __name__ == "__main__":
    unittest.main()
