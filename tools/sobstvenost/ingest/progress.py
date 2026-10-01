"""Private, atomic checkpoints for bounded real source reads."""
import datetime as dt
import json,time
from .config import DATA
from .store import digest,reconcile
from .parse import ShapeError

class BudgetReached(Exception):pass
class Deadline:
    def __init__(self,seconds=None):self.end=time.monotonic()+seconds if seconds is not None else None
    def require(self,seconds=0):
        if self.end is not None and time.monotonic()+seconds>=self.end:raise BudgetReached()

class Queue:
    def __init__(self,source,scope,rows,continue_pending=False):
        self.path=DATA/'checkpoints'/f'{source}-{scope}.json'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if continue_pending and self.path.exists():
            self.data=json.loads(self.path.read_text(encoding='utf-8'))
            rows=self.data['rows'];reconcile(rows,len(rows))
            if self.data['sha256']!=digest(rows) or not 0<=self.data['done']<=len(rows):raise ShapeError('corrupt source checkpoint')
            if dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(self.data['started_at'])>dt.timedelta(days=9):raise ShapeError('checkpoint older than freshness limit; explicit new full read required')
        else:
            if self.path.exists():self.path.rename(self.path.with_suffix(f'.superseded-{time.time_ns()}.json'))
            self.data=dict(rows=rows,done=0,sha256=digest(rows),started_at=dt.datetime.now(dt.timezone.utc).isoformat())
            self.save()
        self.rows=self.data['rows'];self.done=self.data['done']
    def save(self):
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8');tmp.replace(self.path)
    def advance(self):self.done+=1;self.data['done']=self.done;self.save()
    def complete(self):self.path.rename(self.path.with_suffix(f'.completed-{time.time_ns()}.json'))

def pending(source,scope):return (DATA/'checkpoints'/f'{source}-{scope}.json').exists()
