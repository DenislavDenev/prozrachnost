import time
import urllib.error
import urllib.request

from .config import PAUSE, USER_AGENT


class Gone(Exception):
    """The resource does not exist (403/404): a source gap or a renamed dataset, reported, not retried."""


_last = [0.0]


def get(url, timeout=120, retries=5, delay=30):
    """GET, one request at a time with PAUSE between them; on 429/5xx waits Retry-After (else 30 s, 1, 2, 4 min),
    then gives up. Raises Gone on 403/404."""
    for attempt in range(retries):
        time.sleep(max(0.0, _last[0] + PAUSE - time.monotonic()))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise Gone(f"{e.code} {url}") from e
            if e.code != 429 and e.code < 500:
                raise
            wait = int(e.headers.get("Retry-After") or delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            wait = delay
        finally:
            _last[0] = time.monotonic()
        if attempt == retries - 1:
            break
        time.sleep(wait)
        delay = min(delay * 2, 600)
    raise RuntimeError(f"giving up on {url} after {retries} attempts")
