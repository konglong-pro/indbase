from datetime import datetime, timezone

import pytest

import re

from indbase_core.ids import date_parts_from_doc_id, new_doc_id, new_prefixed_id, revision_id


def test_doc_id_uses_date_and_suffix() -> None:
    doc_id = new_doc_id(datetime(2026, 5, 12, tzinfo=timezone.utc), short_id="a8f3c2")

    assert doc_id == "doc_20260512_a8f3c2"
    assert date_parts_from_doc_id(doc_id) == ("2026", "05", "12")


def test_revision_id_is_doc_bound_sequence() -> None:
    assert revision_id("doc_20260512_a8f3c2", 2) == "rev_doc_20260512_a8f3c2_0002"


def test_prefixed_id_uses_longer_suffix_budget() -> None:
    prefixed_id = new_prefixed_id("idxentry", datetime(2026, 5, 12, tzinfo=timezone.utc))

    assert re.fullmatch(r"idxentry_20260512_[0-9a-f]{12}", prefixed_id)


def test_revision_sequence_must_be_positive() -> None:
    with pytest.raises(ValueError):
        revision_id("doc_20260512_a8f3c2", 0)
