import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("UCHILISHTA_DATA", "/srv/prozrachnost/uchilishta"))
RAW = DATA / "raw"
DSN = os.environ.get("UCHILISHTA_DSN", "dbname=uchilishta")
USER_AGENT = "Prozrachnost/uchilishta (+https://github.com/DenislavDenev/prozrachnost)"
EGOV = os.environ.get("UCHILISHTA_EGOV", "https://data.egov.bg/api/")
