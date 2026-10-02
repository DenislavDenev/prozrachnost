"""The archive of Наблюдател is read as it is: a file that is not the one the state names is an error, not data."""
import datetime as dt

import pytest

from ingest import archive
from tests import helpers as h


def test_a_file_that_does_not_match_its_hash_is_an_error(tmp_path):
    st = h.build_archive(tmp_path, {h.POLICE: {h.B: h.fixture(h.POLICE, h.B)}})
    raw, sha, rel, at = archive.read(h.B, st, tmp_path)
    assert raw == h.fixture(h.POLICE, h.B) and rel.startswith(f"egov/{h.POLICE}/{h.B}/1.")
    (tmp_path / rel).write_bytes(raw[:-10])
    with pytest.raises(archive.ArchiveError, match="не съвпада"):
        archive.read(h.B, st, tmp_path)


def test_a_resource_the_archive_does_not_have_is_none_and_a_missing_file_is_an_error(tmp_path):
    st = h.build_archive(tmp_path, {h.POLICE: {h.B: h.fixture(h.POLICE, h.B)}})
    assert archive.read(h.A, st, tmp_path) is None
    assert archive.resources("5a852fa8-3652-4a3c-9124-416cd40438af", st, tmp_path) is None
    (tmp_path / archive.read(h.B, st, tmp_path)[2]).unlink()
    with pytest.raises(archive.ArchiveError, match="не се чете"):
        archive.read(h.B, st, tmp_path)


def test_the_age_is_counted_from_the_last_good_read():
    st = {"last_ok": "2026-10-02T02:56:05Z"}
    assert archive.age_hours(st, dt.datetime(2026, 10, 3, 2, 56, 5, tzinfo=dt.timezone.utc)) == 24
