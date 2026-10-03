"""The archive of Наблюдател as the bronze layer: files by name, checked against their name and the archive's own record. No database."""
import datetime as dt
import hashlib
import json

import pytest

from ingest import archive
from tests.helpers import archive_dir, fixture


def test_the_files_of_the_archive_are_found_by_their_names_oldest_first(tmp_path):
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024), (2025, "2026-10-05"): fixture(2025), (2025, "2026-09-28"): b"x\n"})
    snaps = archive.snapshots(root)
    assert sorted(snaps) == [2024, 2025]
    assert [s.day for s in snaps[2025]] == [dt.date(2026, 9, 28), dt.date(2026, 10, 5)]
    assert snaps[2024][0].rel.startswith("dfz/2024/2026-09-28.") and len(snaps[2024][0].sha12) == 12


def test_two_files_of_one_day_are_one_snapshot_the_later_one(tmp_path):
    a, b = fixture(2024), fixture(2025)
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): a})
    sha = hashlib.sha256(b).hexdigest()
    rel = f"dfz/2024/2026-09-28.{sha[:12]}.csv"
    (root / rel).write_bytes(b)
    with (root / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": "2026-09-28T09:00:00Z", "source": "dfz", "key": "fy2024", "file": rel, "sha256": sha, "bytes": len(b)}) + "\n")
    snaps = archive.snapshots(root)
    assert len(snaps[2024]) == 1 and snaps[2024][0].rel == rel


def test_the_file_is_checked_against_its_name(tmp_path):
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024)})
    snap = archive.snapshots(root)[2024][0]
    raw, sha, path = archive.read(snap, archive.index_shas(root), root)
    assert raw == fixture(2024) and sha.startswith(snap.sha12)
    (root / snap.rel).write_bytes(raw + b"\n")
    with pytest.raises(archive.ArchiveError, match="не съвпада с името"):
        archive.read(snap, None, root)


def test_the_file_is_checked_against_the_record_of_the_archive(tmp_path):
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024)})
    snap = archive.snapshots(root)[2024][0]
    idx = archive.index_shas(root)
    idx[snap.rel] = ("0" * 64, idx[snap.rel][1], "")
    with pytest.raises(archive.ArchiveError, match="index.jsonl"):
        archive.read(snap, idx, root)
    idx[snap.rel] = (hashlib.sha256(fixture(2024)).hexdigest(), 5, "")
    with pytest.raises(archive.ArchiveError, match="index.jsonl"):
        archive.read(snap, idx, root)


def test_a_missing_file_or_state_is_an_archive_error_not_an_empty_year(tmp_path):
    with pytest.raises(archive.ArchiveError):
        archive.state(tmp_path)
    with pytest.raises(archive.ArchiveError):
        archive.snapshots(tmp_path, {})
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024)})
    snap = archive.snapshots(root)[2024][0]
    (root / snap.rel).unlink()
    with pytest.raises(archive.ArchiveError, match="не се чете"):
        archive.read(snap, None, root)


def test_the_age_of_the_archive_and_the_years_the_source_still_offers(tmp_path):
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024)}, last_ok="2026-10-05T00:41:00Z", form=(2025, 2026))
    st = archive.state(root)
    assert archive.years_in_form(st) == {2025, 2026}
    assert round(archive.age_hours(st, dt.datetime(2026, 10, 6, 0, 41, tzinfo=dt.timezone.utc))) == 24
    with pytest.raises(archive.ArchiveError):
        archive.age_hours({"seen": {}})
