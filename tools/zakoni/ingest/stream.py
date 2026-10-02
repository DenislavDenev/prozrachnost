"""Streaming reader of the data.egov.bg answers kept by the Наблюдател archive: {"success":true,"data":[rec,rec,...]}.

The biggest answer is 300 MB; it is never loaded whole. Records are decoded one by one from a sliding buffer, so the
memory stays at a few times the chunk size (measured: about 40 MB for the 300 MB file). A file that is not exactly
this shape (empty, HTML, cut off, success:false, something after the data) is a ShapeError: nothing is written.
"""
import hashlib
import json
import re

CHUNK = 1 << 22
_HEAD = re.compile(r'\s*\{\s*"success"\s*:\s*(true|false)\s*,\s*"data"\s*:\s*\[')
_WS = " ,\n\r\t"
_dec = json.JSONDecoder()


class ShapeError(ValueError):
    """The answer is not the shape we know. The import stops and writes nothing."""


def sha256_file(path, chunk=1 << 22):
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def records(path, chunk=CHUNK):
    """Yield every record of "data" (the first one is the legend, not data: the caller checks that)."""
    with open(path, encoding="utf-8-sig") as f:
        buf = f.read(chunk)
        if not buf.strip():
            raise ShapeError("празен отговор")
        m = _HEAD.match(buf)
        while not m and len(buf) < 512 and (more := f.read(chunk)):
            buf += more
            m = _HEAD.match(buf)
        if not m:
            raise ShapeError("не е очакваният отговор {success, data}: " + buf[:80].replace("\n", " "))
        if m.group(1) != "true":
            raise ShapeError("отговорът е success:false")
        pos = m.end()
        while True:
            while pos < len(buf) and buf[pos] in _WS:
                pos += 1
            if pos >= len(buf):
                more = f.read(chunk)
                if not more:
                    raise ShapeError("отрязан файл: няма край на data")
                buf = buf[pos:] + more
                pos = 0
                continue
            if buf[pos] == "]":
                rest = (buf[pos + 1:] + f.read()).strip()
                if not rest:
                    raise ShapeError("отрязан файл: няма затварящата скоба")
                if rest != "}":
                    raise ShapeError("след data има непознато съдържание: " + rest[:80])
                return
            try:
                obj, end = _dec.raw_decode(buf, pos)
            except json.JSONDecodeError:
                more = f.read(chunk)
                if not more:
                    raise ShapeError("отрязан файл: последният запис не е цял") from None
                buf = buf[pos:] + more
                pos = 0
                continue
            if not isinstance(obj, dict):
                raise ShapeError(f"запис, който не е обект: {type(obj).__name__}")
            yield obj
            pos = end
