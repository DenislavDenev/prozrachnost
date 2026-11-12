import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = "parlament"
DATA = Path(os.environ.get("PARLAMENT_DATA", "/opt/parlament/data"))
RAW = DATA / "raw"
# the shared database of Прозрачност; this tool owns the schemas parlament_src, parlament and pub_parlament
DSN = os.environ.get("PARLAMENT_DSN", "dbname=prozrachnost")
# the database this tool had on its own until 13.11.2026; read only by the steps replay and parity
OLD_DSN = os.environ.get("PARLAMENT_OLD_DSN", "dbname=parlament")
# the heavy steps of every tool wait for each other on CT 118 (a publication of the whole layer is one)
HEAVY_LOCK = os.environ.get("PZ_HEAVY_LOCK", "/run/lock/prozrachnost-heavy.lock")

USER_AGENT = "Prozrachnost/parlament (+https://github.com/DenislavDenev/prozrachnost)"
SITE = os.environ.get("PARLAMENT_SITE", "https://www.parliament.bg")
API = f"{SITE}/api/v1"
PAUSE = float(os.environ.get("PARLAMENT_PAUSE", "1.5"))  # seconds between two requests
