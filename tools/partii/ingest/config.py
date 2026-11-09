import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

TOOL = "partii"
ROOT = Path(__file__).resolve().parent.parent
# the shared database of Прозрачност: this tool writes only in its schemas partii_src and partii
DSN = os.environ.get("PARTII_DSN", "dbname=prozrachnost")
DATA = Path(os.environ.get("PARTII_DATA", str(ROOT / "data")))
# the archive of Наблюдател is the raw layer of this tool. ЕРИК is read once a day by the archive, never from here.
ARCHIVE = Path(os.environ.get("PARTII_ARCHIVE", "/srv/prozrachnost/nablyudatel"))

BGN_PER_EUR = Decimal("1.95583")      # the fixed rate of the changeover
EURO_FROM = dt.date(2026, 1, 1)       # reports of elections before this day were filed in leva
MAX_ARCHIVE_AGE_H = 30                # the build refuses an archive whose last good read is older
RECENT = 6                            # the archive reads the first 6 elections of the list daily, the rest on Sunday
CONFIRM_H = 20                        # a smaller answer is accepted when a read of its address this much later still gives it
SUM_TOLERANCE = Decimal("0.000001")   # the totals of the source are floats: the same number to the 16th digit
