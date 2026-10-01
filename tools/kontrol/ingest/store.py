import datetime as dt
import hashlib
import json
from pathlib import Path
from psycopg.types.json import Jsonb
from .config import DATA, SOURCES
from .parse import ShapeError, canonical

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def encode(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()

def archive(c, source, url, raw, root=None):
    sha = digest(raw)
    path = Path(root or DATA) / 'raw' / source / (sha + '.bin')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    elif digest(path.read_bytes()) != sha:
        raise ShapeError('Повреден неизменяем суров архив, няма презаписване')
    if c is not None:
        c.execute('INSERT INTO ops.raw_file(source,url,path,sha256,bytes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                  (source, url, str(path), sha, len(raw)))
    return sha

def log(c, source, ref, field, old, new, cause):
    c.execute('INSERT INTO ops.change_log(source,ref,field,old,new,cause) VALUES (%s,%s,%s,%s,%s,%s)',
              (source, ref, field, None if old is None else str(old), None if new is None else str(new), cause))

def should_hold(old, new):
    a = {r['id']: r for r in old}
    b = {r['id']: r for r in new}
    def loss(before, after):
        if before not in (None, '', [], {}) and after in (None, '', [], {}):
            return True
        if isinstance(before, dict) and isinstance(after, dict):
            return any(k not in after or loss(v, after[k]) for k,v in before.items())
        if isinstance(before, list) and isinstance(after, list):
            return any(encode(v) not in {encode(x) for x in after} for v in before)
        return False
    return any(key not in b or loss(row, b[key]) for key,row in a.items())

def confirmed(held, sha, now):
    return bool(held and held[0] == sha and now - held[1] >= dt.timedelta(days=1))

def publish(c, source, rows, manifest, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    # Reconcile occurrence count before canonical category deduplication.
    if not manifest.get('complete') or manifest.get('occurrence_count') != len(rows):
        raise ShapeError('Непълен обхват или неуспешна сверка по брой')
    if manifest.get('source_count') is not None and manifest['source_count'] != len(rows):
        raise ShapeError('Броячът на източника не съвпада')
    rows = canonical([{k:v for k,v in r.items() if k not in ('pdf_event','document_event')} for r in rows])
    # Public idempotency is semantic. Hold confirmation is deliberately stricter:
    # it requires the same archived original response fingerprint, including the
    # ASP.NET/CSRF envelope. Volatile envelopes may keep a loss held indefinitely.
    answer_sha = manifest.get('answer_sha256')
    if not answer_sha:
        raise ShapeError('Липсва хеш на оригиналния пълен отговор')
    public_manifest = {k:v for k,v in manifest.items() if k != 'answer_sha256'}
    data = dict(rows=rows, manifest=public_manifest)
    sha = digest(encode(data))
    c.execute("INSERT INTO src.resource(source,url,read_at,status,row_count,page_count,source_count,scope) VALUES (%s,%s,%s,'наред',%s,%s,%s,%s) ON CONFLICT(source) DO UPDATE SET read_at=excluded.read_at,row_count=excluded.row_count,page_count=excluded.page_count,source_count=excluded.source_count,scope=excluded.scope", 
              (source, SOURCES[source][1], now, len(rows), manifest['pages'], manifest.get('source_count'), manifest['scope']))
    previous = c.execute('SELECT payload,sha256 FROM live.snapshot WHERE source=%s', (source,)).fetchone()
    old = previous[0]['rows'] if previous else []
    held = c.execute('SELECT sha256,first_seen FROM ops.held WHERE source=%s', (source,)).fetchone()
    if previous and previous[1] == sha:
        c.execute("UPDATE src.resource SET status='наред',error=NULL WHERE source=%s", (source,))
        c.execute('DELETE FROM ops.held WHERE source=%s', (source,))
        return 'unchanged'
    if old and should_hold(old, rows) and not confirmed(held, answer_sha, now):
        if not held or held[0] != answer_sha:
            c.execute('INSERT INTO ops.held VALUES (%s,%s,%s) ON CONFLICT(source) DO UPDATE SET sha256=excluded.sha256,first_seen=excluded.first_seen', (source, answer_sha, now))
            log(c, source, source, None, previous[1], answer_sha, 'held')
        c.execute("UPDATE src.resource SET status='задържан до второ четене',error=NULL WHERE source=%s", (source,))
        return 'held'
    with c.transaction():
        c.execute('INSERT INTO stage.snapshot(source,payload,sha256) VALUES (%s,%s,%s) ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,sha256=excluded.sha256,published_at=now()', (source, Jsonb(data), sha))
        a, b = {r['id']: r for r in old}, {r['id']: r for r in rows}
        for key in sorted(a.keys() | b.keys()):
            before, after = a.get(key, {}), b.get(key, {})
            for field in sorted(before.keys() | after.keys()):
                if before.get(field) != after.get(field):
                    cause = 'confirmed' if held else 'new-record' if not before else 'removed' if not after else 'rewritten'
                    log(c, source, key, field, before.get(field), after.get(field), cause)
            if after:
                version = digest(encode(after))
                c.execute('INSERT INTO ops.version(source,ref,sha256,payload) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', (source, key, version, Jsonb(after)))
        c.execute('INSERT INTO live.snapshot SELECT * FROM stage.snapshot WHERE source=%s ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,sha256=excluded.sha256,published_at=excluded.published_at', (source,))
        c.execute('DELETE FROM stage.snapshot WHERE source=%s', (source,))
        c.execute('DELETE FROM ops.held WHERE source=%s', (source,))
        c.execute("UPDATE src.resource SET status='наред',error=NULL WHERE source=%s", (source,))
    return 'stored'

def failure(c, source, error):
    c.execute("INSERT INTO src.resource(source,url,status,error) VALUES (%s,%s,'неуспешно четене',%s) ON CONFLICT(source) DO UPDATE SET status=excluded.status,error=excluded.error", (source, SOURCES[source][1], str(error)))
    log(c, source, source, None, None, str(error), 'invalid')
