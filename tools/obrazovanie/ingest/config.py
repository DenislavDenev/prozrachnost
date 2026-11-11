import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOL = "obrazovanie"
DATA = Path(os.environ.get("OBRAZOVANIE_DATA", "/srv/prozrachnost/obrazovanie"))
RAW = DATA / "raw"
# the shared database of Прозрачност; this tool owns the schemas obrazovanie_src, obrazovanie and pub_obrazovanie
DSN = os.environ.get("OBRAZOVANIE_DSN", "dbname=prozrachnost")
# the database this tool had on its own until 11.11.2026; read only by the steps replay and parity
OLD_DSN = os.environ.get("OBRAZOVANIE_OLD_DSN", "dbname=obrazovanie")
USER_AGENT = "Prozrachnost/obrazovanie (+https://github.com/DenislavDenev/prozrachnost)"
EGOV = os.environ.get("OBRAZOVANIE_EGOV", "https://data.egov.bg/api/")
