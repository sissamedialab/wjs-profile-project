"""Configure this application."""
from django.apps import AppConfig


class WjsReviewConfig(AppConfig):
    """Configuration for this django app."""

    name = "plugins.wjs_review"
    verbose_name = "WJS Review plugin"

    def ready(self):
        """Monkeypatch AccountQuerySet / AccountManager."""
        from core.models import AccountManager, AccountQuerySet

        from . import users

        # Monkeypatch AccountQuerySet / AccountManager to add custom method
        # We have to both classes because to be able to use the function both as Account.objects.filter_reviewers()
        # and Account.objects.all().filter_reviewers()
        AccountManager.filter_reviewers = users.filter_reviewers
        AccountManager.get_reviewers_choices = users.get_reviewers_choices
        AccountManager.exclude_authors = users.exclude_authors
        AccountQuerySet.filter_reviewers = users.filter_reviewers
        AccountQuerySet.get_reviewers_choices = users.get_reviewers_choices
        AccountQuerySet.exclude_authors = users.exclude_authors
