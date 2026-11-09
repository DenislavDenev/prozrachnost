import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = 'danni'
DSN = os.environ.get('DANNI_DSN', 'dbname=prozrachnost')
DATA = Path(os.environ.get('DANNI_DATA', '/srv/prozrachnost/danni'))
RAW = DATA / 'raw'
# the archive of Наблюдател: EKATTE is read from there, not downloaded a second time
ARCHIVE = Path(os.environ.get('DANNI_ARCHIVE', '/srv/prozrachnost/nablyudatel'))
# until Тендер moves into the shared database its organisations are read from its own one
TENDER_DSN = os.environ.get('DANNI_TENDER_DSN', 'dbname=tender')
