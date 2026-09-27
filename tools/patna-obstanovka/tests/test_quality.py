import sqlite3
import unittest
from datetime import datetime, timezone

from ingest import toll_time
from refresh import check


class QualityTests(unittest.TestCase):
    def test_bgtoll_clock_is_utc(self):
        self.assertEqual(toll_time("2026-09-26 21:06:00"), "2026-09-26T21:06:00+00:00")
        self.assertEqual(datetime.fromisoformat(toll_time("2026-09-26 21:06:00")).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Sofia')).strftime('%Y-%m-%d %H:%M'), "2026-09-27 00:06")

    def test_old_measurement_fails_even_after_new_fetch(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE sources(key TEXT,updated TEXT,count INTEGER,error TEXT)")
        db.execute("CREATE TABLE traffic(observed TEXT)")
        db.execute("CREATE TABLE weather(observed TEXT)")
        moment = datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)
        for key in ("traffic", "weather", "lima_r01", "lima_r02", "lima_d01"):
            db.execute("INSERT INTO sources VALUES(?,?,?,NULL)", (key, moment.isoformat(), 1))
        db.execute("INSERT INTO traffic VALUES('2026-09-27T05:00:00+00:00')")
        db.execute("INSERT INTO weather VALUES('2026-09-27T06:50:00+00:00')")
        result = check(db, "current", moment)
        self.assertFalse(result[0]["ok"])
        self.assertTrue(result[1]["ok"])


if __name__ == "__main__":
    unittest.main()
