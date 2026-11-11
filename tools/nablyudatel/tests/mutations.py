"""Breaks the rules of the archive and of its register one at a time; each must make the tests fail.

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
    # the register (these need NABLYUDATEL_TEST_DSN; without it the tests skip and the mutation survives)
    ("archive/register.py", "if have[k] > 0:", "if have[k] >= 0:"),                                     # a line never registered
    ("archive/register.py", 'expect_sha=e["sha256"])', "expect_sha=None)"),                              # a file that is not its line
    ("archive/register.py", "        lines.pop()   # cut: written while we read", "        pass"),         # a half-written line read
    ("archive/register.py", "            missing += 1", "            pass"),                              # parity blind to a missing line
    ("archive/register.py", "max(finished_at) FILTER (WHERE status = 'failed')", "max(finished_at) FILTER (WHERE false)"),  # failed runs unseen
    ("archive/register.py", "if conn.execute(\"SELECT 1 FROM ops.run", "if False and conn.execute(\"SELECT 1 FROM ops.run"),  # state imported twice
    ("archive/run.py", 'if c["missing"] or c["extra"] or c["size_differs"]:', "if False:"),            # register lag not reported
    ("archive/run.py", "                if r is not None:", "                if False:"),                 # a run's new lines not registered
    ("archive/register.py", "            if path.stat().st_size != e[\"bytes\"]:", "            if False:"),   # the move blind to a changed file
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
