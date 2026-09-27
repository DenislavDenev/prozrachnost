import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("IKONOMIKA_DATA", "/opt/ikonomika/data"))
RAW = DATA / "raw"
DSN = os.environ.get("IKONOMIKA_DSN", "dbname=ikonomika")

USER_AGENT = "Prozrachnost/ikonomika (+https://github.com/DenislavDenev/prozrachnost)"
EUROSTAT = os.environ.get("EUROSTAT_API", "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data")
BNB = os.environ.get("BNB_FX", "https://www.bnb.bg/Statistics/StExternalSector/StExchangeRates/StERForeignCurrencies/index.htm")
PAUSE = float(os.environ.get("IKONOMIKA_PAUSE", "2"))  # seconds between two requests to the same source

BGN_PER_EUR = 1.95583  # fixed conversion rate, euro adopted 2026-01-01
