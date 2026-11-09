import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = 'naselenie'
DATA = Path(os.environ.get("NASELENIE_DATA", "/opt/naselenie/data"))
RAW = DATA / "raw"
# the shared database of Прозрачност; this tool owns the schemas naselenie_src and naselenie in it
DSN = os.environ.get("NASELENIE_DSN", "dbname=prozrachnost")
# the database this tool had on its own until 10.11.2026; read only by the steps replay and parity
OLD_DSN = os.environ.get("NASELENIE_OLD_DSN", "dbname=naselenie")

USER_AGENT = "Prozrachnost/naselenie (+https://github.com/DenislavDenev/prozrachnost)"
PAUSE = float(os.environ.get("NASELENIE_PAUSE", "2"))  # seconds between two requests to the same source
