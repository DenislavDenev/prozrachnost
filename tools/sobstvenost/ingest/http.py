import time
import email.utils
import datetime as dt
import requests
from .config import USER_AGENT

class Client:
    def __init__(self,deadline=None):
        self.deadline=deadline
        self.session=requests.Session();self.session.headers['User-Agent']=USER_AGENT;self.last=0;self.raws={}
    def get(self,url,data=None):
        for attempt,delay in enumerate([30,120,600,0]):
            if self.deadline:self.deadline.require(90)
            pause=8 if 'data.egov.bg' in url else 2 if '/Download/' in url else 1.1
            time.sleep(max(0,self.last+pause-time.monotonic()))
            try:
                r=self.session.post(url,data=data,timeout=90,stream=True) if data is not None else self.session.get(url,timeout=90,stream=True)
                self.last=time.monotonic()
                if r.status_code != 429 and r.status_code < 500:
                    r.raise_for_status()
                    if not hasattr(r,'iter_content'):return r.content
                    chunks=[]
                    try:
                        for chunk in r.iter_content(65536):
                            if self.deadline:self.deadline.require()
                            chunks.append(chunk)
                        return b''.join(chunks)
                    finally:r.close()
                retry=r.headers.get('Retry-After')
                if retry:
                    try:delay=max(0,float(retry))
                    except ValueError:
                        try:delay=max(0,(email.utils.parsedate_to_datetime(retry)-dt.datetime.now(dt.timezone.utc)).total_seconds())
                        except (ValueError,TypeError):pass
            except requests.exceptions.SSLError:
                raise
            except (requests.Timeout,requests.ConnectionError):
                self.last=time.monotonic()
            if attempt==3:raise RuntimeError('source repeatedly failed '+url)
            if self.deadline:self.deadline.require(delay+90)
            time.sleep(delay)
