from ingest.run import historical_years


def test_history_intersects_catalogs_without_latest_year():
    exams = {year: object() for year in ("2018/2019", "2021/2022", "2022/2023", "2025/2026")}
    registers = {year: object() for year in ("2018/2019", "2021/2022", "2025/2026")}
    assert historical_years(exams, registers) == ["2021/2022"]
