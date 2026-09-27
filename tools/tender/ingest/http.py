import time
import urllib.error
import urllib.request

from .config import USER_AGENT


class Gone(Exception):
    """The resource does not exist (403/404): a source gap, not a failure."""


def get(url, accept="*/*", timeout=120, retries=5, delay=5):
    """GET with backoff on 429/5xx; honours Retry-After. Raises Gone on 403/404."""
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise Gone(url) from e
            if e.code != 429 and e.code < 500:
                raise
            wait = int(e.headers.get("Retry-After") or delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            wait = delay
        if attempt == retries - 1:
            break
        time.sleep(wait)
        delay = min(delay * 2, 300)
    raise RuntimeError(f"giving up on {url} after {retries} attempts")
