import csv
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("SIGURNOST_DSN", "dbname=sigurnost")
DATA = Path(os.environ.get("SIGURNOST_DATA", str(ROOT / "data")))
# the archive of Наблюдател is the bronze layer of this tool. Nothing is downloaded here: data.egov.bg is read by the archive only.
ARCHIVE = Path(os.environ.get("SIGURNOST_ARCHIVE", "/opt/tender/arhiv"))
USER_AGENT = "Prozrachnost/sigurnost (+https://github.com/DenislavDenev/prozrachnost)"
LOCK = Path("/run/lock/prozrachnost-heavy.lock")

MAX_ARCHIVE_AGE_H = 30        # the build refuses an archive whose last good read of data.egov.bg is older
STALE_YEARS = 2               # "Полицейска статистика <year>" is yearly; a set not published for this long is reported as information
HOLD_ROWS = 0.9               # a new answer with fewer rows than this share of the one held is not taken until a second read says the same

LICENCES = {                  # terms_of_use_id of data.egov.bg -> (name, shared)
    "1": ("CC0 (без защитени авторски права)", True),
    "2": ("CC BY (признаване на авторските права)", True),
    "": ("не е посочен", False),
}


def datasets():
    """The sets of the Ministry of the Interior that this tool reads from the archive (db/ref/datasets.csv: uri, kind, year and
    the terms of use as data.egov.bg gave them on 02.10.2026)."""
    with open(ROOT / "db/ref/datasets.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
