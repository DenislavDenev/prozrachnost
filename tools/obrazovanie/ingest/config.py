import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("OBRAZOVANIE_DATA", "/srv/prozrachnost/obrazovanie"))
RAW = DATA / "raw"
DSN = os.environ.get("OBRAZOVANIE_DSN", "dbname=obrazovanie")
USER_AGENT = "Prozrachnost/obrazovanie (+https://github.com/DenislavDenev/prozrachnost)"
EGOV = os.environ.get("OBRAZOVANIE_EGOV", "https://data.egov.bg/api/")
