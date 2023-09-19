from django.apps import AppConfig


class WjsReviewConfig(AppConfig):
    """Configuration for this django app."""

    name = "plugins.wjs_review"
    verbose_name = "WJS Review plugin"

    def ready(self):
        """Monkeypatch AccountQuerySet / AccountManager."""
        from core.models import AccountManager, AccountQuerySet

        from . import signals, users  # noqa: F401

        # Monkeypatch AccountQuerySet / AccountManager to add custom method
        # We have to both classes because to be able to use the function both as Account.objects.filter_reviewers()
        # and Account.objects.all().filter_reviewers()
        AccountManager.filter_reviewers = users.filter_reviewers
        AccountManager.get_reviewers_choices = users.get_reviewers_choices
        AccountManager.exclude_authors = users.exclude_authors
        AccountManager.annotate_is_author = users.annotate_is_author
        AccountManager.annotate_is_active_reviewer = users.annotate_is_active_reviewer
        AccountManager.annotate_is_past_reviewer = users.annotate_is_past_reviewer

        AccountQuerySet.filter_reviewers = users.filter_reviewers
        AccountQuerySet.get_reviewers_choices = users.get_reviewers_choices
        AccountQuerySet.exclude_authors = users.exclude_authors
        AccountQuerySet.annotate_is_author = users.annotate_is_author
        AccountQuerySet.annotate_is_active_reviewer = users.annotate_is_active_reviewer
        AccountQuerySet.annotate_is_past_reviewer = users.annotate_is_past_reviewer

        AccountManager.annotate_has_currently_completed_review = users.annotate_has_currently_completed_review
        AccountManager.annotate_has_previously_completed_review = users.annotate_has_previously_completed_review
        AccountManager.annotate_declined_current_review_round = users.annotate_declined_current_review_round
        AccountManager.annotate_declined_previous_review_round = users.annotate_declined_previous_review_round
        AccountManager.annotate_worked_with_me = users.annotate_worked_with_me

        AccountQuerySet.annotate_has_currently_completed_review = users.annotate_has_currently_completed_review
        AccountQuerySet.annotate_has_previously_completed_review = users.annotate_has_previously_completed_review
        AccountQuerySet.annotate_declined_current_review_round = users.annotate_declined_current_review_round
        AccountQuerySet.annotate_declined_previous_review_round = users.annotate_declined_previous_review_round
        AccountQuerySet.annotate_worked_with_me = users.annotate_worked_with_me

        self.register_events()

    def register_events(self):
        """Register our function in Janeway's events logic."""
        from events import logic as events_logic

        from .events import ReviewEvent
        from .events.handlers import on_article_submitted, on_workflow_submitted

        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_SUBMITTED,
            on_article_submitted,
        )

        events_logic.Events.register_for_event(
            ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED,
            on_workflow_submitted,
        )
