"""Tests for the merge of duplicate editor keywords coming from wjapp (wjs/specs#3436)."""

from unittest import mock

import pytest

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_wjapp import import_editor_keywords, import_keywords
from wjs.jcom_profile.import_wjapp.import_keywords import merge_editor_keywords

EDITOR_ROW = {
    "userCod": 1,
    "lastname": "Editor",
    "firstname": "Section",
    "email": "section.editor@example.org",
    "privacy": 1,
    "editorWorkload": 3,
}


def _rows(*pairs):
    """Build wjapp-like editor keyword rows from (word, weight) pairs."""
    return [{"keywordName": word, "keywordWeight": weight} for word, weight in pairs]


def test_merge_keeps_highest_weight():
    """The same keyword twice gives the highest weight, with a warning."""
    with mock.patch.object(import_keywords.logger, "warning") as warning:
        merged = merge_editor_keywords("JCOM", _rows(("stars", 3), ("stars", 7)))

    assert merged == {"stars": 7}, "highest weight should win"
    assert warning.call_count == 1, "one warning per merged keyword"


def test_merge_equal_weights_gives_one_entry():
    """The same keyword twice with the same weight is one entry."""
    merged = merge_editor_keywords("JCOM", _rows(("stars", 5), ("stars", 5)))

    assert merged == {"stars": 5}, "duplicates with equal weights should collapse"


def test_merge_zero_and_positive_weight():
    """A positive weight beats a zero weight for the same keyword."""
    merged = merge_editor_keywords("JCOM", _rows(("stars", 0), ("stars", 2)))

    assert merged == {"stars": 2}, "positive weight should win over zero"


def test_merge_jcomal_variants_collapse():
    """JCOMAL multi-language strings that normalize to the same word are merged."""
    merged = merge_editor_keywords("JCOMAL", _rows(("Stars;Estrellas;Stelle", 1), ("Stars;X;Y", 4)))

    assert merged == {"Stars": 4}, "variants with the same English word should collapse"


def test_merge_strips_and_keeps_order():
    """Whitespace is stripped before comparing and the first-seen order is kept."""
    merged = merge_editor_keywords("JCOM", _rows((" b ", 1), ("a", 2), ("b", 3)))

    assert list(merged) == ["b", "a"], "keys should keep first-seen order"
    assert merged["b"] == 3, "stripped duplicate should be merged"


def test_merge_no_duplicates_no_warning():
    """Distinct keywords are returned as they are, without warnings."""
    with mock.patch.object(import_keywords.logger, "warning") as warning:
        merged = merge_editor_keywords("JCOM", _rows(("a", 1), ("b", 2)))

    assert merged == {"a": 1, "b": 2}, "distinct keywords should be untouched"
    assert warning.call_count == 0, "no warning without duplicates"


@pytest.fixture
def sync(monkeypatch, section_editor, journal):
    """Return a function that runs sync_editor for ``section_editor`` with the given wjapp keyword rows."""

    def _sync(rows, clear=False):
        monkeypatch.setattr(import_editor_keywords, "read_editor_keywords", lambda connection, editor_cod: rows)
        return import_editor_keywords.sync_editor(
            journal, None, EDITOR_ROW, clear=clear, get_account=lambda *args, **kwargs: section_editor
        )

    return _sync


@pytest.mark.django_db
def test_sync_editor_merges_duplicate_source_keywords(sync, section_editor, journal):
    """Duplicate source keywords give one StaffKeyword with the highest weight."""
    result = sync(_rows(("alpha", 3), ("alpha", 7)))

    staff_keywords = wjs_models.StaffKeyword.objects.filter(
        parameters__user=section_editor, parameters__journal=journal
    )
    assert [(sk.keyword.word, sk.weight) for sk in staff_keywords] == [("alpha", 7)], "one row with the max weight"
    assert result.keywords_added == 1, "only one keyword was added"


@pytest.mark.django_db
def test_sync_editor_does_not_overwrite_existing_weight(sync, section_editor, journal):
    """Without clear, a keyword already there keeps its weight."""
    sync(_rows(("alpha", 9)))
    result = sync(_rows(("alpha", 3)))

    staff_keywords = wjs_models.StaffKeyword.objects.filter(
        parameters__user=section_editor, parameters__journal=journal
    )
    assert [(sk.keyword.word, sk.weight) for sk in staff_keywords] == [("alpha", 9)], "existing weight is kept"
    assert result.keywords_added == 0, "nothing was added the second time"


@pytest.mark.django_db
def test_sync_editor_clear_replaces_keywords(sync, section_editor, journal):
    """With clear, the keyword set is replaced by the synced one."""
    sync(_rows(("alpha", 1)))
    sync(_rows(("beta", 2)), clear=True)

    staff_keywords = wjs_models.StaffKeyword.objects.filter(
        parameters__user=section_editor, parameters__journal=journal
    )
    assert [sk.keyword.word for sk in staff_keywords] == ["beta"], "only the synced keyword should remain"


@pytest.mark.django_db
def test_sync_editor_without_keywords_creates_nothing(sync):
    """An editor without wjapp keywords gets no StaffKeyword."""
    result = sync([])

    assert not wjs_models.StaffKeyword.objects.exists(), "no keyword should be created"
    assert result.keywords_added == 0, "nothing was added"
