import urllib.error

import pytest

from ingest import http


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"success":true,"data":[]}'


def test_retry_after_on_429(monkeypatch):
    waits = []
    calls = []
    monkeypatch.setattr(http.time, "sleep", waits.append)
    monkeypatch.setattr(http.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(http, "_last", [0.0])

    def answer(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "17"}, None)
        return Response()

    monkeypatch.setattr(http.urllib.request, "urlopen", answer)
    assert http.post("getResourceData", {"resource_uri": "abc"}).startswith(b'{"success":true')
    assert len(calls) == 2 and 17 in waits
    assert calls[0].get_header("User-agent").startswith("Prozrachnost/obrazovanie")


def test_403_is_not_empty_data_or_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(http.time, "sleep", lambda *_: None)
    monkeypatch.setattr(http.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(http, "_last", [0.0])

    def denied(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, None)

    monkeypatch.setattr(http.urllib.request, "urlopen", denied)
    with pytest.raises(urllib.error.HTTPError):
        http.post("getResourceData", {"resource_uri": "abc"})
    assert len(calls) == 1
