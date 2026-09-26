import unittest

from ingest import parse_datex


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


if __name__ == "__main__":
    unittest.main()
