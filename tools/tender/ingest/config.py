import os
from pathlib import Path

DATA = Path(os.environ.get("TENDER_DATA", "/opt/tender/data"))
RAW_EOP = DATA / "raw" / "eop"
RAW_TR = DATA / "raw" / "tr"
DSN = os.environ.get("TENDER_DSN", "dbname=tender")

EOP_BASE = os.environ.get("EOP_OPEN_DATA_BASE_URL", "https://storage.eop.bg")
EOP_FIRST_DAY = "2020-01-01"

TR_BASE = os.environ.get("TR_API_BASE", "https://api-sigma-cr.registryagency.bg")
TR_PORTAL_ENTRIES = "https://portal.registryagency.bg/CR/api/Applications/Entries"
USER_AGENT = "tender-research-crawler/1.0 (self-hosted, non-commercial; polite single-connection)"

BGN_PER_EUR = 1.95583  # fixed conversion rate, euro adopted 2026-01-01
