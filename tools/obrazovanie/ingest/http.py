"""Polite single-connection access to the official data portal."""

import json
import time
import urllib.error
import urllib.request

from .config import EGOV, USER_AGENT


_last = [0.0]


def post(method: str, payload: dict) -> bytes:
    if method not in {"listResources", "getResourceData"}:
        raise ValueError("Unknown MON API method")
    body = json.dumps(payload).encode()
    delay = 30
    for attempt in range(4):
        time.sleep(max(0, _last[0] + 8 - time.monotonic()))
        request = urllib.request.Request(
            EGOV + method, data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            retry = exc.headers.get("Retry-After")
            wait = int(retry) if retry and retry.isdigit() else delay
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            wait = delay
        finally:
            _last[0] = time.monotonic()
        if attempt == 3:
            break
        time.sleep(wait)
        delay = min(delay * 2, 240)
    raise RuntimeError(f"MON API {method} failed after four attempts")
