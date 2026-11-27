"""Tests for the wjs_stats view that downloads the journal editors as CSV."""

import csv
import io

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404
from plugins.wjs_stats.views import JournalEditorsCSV

from wjs.jcom_profile import constants
from wjs.jcom_profile.models import StaffWorkloadParameters

# wjs_stats URLs are mounted by Janeway's plugin loader only if the plugin is registered in the DB when the urlconf
# is imported, which does not happen in tests: call the view directly.


def _get(rf, user, journal):
    request = rf.get("/plugins/wjs_stats/journal-editors.csv")
    request.user = user
    request.journal = journal
    return JournalEditorsCSV.as_view()(request)


@pytest.mark.django_db
def test_journal_editors_csv(rf, admin, journal, editor, section_editor, reviewer):
    """Editors and section editors are listed once each, with their data and workload parameters."""
    # A user with both roles must appear only once.
    section_editor.add_account_role(constants.EDITOR_ROLE, journal)
    # The section editor has workload parameters; the editor has none at all.
    StaffWorkloadParameters.objects.update_or_create(
        user=section_editor.janeway_account,
        journal=journal,
        defaults={"enabled": False, "workload": 5, "brake_on": -1},
    )
    StaffWorkloadParameters.objects.filter(user=editor.janeway_account, journal=journal).delete()

    response = _get(rf, admin, journal)

    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    assert response["Content-Type"] == "text/csv", "The response must be a CSV"
    assert "attachment" in response["Content-Disposition"], "The CSV must be served as a download"
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    expected_header = ["name", "surname", "email", "journal", "enabled", "workload", "brake_on"]
    assert rows[0] == expected_header, f"Unexpected header {rows[0]}"
    by_email = {row[2]: row for row in rows[1:]}
    assert sorted(by_email) == sorted([editor.email, section_editor.email]), f"Unexpected editors {rows[1:]}"
    assert all(row[3] == journal.code for row in rows[1:]), f"Every row must report the journal code: {rows[1:]}"
    assert by_email[section_editor.email][4:] == [
        "False",
        "5",
        "-1",
    ], f"Workload parameters must be reported: {by_email[section_editor.email]}"
    assert by_email[editor.email][4:] == ["", "", ""], f"No parameters, no values: {by_email[editor.email]}"


@pytest.mark.django_db
def test_journal_editors_csv_staff_only(rf, journal, editor):
    """Non-staff users cannot download the list."""
    with pytest.raises(PermissionDenied):
        _get(rf, editor, journal)


@pytest.mark.django_db
def test_journal_editors_csv_needs_journal(rf, admin):
    """At press level there is no journal, hence no list."""
    with pytest.raises(Http404):
        _get(rf, admin, None)
