import importlib

import pytest
from core.models import Account
from django.apps import apps as django_apps
from submission.models import Article, FrozenAuthor

migration = importlib.import_module("wjs.jcom_profile.migrations.0045_clear_salutation")


def _frozen_author(article: Article, account: Account | None, name_prefix: str, order: int = 99) -> FrozenAuthor:
    """Create a frozen author with the given prefix, bypassing any snapshot logic."""
    return FrozenAuthor.objects.create(
        article=article,
        author=account,
        first_name="Jane",
        last_name="Doe",
        name_prefix=name_prefix,
        order=order,
    )


def _set_salutation(account: Account, salutation: str):
    """Set the salutation straight in the DB, as legacy data had it."""
    Account.objects.filter(pk=account.pk).update(salutation=salutation)


@pytest.mark.django_db
def test_migration_clears_account_salutations(article: Article):
    """Every account salutation is blanked."""
    account = article.correspondence_author
    _set_salutation(account, "Dr")

    migration.clear_salutations(django_apps, None)

    assert Account.objects.get(pk=account.pk).salutation == "", "The account salutation must be cleared"


@pytest.mark.django_db
def test_migration_clears_frozen_author_prefix_equal_to_salutation(article: Article):
    """A frozen author prefix copied from the account's salutation is blanked."""
    account = article.correspondence_author
    _set_salutation(account, "Dr")
    frozen_author = _frozen_author(article, account, "Dr")

    migration.clear_salutations(django_apps, None)

    frozen_author.refresh_from_db()
    assert frozen_author.name_prefix == "", "The prefix copied from the salutation must be cleared"


@pytest.mark.django_db
def test_migration_keeps_frozen_author_prefix_different_from_salutation(article: Article):
    """A prefix that is not the account's salutation is a real prefix: it stays."""
    account = article.correspondence_author
    _set_salutation(account, "Dr")
    frozen_author = _frozen_author(article, account, "Prof.")

    migration.clear_salutations(django_apps, None)

    frozen_author.refresh_from_db()
    assert frozen_author.name_prefix == "Prof.", "A prefix different from the salutation must be left alone"
    assert Account.objects.get(pk=account.pk).salutation == "", "The account salutation must still be cleared"


@pytest.mark.django_db
def test_migration_handles_frozen_author_without_account(article: Article):
    """A frozen author not linked to any account is left alone and does not break the migration."""
    frozen_author = _frozen_author(article, None, "Dr")

    migration.clear_salutations(django_apps, None)

    frozen_author.refresh_from_db()
    assert frozen_author.name_prefix == "Dr", "A frozen author without account has no salutation to compare with"


@pytest.mark.django_db
def test_migration_is_idempotent(article: Article):
    """Running the migration twice gives the same result as running it once."""
    account = article.correspondence_author
    _set_salutation(account, "Dr")
    frozen_author = _frozen_author(article, account, "Dr")

    migration.clear_salutations(django_apps, None)
    migration.clear_salutations(django_apps, None)

    frozen_author.refresh_from_db()
    assert frozen_author.name_prefix == ""
    assert Account.objects.get(pk=account.pk).salutation == ""


@pytest.mark.django_db
def test_migration_leaves_empty_prefix_and_salutation_alone(article: Article):
    """An empty prefix on an account with an empty salutation is neither touched nor an error."""
    account = article.correspondence_author
    _set_salutation(account, "")
    frozen_author = _frozen_author(article, account, "")

    migration.clear_salutations(django_apps, None)

    frozen_author.refresh_from_db()
    assert frozen_author.name_prefix == ""
    assert Account.objects.get(pk=account.pk).salutation == ""
