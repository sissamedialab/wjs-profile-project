"""Tests for the merge of duplicate editor keywords coming from wjapp (wjs/specs#3436, wjs/specs#3449)."""

from unittest import mock

import pytest
from submission import models as submission_models

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_wjapp import import_editor_keywords, import_keywords
from wjs.jcom_profile.import_wjapp.import_keywords import (
    get_or_create_import_keyword,
    merge_editor_keywords,
)

EDITOR_ROW = {
    "userCod": 1,
    "lastname": "Editor",
    "firstname": "Section",
    "email": "section.editor@example.org",
    "privacy": 1,
    "editorWorkload": 3,
}


def _rows(*rows):
    """Build wjapp-like editor keyword rows.

    Each row is (word, weight), (word, weight, available) or (word, weight, available, group_name).
    """
    result = []
    for row in rows:
        word, weight, *rest = row
        available = rest[0] if rest else False
        group_name = rest[1] if len(rest) > 1 else None
        result.append(
            {
                "keywordName": word,
                "keywordWeight": weight,
                "keywordAvailable": available,
                "subgroupName": group_name,
            },
        )
    return result


def test_merge_keeps_highest_weight():
    """The same keyword twice gives the highest weight, with a warning."""
    with mock.patch.object(import_keywords.logger, "warning") as warning:
        merged = merge_editor_keywords("JCOM", _rows(("stars", 3), ("stars", 7)))

    assert merged == {
        "stars": {"weight": 7, "available": False, "group_name": None},
    }, "highest weight should win"
    assert warning.call_count == 1, "one warning per merged keyword"


def test_merge_equal_weights_gives_one_entry():
    """The same keyword twice with the same weight is one entry."""
    merged = merge_editor_keywords("JCOM", _rows(("stars", 5), ("stars", 5)))

    assert merged == {
        "stars": {"weight": 5, "available": False, "group_name": None},
    }, "duplicates with equal weights should collapse"


def test_merge_zero_and_positive_weight():
    """A positive weight beats a zero weight for the same keyword."""
    merged = merge_editor_keywords("JCOM", _rows(("stars", 0), ("stars", 2)))

    assert merged == {
        "stars": {"weight": 2, "available": False, "group_name": None},
    }, "positive weight should win over zero"


def test_merge_jcomal_variants_collapse():
    """JCOMAL multi-language strings that normalize to the same word are merged."""
    merged = merge_editor_keywords("JCOMAL", _rows(("Stars;Estrellas;Stelle", 1), ("Stars;X;Y", 4)))

    assert merged == {
        "Stars": {"weight": 4, "available": False, "group_name": None},
    }, "variants with the same English word should merge"


def test_merge_strips_and_keeps_order():
    """Whitespace is stripped before comparing and the first-seen order is kept."""
    merged = merge_editor_keywords("JCOM", _rows((" b ", 1), ("a", 2), ("b", 3)))

    assert list(merged) == ["b", "a"], "keys should keep first-seen order"
    assert merged["b"]["weight"] == 3, "stripped duplicate should be merged"


def test_merge_no_duplicates_no_warning():
    """Distinct keywords are returned as they are, without warnings."""
    with mock.patch.object(import_keywords.logger, "warning") as warning:
        merged = merge_editor_keywords("JCOM", _rows(("a", 1), ("b", 2)))

    assert merged == {
        "a": {"weight": 1, "available": False, "group_name": None},
        "b": {"weight": 2, "available": False, "group_name": None},
    }, "distinct keywords should be untouched"
    assert warning.call_count == 0, "no warning without duplicates"


def test_merge_availability_any_wins():
    """The merged keyword is available if any of its wjapp rows is, regardless of order."""
    merged = merge_editor_keywords("JCOM", _rows(("stars", 3, False), ("stars", 7, True)))

    assert merged["stars"]["available"] is True, "available should win over unavailable"


def test_merge_availability_all_unavailable():
    """The merged keyword stays unavailable only if none of its wjapp rows is available."""
    merged = merge_editor_keywords("JCOM", _rows(("stars", 3, False), ("stars", 7, False)))

    assert merged["stars"]["available"] is False, "all-unavailable rows should merge to unavailable"


def test_merge_group_name_first_non_null_wins():
    """The merged keyword keeps the first non-null group name seen across its duplicate rows."""
    merged = merge_editor_keywords("JCAP", _rows(("alpha", 3, True, "Stars"), ("alpha", 7, True, None)))

    assert merged["alpha"]["group_name"] == "Stars", "a later null group name should not clear an earlier one"


def test_merge_group_name_defaults_to_none():
    """A keyword with no subgroupName at all merges to a None group name."""
    merged = merge_editor_keywords("JCOM", _rows(("stars", 3)))

    assert merged["stars"]["group_name"] is None


