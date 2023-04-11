import pytest
from journal.models import Issue
from submission.models import Keyword


def test_ccc():
    """DB completely ignored (not even created with --create-db)."""
    assert True


@pytest.mark.django_db
def test_ddd():
    """Fails with UndefinedColumn issue.issue_title_en."""
    assert True


def test_eee(sync_translation_fields):
    """Fails with no access to DB."""
    assert True


def test_fff(sync_translation_fields, db):
    """Fails with UndefinedColumn issue.issue_title_en."""
    assert True


@pytest.mark.django_db
def test_aaa(sync_translation_fields):
    """Test that the fixture works."""
    kwd = Keyword.objects.create(word="CIAO")
    assert kwd.word == "CIAO"


@pytest.mark.django_db
def test_bbb(sync_translation_fields, journal):
    """Test that the fixture works."""
    volume = 1
    issue = "01"
    issue = Issue.objects.create(issue_title="CIAO", volume=volume, issue=issue, journal=journal)
    assert issue.issue_title == "CIAO"
