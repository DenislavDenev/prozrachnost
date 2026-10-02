"""Every set that is read has its terms of use from data.egov.bg; only an explicit licence makes a set shareable."""
from ingest import config


def test_every_set_has_known_terms_and_only_explicit_licences_are_shared():
    sets = config.datasets()
    assert len(sets) == 74 and len({d["set_uri"] for d in sets}) == 74
    for d in sets:
        name, shared = config.LICENCES[d["terms_of_use_id"]]
        assert shared == (d["terms_of_use_id"] in ("1", "2"))
        assert d["kind"] in ("police", "crime-old", "bulletin", "road-agg")


def test_the_three_sets_of_the_archive_have_the_terms_the_portal_gave_them():
    by = {d["set_uri"]: d for d in config.datasets()}
    assert by["386ae85b-0c5c-4a5e-bd88-a8c7c123b765"]["terms_of_use_id"] == "2"       # CC BY
    assert by["dc074958-c8d5-4484-808a-800335ea4a23"]["terms_of_use_id"] == "1"       # CC0
    assert "4b948dd7-c9ef-4239-b2de-9b8e1c312467" not in by                           # the accidents themselves are Пътна обстановка