@pytest.mark.django_db
def test_get_or_create_import_keyword_available_is_visible(journal):
    """A newly created keyword marked available is not deactivated (visible)."""
    keyword, created = get_or_create_import_keyword(journal, "alpha", True)

    assert created is True
    assert keyword.deactivated is None, "an available keyword should be visible on creation"


@pytest.mark.django_db
def test_get_or_create_import_keyword_unavailable_is_hidden(journal):
    """A newly created keyword marked unavailable is deactivated (hidden until reviewed)."""
    keyword, created = get_or_create_import_keyword(journal, "alpha", False)

    assert created is True
    assert keyword.deactivated is not None, "an unavailable keyword should be hidden until reviewed"


@pytest.mark.django_db
def test_get_or_create_import_keyword_does_not_touch_existing_visibility(journal):
    """An already existing keyword's visibility is left untouched (an EO may have reviewed it since)."""
    existing = submission_models.Keyword.objects.create(word="alpha", deactivated=None)

    keyword, created = get_or_create_import_keyword(journal, "alpha", False)

    assert created is False
    assert keyword.pk == existing.pk
    assert keyword.deactivated is None, "an existing keyword's visibility should not be changed"


@pytest.mark.django_db
def test_get_or_create_import_keyword_backfills_missing_group_on_existing(journal):
    """A keyword created ungrouped by some other journal's import is grouped once JCAP's own group is seen."""
    existing = submission_models.Keyword.objects.create(word="alpha", deactivated=None)
    group = submission_models.KeywordGroup.objects.create(name="Stars", order=1)

    keyword, created = get_or_create_import_keyword(journal, "alpha", True, "Stars")

    assert created is False
    assert keyword.pk == existing.pk
    assert keyword.group == group, "a missing group should be backfilled from the new import"


@pytest.mark.django_db
def test_get_or_create_import_keyword_does_not_overwrite_existing_group(journal):
    """An existing keyword's already-set group is never replaced by a later import."""
    original_group = submission_models.KeywordGroup.objects.create(name="Stars", order=1)
    other_group = submission_models.KeywordGroup.objects.create(name="Planets", order=2)
    existing = submission_models.Keyword.objects.create(word="alpha", deactivated=None, group=original_group)

    keyword, created = get_or_create_import_keyword(journal, "alpha", True, other_group.name)

    assert created is False
    assert keyword.pk == existing.pk
    assert keyword.group == original_group, "an already-set group should never be overwritten"


@pytest.mark.django_db
def test_get_or_create_import_keyword_links_to_journal(journal):
    """The keyword is linked to the journal regardless of whether it was just created."""
    existing = submission_models.Keyword.objects.create(word="alpha", deactivated=None)

    get_or_create_import_keyword(journal, "alpha", True)

    assert journal.keywords.filter(pk=existing.pk).exists(), "an existing keyword should also get linked"


@pytest.mark.django_db
def test_get_or_create_import_keyword_sets_group_on_creation(journal):
    """A newly created keyword gets the matching top-level KeywordGroup."""
    group = submission_models.KeywordGroup.objects.create(name="Stars", order=1)

    keyword, created = get_or_create_import_keyword(journal, "alpha", True, "Stars")

    assert created is True
    assert keyword.group == group


@pytest.mark.django_db
def test_get_or_create_import_keyword_unknown_group_logs_error_and_skips(journal):
    """An unmapped wjapp group name is logged and does not abort the import."""
    with mock.patch.object(import_keywords.logger, "error") as error:
        keyword, created = get_or_create_import_keyword(journal, "alpha", True, "No Such Group")

    assert created is True
    assert keyword.group is None, "the keyword should still be created, just ungrouped"
    assert error.call_count == 1


def test_is_active_wjapp_editor_true_when_role_found():
    """wjapp reports an enabled EDT/EB_MEMBER row for this user."""
    cursor = mock.MagicMock()
    cursor.fetchone.return_value = {"1": 1}
    connection = mock.MagicMock()
    connection.cursor.return_value = cursor

    assert import_editor_keywords.is_active_wjapp_editor(connection, editor_cod=42) is True
    cursor.close.assert_called_once()


def test_is_active_wjapp_editor_false_when_not_found():
    """wjapp has no enabled EDT/EB_MEMBER row for this user (retired, or never a general editor)."""
    cursor = mock.MagicMock()
    cursor.fetchone.return_value = None
    connection = mock.MagicMock()
    connection.cursor.return_value = cursor

    assert import_editor_keywords.is_active_wjapp_editor(connection, editor_cod=42) is False


