import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PAZAR_DSN", "dbname=pazar")
DATA = Path(os.environ.get("PAZAR_DATA", str(ROOT / "data")))
# the archive of Наблюдател: the bronze layer of this tool. Nothing is downloaded again.
ARCHIVE = Path(os.environ.get("PAZAR_ARCHIVE", "/opt/tender/arhiv"))
USER_AGENT = "Prozrachnost/pazar (+https://github.com/DenislavDenev)"
FIRST_DAY = "2025-10-16"
LOCK = Path("/run/lock/prozrachnost-heavy.lock")

BGN_PER_EUR = 1.95583        # fixed rate of the changeover
MAX_ARCHIVE_AGE_H = 30       # the build refuses an archive whose last good read is older
HOLD_CHAINS = 0.8            # a day with fewer files than this share of the previous built day is held
HOLD_ROWS = 0.7              # ... or with fewer valid rows than this share
