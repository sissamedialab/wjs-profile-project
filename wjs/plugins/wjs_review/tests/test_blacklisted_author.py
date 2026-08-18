"""Tests for the blacklisted author attention condition."""

import pytest
from plugins.wjs_review.ac_service import (
    BLACKLISTED_AUTHOR,
    evaluate_blacklisted_author,
)
from plugins.wjs_review.events.checks import check_blacklisted_authors
from plugins.wjs_review.models import AttentionCondition, BlacklistedAuthorEmail


@pytest.fixture
def blacklisted_email(db):
    """Create a single blacklisted email entry."""
    return BlacklistedAuthorEmail.objects.create(
        email="bad.author@example.com",
        note="Duplicate submission",
    )


@pytest.mark.django_db
def test_blacklisted_author_ac_created(submitted_article, eo_user, blacklisted_email):
    """AC is created for EO when an author's email is on the blacklist."""
    article = submitted_article

    # Set the correspondence author's email to the blacklisted email.
    author = article.correspondence_author
    author.email = "bad.author@example.com"
    author.save()

    evaluate_blacklisted_author(article)

    ac = AttentionCondition.objects.filter(
        article=article,
        user=eo_user,
        code=BLACKLISTED_AUTHOR,
    ).first()
    assert ac is not None
    assert ac.status == AttentionCondition.Status.ACTIVE
    assert "bad.author@example.com" in ac.message


@pytest.mark.django_db
def test_blacklisted_author_ac_not_created_when_no_match(submitted_article, eo_user, blacklisted_email):
    """No AC is created when no author email matches the blacklist."""
    article = submitted_article

    # The article's author email doesn't match the blacklist.
    evaluate_blacklisted_author(article)

    ac = AttentionCondition.objects.filter(
        article=article,
        code=BLACKLISTED_AUTHOR,
    )
    assert not ac.exists()


@pytest.mark.django_db
def test_blacklisted_author_ac_resolved_when_email_removed(submitted_article, eo_user, blacklisted_email):
    """AC is resolved when the author's email is no longer on the blacklist."""
    article = submitted_article

    # Set up: author email matches blacklist.
    author = article.correspondence_author
    author.email = "bad.author@example.com"
    author.save()

    evaluate_blacklisted_author(article)
    assert AttentionCondition.objects.filter(
        article=article, code=BLACKLISTED_AUTHOR, status=AttentionCondition.Status.ACTIVE
    ).exists()

    # Remove the email from the blacklist.
    blacklisted_email.delete()

    evaluate_blacklisted_author(article)

    ac = AttentionCondition.objects.filter(article=article, code=BLACKLISTED_AUTHOR).first()
    assert ac is not None
    assert ac.status == AttentionCondition.Status.RESOLVED


@pytest.mark.django_db
def test_check_blacklisted_authors_returns_true(submitted_article, eo_user, blacklisted_email):
    """The check function always returns True (non-blocking)."""
    article = submitted_article
    author = article.correspondence_author
    author.email = "bad.author@example.com"
    author.save()

    result = check_blacklisted_authors(article)
    assert result is True


@pytest.mark.django_db
def test_check_blacklisted_authors_creates_ac(submitted_article, eo_user, blacklisted_email):
    """The check function creates an AC as a side effect."""
    article = submitted_article
    author = article.correspondence_author
    author.email = "bad.author@example.com"
    author.save()

    check_blacklisted_authors(article)

    ac = AttentionCondition.objects.filter(
        article=article,
        code=BLACKLISTED_AUTHOR,
    ).first()
    assert ac is not None
    assert ac.status == AttentionCondition.Status.ACTIVE


@pytest.mark.django_db
def test_multiple_blacklisted_authors(submitted_article, eo_user):
    """AC message lists all blacklisted emails when multiple match."""
    article = submitted_article

    BlacklistedAuthorEmail.objects.create(email="bad1@example.com", note="Reason 1")
    BlacklistedAuthorEmail.objects.create(email="bad2@example.com")

    # Set emails on FrozenAuthor records, since evaluate_blacklisted_author
    # reads from frozen_email (the single source of truth).
    frozen_authors = list(article.frozenauthor_set.all())
    if frozen_authors:
        frozen_authors[0].frozen_email = "bad1@example.com"
        frozen_authors[0].save()
    if len(frozen_authors) > 1:
        frozen_authors[1].frozen_email = "bad2@example.com"
        frozen_authors[1].save()

    evaluate_blacklisted_author(article)

    ac = AttentionCondition.objects.filter(
        article=article,
        code=BLACKLISTED_AUTHOR,
    ).first()
    assert ac is not None
    assert "bad1@example.com" in ac.message
    assert "bad2@example.com" in ac.message


@pytest.mark.django_db
def test_blacklisted_author_case_insensitive(submitted_article, eo_user):
    """Email matching is case-insensitive."""
    article = submitted_article
    BlacklistedAuthorEmail.objects.create(email="Bad.Author@Example.COM")

    author = article.correspondence_author
    author.email = "bad.author@example.com"
    author.save()

    evaluate_blacklisted_author(article)

    ac = AttentionCondition.objects.filter(
        article=article,
        code=BLACKLISTED_AUTHOR,
    ).first()
    assert ac is not None
    assert ac.status == AttentionCondition.Status.ACTIVE


# -- Admin-triggered re-evaluation tests --


@pytest.mark.django_db
def test_admin_add_blacklisted_email_fires_ac(submitted_article, eo_user):
    """Adding a BlacklistedAuthorEmail via admin re-evaluates existing articles."""
    from django.contrib.admin.sites import AdminSite
    from plugins.wjs_review.advanced_admin.admin import BlacklistedAuthorEmailAdmin

    article = submitted_article
    author = article.correspondence_author
    author.email = "spammer@example.com"
    author.save()

    # No AC should exist yet.
    assert not AttentionCondition.objects.filter(article=article, code=BLACKLISTED_AUTHOR).exists()

    # Simulate admin add via save_model.
    admin_instance = BlacklistedAuthorEmailAdmin(BlacklistedAuthorEmail, AdminSite())
    obj = BlacklistedAuthorEmail(email="spammer@example.com", note="Spam")
    admin_instance.save_model(request=None, obj=obj, form=None, change=False)

    ac = AttentionCondition.objects.filter(article=article, code=BLACKLISTED_AUTHOR).first()
    assert ac is not None
    assert ac.status == AttentionCondition.Status.ACTIVE
    assert "spammer@example.com" in ac.message


@pytest.mark.django_db
def test_admin_delete_blacklisted_email_resolves_ac(submitted_article, eo_user, blacklisted_email):
    """Deleting a BlacklistedAuthorEmail via admin resolves the AC on affected articles."""
    from django.contrib.admin.sites import AdminSite
    from plugins.wjs_review.advanced_admin.admin import BlacklistedAuthorEmailAdmin

    article = submitted_article
    author = article.correspondence_author
    author.email = "bad.author@example.com"
    author.save()

    # Create the AC first.
    evaluate_blacklisted_author(article)
    assert AttentionCondition.objects.filter(
        article=article, code=BLACKLISTED_AUTHOR, status=AttentionCondition.Status.ACTIVE
    ).exists()

    # Simulate admin delete via delete_model.
    admin_instance = BlacklistedAuthorEmailAdmin(BlacklistedAuthorEmail, AdminSite())
    admin_instance.delete_model(request=None, obj=blacklisted_email)

    ac = AttentionCondition.objects.filter(article=article, code=BLACKLISTED_AUTHOR).first()
    assert ac is not None
    assert ac.status == AttentionCondition.Status.RESOLVED