@pytest.fixture
def sync(monkeypatch, section_editor, journal):
    """Return a function that runs sync_editor for ``section_editor`` with the given wjapp keyword rows."""

    def _sync(rows, clear=False):
        monkeypatch.setattr(
            import_editor_keywords,
            "read_editor_keywords",
            lambda connection, editor_cod, journal_code: rows,
        )
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
def test_sync_editor_sets_workload_on_newly_added_role(monkeypatch, create_jcom_user, journal):
    """A brand-new editor (role just granted by this sync) gets their workload set from wjapp.

    Granting the role creates a default StaffWorkloadParameters row as a side effect (signals.py), so
    this must not be confused with "an existing editor's parameters": wjs/specs#3493, gamboz note 84073.
    """
    new_editor = create_jcom_user("new_section_editor")
    monkeypatch.setattr(
        import_editor_keywords,
        "read_editor_keywords",
        lambda connection, editor_cod, journal_code: _rows(("alpha", 1)),
    )

    result = import_editor_keywords.sync_editor(
        journal, None, EDITOR_ROW, get_account=lambda *args, **kwargs: new_editor
    )

    assert result.role_added is True
    assert result.workload_set is True
    params = wjs_models.StaffWorkloadParameters.objects.get(user=new_editor, journal=journal)
    assert params.workload == EDITOR_ROW["editorWorkload"]
    assert params.enabled is True, "an editor wjapp still considers active stays a live candidate"


@pytest.mark.django_db
def test_sync_editor_workload_and_keywords_disables_inactive_wjapp_editor(create_jcom_user, journal):
    """A newly role-granted editor no longer active in wjapp is kept out of the automatic-assignment pool.

    E.g. a retired editor, or a special-issue guest editor, found only in an old paper's backfilled
    history: the role is still granted (required to record the historical assignment) and the
    workload/keywords are still recorded, but they must not silently re-enter the general
    automatic-assignment pool (wjs/specs#3493, gamboz note 84073).
    """
    new_editor = create_jcom_user("retired_editor")

    result = import_editor_keywords.sync_editor_workload_and_keywords(
        journal,
        new_editor,
        3,
        _rows(("alpha", 1)),
        newly_created_editor=True,
        editor_active_in_wjapp=False,
    )

    assert result.workload_set is True
    params = wjs_models.StaffWorkloadParameters.objects.get(user=new_editor, journal=journal)
    assert params.workload == 3, "workload is still recorded from wjapp"
    assert params.enabled is False, "must not be a live automatic-assignment candidate"


@pytest.mark.django_db
def test_sync_editor_workload_and_keywords_does_not_disable_existing_editor(section_editor, journal):
    """An editor who already has the role is never touched by editor_active_in_wjapp."""
    params, _ = wjs_models.StaffWorkloadParameters.objects.get_or_create(user=section_editor, journal=journal)
    assert params.enabled is True

    import_editor_keywords.sync_editor_workload_and_keywords(
        journal,
        section_editor,
        3,
        _rows(("alpha", 1)),
        newly_created_editor=False,
        editor_active_in_wjapp=False,
    )

    params.refresh_from_db()
    assert params.enabled is True, "enabled is only ever set when the role was just granted"


@pytest.mark.django_db
def test_sync_editor_does_not_overwrite_existing_workload(sync, section_editor, journal):
    """Re-syncing an editor who already has the role must never reset a workload an EO has since corrected."""
    params, _ = wjs_models.StaffWorkloadParameters.objects.get_or_create(user=section_editor, journal=journal)
    params.workload = 42
    params.save()

    result = sync(_rows(("alpha", 1)))

    params.refresh_from_db()
    assert result.role_added is False, "the section_editor fixture already granted the role"
    assert params.workload == 42, "an existing workload must not be reset from wjapp on a plain re-sync"


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


@pytest.mark.django_db
def test_sync_editor_creates_available_keyword_visible(sync):
    """A new keyword with an available wjapp code is created visible."""
    sync(_rows(("gamma", 1, True)))

    assert submission_models.Keyword.objects.get(word="gamma").deactivated is None


@pytest.mark.django_db
def test_sync_editor_creates_unavailable_keyword_hidden(sync):
    """A new keyword with no available wjapp code is created hidden until reviewed."""
    sync(_rows(("gamma", 1, False)))

    assert submission_models.Keyword.objects.get(word="gamma").deactivated is not None


@pytest.mark.django_db
def test_sync_editor_merges_availability_from_duplicate_source_keywords(sync):
    """A new keyword is visible if any of its duplicate wjapp rows is available."""
    sync(_rows(("gamma", 1, False), ("gamma", 2, True)))

    assert submission_models.Keyword.objects.get(word="gamma").deactivated is None


@pytest.mark.django_db
def test_sync_editor_sets_group_on_new_keyword(sync):
    """A new keyword is assigned the wjapp-provided KeywordGroup."""
    group = submission_models.KeywordGroup.objects.create(name="Stars", order=1)

    sync(_rows(("gamma", 1, True, "Stars")))

    assert submission_models.Keyword.objects.get(word="gamma").group == group


@pytest.mark.django_db
def test_sync_editor_links_keyword_to_journal(sync, journal):
    """A synced keyword is linked to the journal."""
    sync(_rows(("gamma", 1, True)))

    keyword = submission_models.Keyword.objects.get(word="gamma")
    assert journal.keywords.filter(pk=keyword.pk).exists()
