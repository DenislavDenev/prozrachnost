import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("PARLAMENT_DATA", "/opt/parlament/data"))
RAW = DATA / "raw"
DSN = os.environ.get("PARLAMENT_DSN", "dbname=parlament")

USER_AGENT = "Prozrachnost/parlament (+https://github.com/DenislavDenev/prozrachnost)"
SITE = os.environ.get("PARLAMENT_SITE", "https://www.parliament.bg")
API = f"{SITE}/api/v1"
PAUSE = float(os.environ.get("PARLAMENT_PAUSE", "1.5"))  # seconds between two requests
