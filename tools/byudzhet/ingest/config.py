import os
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
TOOL='byudzhet'
DATA=Path(os.environ.get('BYUDZHET_DATA',str(ROOT/'data')))
RAW=DATA/'raw'
# the shared database of Прозрачност; this tool owns the schemas byudzhet_src, byudzhet and pub_byudzhet in it
DSN=os.environ.get('BYUDZHET_DSN','dbname=prozrachnost')
# the database this tool had on its own until 11.11.2026; read only by the steps replay and parity
OLD_DSN=os.environ.get('BYUDZHET_OLD_DSN','dbname=byudzhet')
SETS={'state':'79ce7de2-0150-4ba7-a96c-dbacb76c95b6','kfp':'bfcdd4cb-4737-4272-92ea-9b2395f7cb14','indicators':'980fa747-e0d0-4371-9457-f41d730040cd','debt':'ee08391e-be09-44c1-b278-b4af8b62a147','reserve':'3178b3ee-cfe8-4adc-b716-659d3228407f'}
USER_AGENT='Prozrachnost/byudzhet (+https://github.com/DenislavDenev)'

SOURCE_NAMES={'state':'Държавен бюджет','kfp':'КФП','reserve':'Фискален резерв','debt':'Общински дълг','indicators':'Финансови показатели','population':'ГРАО'}
