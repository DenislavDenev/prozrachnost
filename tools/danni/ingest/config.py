import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = 'danni'
DSN = os.environ.get('DANNI_DSN', 'dbname=prozrachnost')
DATA = Path(os.environ.get('DANNI_DATA', '/srv/prozrachnost/danni'))
RAW = DATA / 'raw'
# the archive of Наблюдател: EKATTE is read from there, not downloaded a second time
ARCHIVE = Path(os.environ.get('DANNI_ARCHIVE', '/srv/prozrachnost/nablyudatel'))
# the organisations come from the clean tables of Тендер: schema tender of this database since its move here
# (until 10.11.2026 its own database: DANNI_TENDER_DSN=dbname=tender DANNI_TENDER_SCHEMA=live, also the way back)
TENDER_DSN = os.environ.get('DANNI_TENDER_DSN', DSN)
TENDER_SCHEMA = os.environ.get('DANNI_TENDER_SCHEMA', 'tender')
