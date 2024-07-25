"""Not real tests, just academic stuff, curiosities & co."""

import pytest
from django.db import IntegrityError
from django.db.transaction import TransactionManagementError


@pytest.mark.skipif("not config.getoption('--run-academic')")
@pytest.mark.django_db
def test_section_wjssection_creation_without_refresh(
    journal,
    section_factory,
):
    """Get a NotNullViolation: for column "journal_id" of relation "submission_section".

    This happens if I try to save the wjssection without first refreshing it from the DB.
    WHY???
    """
    section = section_factory(name="any", journal=journal)
    with pytest.raises(IntegrityError):
        section.wjssection.save()
    assert True


@pytest.mark.skipif("not config.getoption('--run-academic')")
@pytest.mark.django_db
def test_section_wjssection_creation_with_refresh(
    journal,
    section_factory,
):
    """Counterpart the above."""
    section = section_factory(name="any", journal=journal)
    section.refresh_from_db()
    section.wjssection.save()
    assert True


@pytest.mark.skipif("not config.getoption('--run-academic')")
@pytest.mark.django_db
def test_section_wjssection_creation_with_and_without_refresh(
    journal,
    section_factory,
):
    """Both combined cannot be done: problems with the management of the transactions."""
    section = section_factory(name="any", journal=journal)
    with pytest.raises(IntegrityError):
        section.wjssection.save()
    with pytest.raises(TransactionManagementError):
        section.refresh_from_db()
    assert True
