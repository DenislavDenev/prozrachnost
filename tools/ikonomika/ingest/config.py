import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("IKONOMIKA_DATA", "/opt/ikonomika/data"))
RAW = DATA / "raw"
TOOL = "ikonomika"
# the shared database of Прозрачност; this tool owns the schemas ikonomika_src, ikonomika and pub_ikonomika in it
DSN = os.environ.get("IKONOMIKA_DSN", "dbname=prozrachnost")
# the database this tool had on its own until 11.11.2026; read only by the steps replay and parity
OLD_DSN = os.environ.get("IKONOMIKA_OLD_DSN", "dbname=ikonomika")

USER_AGENT = "Prozrachnost/ikonomika (+https://github.com/DenislavDenev/prozrachnost)"
EUROSTAT = os.environ.get("EUROSTAT_API", "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data")
ECB = os.environ.get("ECB_API", "https://data-api.ecb.europa.eu/service/data")
EGOV = os.environ.get("EGOV_API", "https://data.egov.bg/api")
BNB = os.environ.get("BNB_FX", "https://www.bnb.bg/Statistics/StExternalSector/StExchangeRates/StERForeignCurrencies/index.htm")
PAUSE = float(os.environ.get("IKONOMIKA_PAUSE", "2"))  # seconds between two requests to the same source

BGN_PER_EUR = 1.95583  # fixed conversion rate, euro adopted 2026-01-01
