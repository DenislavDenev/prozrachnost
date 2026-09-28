"""Breaks six rules of the archive one at a time; each must make the tests fail.

    python tests/mutations.py    # from tools/nablyudatel; exit 1 when a mutation survives
"""
import subprocess
import sys
from pathlib import Path

MUTATIONS = [
    ("archive/core.py", 'if seen.get(key, {}).get("sha") == sha:', "if False:"),                     # the same answer twice
    ("archive/core.py", 'if fmt != "html" and (head.startswith', "if False and (head.startswith"),   # an error page kept
    ("archive/run.py", "if err and err >= ok:", 'if err and err > "9":'),                            # a failure not reported
    ("archive/sources.py", "if data == home:", "if False:"),                                          # ЕРИК's home page as a report
    ("archive/sources.py", 'return m.group(3) if m.group(1) == m.group(2) else (m.group(4) or "")', "return m.group(0)"),
    ("archive/sources.py", "if newest is None or date.fromisoformat(newest) < last - timedelta(days=3):", "if newest is None:"),
]


def pytest():
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], capture_output=True).returncode


if __name__ == "__main__":
    assert pytest() == 0, "the tests fail before any mutation"
    survived = 0
    for f, a, b in MUTATIONS:
        p = Path(f)
        s = p.read_text(encoding="utf-8")
        assert a in s, f"not found in {f}: {a}"
        try:
            p.write_text(s.replace(a, b), encoding="utf-8")
            caught = pytest() != 0
        finally:
            p.write_text(s, encoding="utf-8")
        survived += not caught
        print("caught  " if caught else "SURVIVED", f, a[:60])
    sys.exit(1 if survived else 0)
