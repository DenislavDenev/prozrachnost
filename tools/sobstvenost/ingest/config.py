import os
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
DSN=os.environ.get('SOBSTVENOST_DSN','dbname=sobstvenost')
DATA=Path(os.environ.get('SOBSTVENOST_DATA',str(ROOT/'data')))
USER_AGENT='Prozrachnost/sobstvenost (+https://github.com/DenislavDenev/prozrachnost)'
