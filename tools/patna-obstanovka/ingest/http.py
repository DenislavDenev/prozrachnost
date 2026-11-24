"""One request at a time to each host, with a pause between them and an honest User-Agent.

A refused connection, a timeout, 429 and 5xx (Cloudflare's 525 among them) are waited out (Retry-After when the
source gives one) and asked again a few times; after that the read fails and the caller records it as rejected.
403 and 404 are not asked again at once: the source refused, and the next scheduled read tries again."""
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import HOST_PAUSE, PAUSE, USER_AGENT


class Refused(Exception):
    """403 or 404: the source refused the request or the resource is gone."""


_last = {}


def get(url, timeout=90, retries=4, delay=20, data=None, headers=None, sleep=time.sleep):
    host = urllib.parse.urlsplit(url).hostname or ''
    pause = HOST_PAUSE.get(host, PAUSE)
    last_error = None
    for attempt in range(retries):
        sleep(max(0.0, _last.get(host, -1e9) + pause - time.monotonic()))
        req = urllib.request.Request(url, data=data, headers={'User-Agent': USER_AGENT, 'Accept': '*/*', **(headers or {})})
        wait = delay
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise Refused(f'HTTP {e.code} {url}') from e
            if e.code != 429 and e.code < 500:
                raise
            last_error = f'HTTP {e.code}'
            after = e.headers.get('Retry-After') or ''
            wait = int(after) if after.isdigit() else delay
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_error = str(getattr(e, 'reason', e))
        finally:
            _last[host] = time.monotonic()
        if attempt < retries - 1:
            sleep(wait)
            delay = min(delay * 2, 300)
    raise RuntimeError(f'{last_error} след {retries} опита: {url}')
