import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("NASELENIE_DATA", "/opt/naselenie/data"))
RAW = DATA / "raw"
DSN = os.environ.get("NASELENIE_DSN", "dbname=naselenie")

USER_AGENT = "Prozrachnost/naselenie (+https://github.com/DenislavDenev/prozrachnost)"
EUROSTAT = os.environ.get("EUROSTAT_API", "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data")
ECB = os.environ.get("ECB_API", "https://data-api.ecb.europa.eu/service/data")
EGOV = os.environ.get("EGOV_API", "https://data.egov.bg/api")
BNB = os.environ.get("BNB_FX", "https://www.bnb.bg/Statistics/StExternalSector/StExchangeRates/StERForeignCurrencies/index.htm")
PAUSE = float(os.environ.get("NASELENIE_PAUSE", "2"))  # seconds between two requests to the same source

BGN_PER_EUR = 1.95583  # fixed conversion rate, euro adopted 2026-01-01
