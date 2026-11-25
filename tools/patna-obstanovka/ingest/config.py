import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = 'patna-obstanovka'
DATA = Path(os.environ.get('PATNA_OBSTANOVKA_DATA', '/srv/prozrachnost/patna-obstanovka'))
RAW = DATA / 'raw'
# the shared database of Прозрачност; this tool owns the schemas patna_obstanovka_src, patna_obstanovka and
# pub_patna_obstanovka in it
DSN = os.environ.get('PATNA_OBSTANOVKA_DSN', 'dbname=prozrachnost')
# the SQLite file this tool had on its own until 24.11.2026; read only, by the steps copy and parity
OLD_DB = Path(os.environ.get('PATNA_OBSTANOVKA_OLD_DB', '/databases/prozrachnost/patna-obstanovka/road.db'))

USER_AGENT = 'Prozrachnost/patna-obstanovka (+https://github.com/DenislavDenev/prozrachnost)'
# seconds between two requests to the same host. The server of АПИ (datasheet.api.bg) refuses connections for a few
# seconds after a quick series of requests ("No route to host"), so it gets a longer pause.
PAUSE = float(os.environ.get('PATNA_OBSTANOVKA_PAUSE', '2'))
HOST_PAUSE = {'datasheet.api.bg': float(os.environ.get('PATNA_OBSTANOVKA_LIMA_PAUSE', '8'))}
