import os
from pathlib import Path

TOOL = "zakoni"
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("ZAKONI_DATA", "/srv/prozrachnost/zakoni"))
RAW = DATA / "raw"                       # only db/ref/institution_municipality.csv is stored here; the data files stay in the archive
DSN = os.environ.get("ZAKONI_DSN", "dbname=prozrachnost")
# the database of its own, before the move into the shared one (10.11.2026 on); read only, by `replay` and `parity`
OLD_DSN = os.environ.get("ZAKONI_OLD_DSN", "dbname=zakoni")

# The raw files are the daily reads of the Наблюдател archive; this tool never calls data.egov.bg.
ARCHIVE = Path(os.environ.get("ZAKONI_ARCHIVE", "/srv/prozrachnost/nablyudatel"))
SET_ID = "18da0fff-79b2-45c6-a9af-1509df96261b"
SET_URL = f"https://data.egov.bg/data/view/{SET_ID}"
SET_DIR = ARCHIVE / "egov" / SET_ID
STATE_FILE = ARCHIVE / "state" / "egov.json"
STALE_HOURS = 30          # the archive reads data.egov.bg every day at 02:50 UTC; an older read is refused

EUR_PER_BGN = 1 / 1.95583   # the fixed rate; the original amount is kept next to the converted one
