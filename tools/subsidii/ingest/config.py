import os
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("SUBSIDII_DSN", "dbname=subsidii")
DATA = Path(os.environ.get("SUBSIDII_DATA", str(ROOT / "data")))
# the archive of Наблюдател: the bronze layer of this tool. Nothing is downloaded here, the source is never asked.
ARCHIVE = Path(os.environ.get("SUBSIDII_ARCHIVE", "/opt/tender/arhiv"))
USER_AGENT = "Prozrachnost/subsidii (+https://github.com/DenislavDenev/prozrachnost)"
LOCK = Path("/run/lock/prozrachnost-heavy.lock")

BGN_PER_EUR = Decimal("1.95583")      # the fixed rate of the changeover
MAX_ARCHIVE_AGE_H = 8 * 24            # the build refuses an archive whose last good read is older (the source is read on Mondays)
HOLD_ROWS = Decimal("0.9")            # a file with fewer rows than this share of the previous one is held
HOLD_SUM = Decimal("0.9")             # ... or with a smaller total than this share
MAX_BLOCK_DIFF = Decimal("0.002")     # share of recipients whose rows do not add up to their own ОБЩО row in the source (26 of 54942 on 28.09.2026) ...
MIN_BLOCK_DIFF = 5                    # ... but never fewer than this many, so a small file is not judged by a share; above the limit: invalid
