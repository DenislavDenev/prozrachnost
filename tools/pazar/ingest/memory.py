"""The free memory of the server, as the kernel counts it (MemAvailable). The shared database lives on the same server
with 4 GB: a step that takes the memory it needs kills the database, so a heavy step waits for it first."""
import time


def available_mb():
    """MemAvailable in MB, or None where /proc/meminfo is not there (not Linux)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


def wait(need_mb, limit_secs=1800, pause=30):
    """True when need_mb are free now or become free within limit_secs; False when they do not."""
    waited = 0
    while True:
        have = available_mb()
        if have is None or have >= need_mb:
            return True
        if waited >= limit_secs:
            return False
        time.sleep(pause)
        waited += pause
