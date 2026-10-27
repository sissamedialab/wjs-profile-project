"""Tests for the signals that create StaffWorkloadParameters when a staff role is granted (specs#3093)."""

import datetime

import pytest
from core.models import AccountRole, Role
from django.contrib.auth.models import Group

from wjs.jcom_profile.constants import (
    AUTHOR_ROLE,
    DIRECTOR_MAIN_ROLE,
    DIRECTOR_ROLE,
    EDITOR_ROLE,
    REVIEWER_ROLE,
    SECTION_EDITOR_ROLE,
)
from wjs.jcom_profile.models import StaffWorkloadParameters


@pytest.fixture
def staff_user(create_jcom_user, roles, director_role):
    """A user without roles nor groups."""
    return create_jcom_user("staff").janeway_account


@pytest.mark.parametrize("role_slug", [SECTION_EDITOR_ROLE, REVIEWER_ROLE, DIRECTOR_ROLE])
@pytest.mark.django_db
def test_covered_role_creates_default_row(staff_user, journal, role_slug):
    staff_user.add_account_role(role_slug, journal)

    params = StaffWorkloadParameters.objects.get(user=staff_user, journal=journal)
    assert (params.workload, params.enabled, params.vacancy_start, params.vacancy_end) == (
        0,
        True,
        None,
        None,
    ), f"Granting {role_slug} must create a row with model defaults"
    assert staff_user.jcomprofile.is_enabled_as_editor(journal), "New staff must not look disabled"


@pytest.mark.parametrize("role_slug", [AUTHOR_ROLE, EDITOR_ROLE, DIRECTOR_MAIN_ROLE])
@pytest.mark.django_db
def test_uncovered_role_creates_nothing(staff_user, journal, role_slug):
    staff_user.add_account_role(role_slug, journal)

    assert not StaffWorkloadParameters.objects.filter(
        user=staff_user
    ).exists(), f"Granting {role_slug} must not create StaffWorkloadParameters"


@pytest.mark.django_db
def test_existing_row_is_not_modified(staff_user, journal):
    StaffWorkloadParameters.objects.create(
        user=staff_user,
        journal=journal,
        workload=7,
        enabled=False,
        vacancy_start=datetime.date(2026, 1, 1),
    )

    staff_user.add_account_role(SECTION_EDITOR_ROLE, journal)

    params = StaffWorkloadParameters.objects.get(user=staff_user, journal=journal)
    assert (params.workload, params.enabled, params.vacancy_start) == (
        7,
        False,
        datetime.date(2026, 1, 1),
    ), "An existing row must be left untouched"
    assert StaffWorkloadParameters.objects.filter(user=staff_user).count() == 1, "No duplicate row expected"


@pytest.mark.django_db
def test_role_in_two_journals_creates_two_rows(staff_user, journal, journal_factory):
    other = journal_factory("OTHER")

    staff_user.add_account_role(REVIEWER_ROLE, journal)
    staff_user.add_account_role(REVIEWER_ROLE, other)

    journals = set(StaffWorkloadParameters.objects.filter(user=staff_user).values_list("journal", flat=True))
    assert journals == {journal.pk, other.pk}, "One row per journal where the role was granted"


@pytest.mark.django_db
def test_raw_save_creates_nothing(staff_user, journal):
    AccountRole(user=staff_user, journal=journal, role=Role.objects.get(slug=SECTION_EDITOR_ROLE)).save_base(raw=True)

    assert not StaffWorkloadParameters.objects.filter(
        user=staff_user
    ).exists(), "Fixture loading (raw save) must not create rows"


@pytest.mark.django_db
def test_user_save_does_not_backfill(staff_user, journal):
    # bulk_create sends no post_save: this mimics a role holder that predates the signal
    AccountRole.objects.bulk_create(
        [AccountRole(user=staff_user, journal=journal, role=Role.objects.get(slug=SECTION_EDITOR_ROLE))]
    )

    staff_user.save()
    staff_user.jcomprofile.save()

    assert not StaffWorkloadParameters.objects.filter(
        user=staff_user
    ).exists(), "Saving a user must not create rows for existing roles (no lazy backfill)"


@pytest.mark.django_db
def test_eo_forward_add_creates_rows_for_all_journals(staff_user, journal, journal_factory, eo_group):
    other = journal_factory("OTHER")

    staff_user.groups.add(eo_group)

    journals = set(StaffWorkloadParameters.objects.filter(user=staff_user).values_list("journal", flat=True))
    assert journals == {journal.pk, other.pk}, "Joining EO must create a row for every journal"


@pytest.mark.django_db
def test_eo_reverse_add_creates_rows_for_all_journals(staff_user, journal, journal_factory, eo_group):
    other = journal_factory("OTHER")

    eo_group.user_set.add(staff_user)

    journals = set(StaffWorkloadParameters.objects.filter(user=staff_user).values_list("journal", flat=True))
    assert journals == {journal.pk, other.pk}, "Joining EO from the group side must create a row for every journal"


@pytest.mark.django_db
def test_eo_membership_is_idempotent(staff_user, journal, journal_factory, eo_group):
    journal_factory("OTHER")

    staff_user.groups.add(eo_group)
    staff_user.groups.set([eo_group])
    staff_user.groups.add(eo_group)

    assert StaffWorkloadParameters.objects.filter(user=staff_user).count() == 2, "Exactly one row per journal"


@pytest.mark.django_db
def test_non_eo_group_creates_nothing(staff_user, journal):
    staff_user.groups.add(Group.objects.create(name="Other"))

    assert not StaffWorkloadParameters.objects.filter(
        user=staff_user
    ).exists(), "Joining a non-EO group must not create rows"


@pytest.mark.django_db
def test_eo_existing_row_is_not_modified(staff_user, journal, journal_factory, eo_group):
    other = journal_factory("OTHER")
    StaffWorkloadParameters.objects.create(user=staff_user, journal=journal, workload=10)

    staff_user.groups.add(eo_group)

    assert (
        StaffWorkloadParameters.objects.get(user=staff_user, journal=journal).workload == 10
    ), "An existing row must be left untouched"
    assert (
        StaffWorkloadParameters.objects.get(user=staff_user, journal=other).workload == 0
    ), "The missing row is created with model defaults"
