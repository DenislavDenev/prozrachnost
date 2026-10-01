import datetime as dt
import email.utils
import time
import httpx
from .config import USER_AGENT

class Failed(RuntimeError):
    pass

class Client:
    def __init__(self, transport=None, pause=2, sleep=time.sleep):
        self.client = httpx.Client(headers={'User-Agent': USER_AGENT}, timeout=90,
                                  transport=transport, follow_redirects=True)
        self.pause, self.sleep, self.last = pause, sleep, 0

    def close(self):
        self.client.close()

    def request(self, method, url, **kw):
        for attempt in range(4):
            self.sleep(max(0, self.last + self.pause - time.monotonic()))
            try:
                r = self.client.request(method, url, **kw)
            except httpx.HTTPError as e:
                raise Failed('Грешка при четене: ' + str(e)) from e
            finally:
                self.last = time.monotonic()
            if r.status_code == 403:
                raise Failed('403: отказан достъп, не се заобикаля защитата')
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 3:
                    raise Failed('HTTP ' + str(r.status_code))
                value, delay = r.headers.get('Retry-After'), [30, 120, 600][attempt]
                if value:
                    try:
                        delay = max(0, float(value))
                    except ValueError:
                        try:
                            delay = max(0, (email.utils.parsedate_to_datetime(value) - dt.datetime.now(dt.timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                self.sleep(delay)
                continue
            if r.status_code != 200:
                raise Failed('HTTP ' + str(r.status_code))
            return r.content

    def get(self, url):
        return self.request('GET', url)
