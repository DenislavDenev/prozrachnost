import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = 'sobstvenost'
DATA = Path(os.environ.get('SOBSTVENOST_DATA', '/srv/prozrachnost/sobstvenost'))
RAW = DATA / 'raw'
# the shared database of Прозрачност; this tool owns the schemas sobstvenost_src, sobstvenost and pub_sobstvenost in it
DSN = os.environ.get('SOBSTVENOST_DSN', 'dbname=prozrachnost')
# the database of its own until 24.11.2026 that held the data (the corrected backfill of 02.10.2026); read only by the
# steps replay and parity. sobstvenost itself was migrated and never filled, sobstvenost_preview is the first backfill.
OLD_DSN = os.environ.get('SOBSTVENOST_OLD_DSN', 'dbname=sobstvenost_verified')
USER_AGENT = 'Prozrachnost/sobstvenost (+https://github.com/DenislavDenev/prozrachnost)'
SOURCES = {
    'appk': ('АППК, каталогът на предприятията и министерствата', 'https://reports.appk.government.bg/Public/Public/Companies'),
    'ncr': ('НКР, търсенето на всички партиди', 'https://ncr.government.bg/Concessions'),
}
