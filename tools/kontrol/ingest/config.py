import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('KONTROL_DATA', '/srv/prozrachnost/kontrol'))
DSN = os.environ.get('KONTROL_DSN', 'dbname=kontrol')
USER_AGENT = 'Prozrachnost/kontrol (+https://github.com/DenislavDenev/prozrachnost)'
SOURCES = {
    'nao': ('Сметна палата, доклади', 'https://www.bulnao.government.bg/bg/oditna-dejnost/dokladi/'),
    'nao-mun': ('Сметна палата, общини', 'https://www.bulnao.government.bg/bg/oditna-dejnost/dokladi-obshini/'),
    'recommendations': ('Изпълнение на препоръките', 'https://www.bulnao.government.bg/bg/oditna-dejnost/izpylnenie-na-preporykite/'),
    'recommendations-mun': ('Препоръки за общини', 'https://www.bulnao.government.bg/bg/oditna-dejnost/izpylnenie-na-preporykite-obshtini/'),
    'nao-crim': ('Доклади след наказателни производства', 'https://www.bulnao.government.bg/bg/oditna-dejnost/dokladi-sled-priklyuchili-nakazatelni-proizvodstva/'),
    'adfi-history': ('АДФИ, исторически списъци', 'https://www.adfi.minfin.bg/bg/18'),
    'adfi': ('АДФИ, публични доклади', 'https://www.adfi.minfin.bg/bg/34'),
    'cpc': ('КЗК, решения', 'https://reg.cpc.bg/Search.aspx'),
}
