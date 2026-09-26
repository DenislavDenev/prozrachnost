import unittest

from ingest import parse_datex, parse_mvr_csv


class DatexTests(unittest.TestCase):
    def test_utf8_payload_with_false_utf16_declaration(self):
        xml = '''<?xml version="1.0" encoding="UTF-16"?>
        <d2LogicalModel xmlns="http://datex2.eu/schema/2/2_0">
          <situation id="s-1"><situationRecord id="r-1">
            <validity><validityTimeSpecification><overallStartTime>2026-09-26T08:00:00+0300</overallStartTime></validityTimeSpecification></validity>
            <generalPublicComment><comment><values><value lang="bg">Затворен път</value><value lang="bg">Ремонт</value></values></comment></generalPublicComment>
            <groupOfLocations><pointByCoordinates><pointCoordinates><latitude>42.1</latitude><longitude>23.4</longitude></pointCoordinates></pointByCoordinates></groupOfLocations>
          </situationRecord></situation>
        </d2LogicalModel>'''.encode("utf-8")
        rows = parse_datex(xml, "Затворен път", "https://example.org/source.xml")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:6], ("r-1", "Затворен път", "Затворен път", "Ремонт", 42.1, 23.4))
        self.assertEqual(rows[0][6], "2026-09-26T08:00:00+0300")

    def test_mvr_skips_rows_without_coordinates(self):
        csv_data = ("diedcount,injuredcount,crashtype,crashdatetime,latitude,longitude\n"
                    "1,2,Сблъсък,2026-09-26 10:00:00,42.5,23.3\n"
                    "0,0,ПТП,2026-09-26 11:00:00,,\n").encode("utf-8-sig")
        rows, skipped = parse_mvr_csv(csv_data)
        self.assertEqual((len(rows), skipped), (1, 1))
        self.assertEqual(rows[0][5:], (1, 2))


if __name__ == "__main__":
    unittest.main()
