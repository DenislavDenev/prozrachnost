import datetime as dt
from .config import SOURCES

def freshness(c, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    resources = {r[0]: r[1:] for r in c.execute('SELECT source,read_at,status,error FROM src.resource')}
    problems = []
    for source, (label, _) in SOURCES.items():
        r = resources.get(source)
        limit = 3 if source == 'cpc' else 14
        if not r or r[0] is None:
            problems.append(label + ': няма доказан пълен прочит')
        elif r[1] != 'наред':
            problems.append(label + ': ' + r[1] + (': ' + r[2] if r[2] else ''))
        elif now - r[0] > dt.timedelta(days=limit):
            problems.append(label + ': остаряло четене')
    for source, first in c.execute('SELECT source,first_seen FROM ops.held'):
        if now - first >= dt.timedelta(days=1):
            problems.append(source + ': задържане над ден')
    return problems
