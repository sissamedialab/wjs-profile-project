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
        AccountManager.get_editors_with_keywords = users.get_editors_with_keywords
        AccountManager.exclude_authors = users.exclude_authors
        AccountManager.annotate_is_author = users.annotate_is_author
        AccountManager.annotate_is_active_reviewer = users.annotate_is_active_reviewer
        AccountManager.annotate_is_last_round_reviewer = users.annotate_is_last_round_reviewer

        AccountQuerySet.filter_reviewers = users.filter_reviewers
        AccountQuerySet.get_reviewers_choices = users.get_reviewers_choices
        AccountQuerySet.get_editors_with_keywords = users.get_editors_with_keywords
        AccountQuerySet.exclude_authors = users.exclude_authors
        AccountQuerySet.annotate_is_author = users.annotate_is_author
        AccountQuerySet.annotate_is_active_reviewer = users.annotate_is_active_reviewer
        AccountQuerySet.annotate_is_last_round_reviewer = users.annotate_is_last_round_reviewer

        AccountManager.annotate_has_currently_completed_review = users.annotate_has_currently_completed_review
        AccountManager.annotate_has_completed_review_in_the_previous_round = (
            users.annotate_has_completed_review_in_the_previous_round
        )
        AccountManager.annotate_declined_current_review_round = users.annotate_declined_current_review_round
        AccountManager.annotate_declined_the_previous_review_round = users.annotate_declined_the_previous_review_round
        AccountManager.annotate_worked_with_me = users.annotate_worked_with_me
        AccountManager.annotate_is_prophy_candidate = users.annotate_is_prophy_candidate
        AccountManager.annotate_is_only_prophy = users.annotate_is_only_prophy
        AccountManager.annotate_ordering_score = users.annotate_ordering_score

        AccountQuerySet.annotate_has_currently_completed_review = users.annotate_has_currently_completed_review
        AccountQuerySet.annotate_has_completed_review_in_the_previous_round = (
            users.annotate_has_completed_review_in_the_previous_round
        )
        AccountQuerySet.annotate_declined_current_review_round = users.annotate_declined_current_review_round
        AccountQuerySet.annotate_declined_the_previous_review_round = users.annotate_declined_the_previous_review_round
        AccountQuerySet.annotate_worked_with_me = users.annotate_worked_with_me
        AccountQuerySet.annotate_is_prophy_candidate = users.annotate_is_prophy_candidate
        AccountQuerySet.annotate_is_only_prophy = users.annotate_is_only_prophy
        AccountQuerySet.annotate_ordering_score = users.annotate_ordering_score

        self.register_events()

    def register_events(self):
        """Register our function in Janeway's events logic."""
        from events import logic as events_logic
        from utils import transactional_emails

        from .events import ReviewEvent
        from .events.handlers import (
            clean_prophy_candidates,
            convert_manuscript_to_pdf,
            notify_author_article_submission,
            notify_coauthors_article_submission,
            on_article_submission_start,
            perform_checks_at_acceptance,
            process_submission,
            restart_review_process_after_revision_submission,
            send_to_prophy,
            sync_article_articleworkflow,
        )

        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_SUBMISSION_START,
            on_article_submission_start,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_ARTICLE_SUBMITTED,
            transactional_emails.send_submission_acknowledgement,
        )
        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_SUBMITTED,
            sync_article_articleworkflow,
            notify_author_article_submission,
            notify_coauthors_article_submission,
        )
        events_logic.Events.register_for_event(
            ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED,
            process_submission,
        )
        events_logic.Events.register_for_event(
            events_logic.Events.ON_REVISIONS_COMPLETE,
            restart_review_process_after_revision_submission,
        )
        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_SUBMITTED,
            send_to_prophy,
        )
        events_logic.Events.register_for_event(
            events_logic.Events.ON_REVISIONS_COMPLETE,
            send_to_prophy,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVISIONS_REQUESTED_NOTIFY,
            transactional_emails.send_revisions_request,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVIEW_WITHDRAWL,
            transactional_emails.send_reviewer_withdrawl_notice,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_ARTICLE_ASSIGNED_ACKNOWLEDGE,
            transactional_emails.send_editor_assigned_acknowledgements,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVIEWER_REQUESTED_ACKNOWLEDGE,
            transactional_emails.send_reviewer_requested_acknowledgements,
        )
        # There are two messages/mails that are sent when a reviewer completes a review:
        # - To the reviewer(s) (settings: {subject_,}review_complete_reviewer_acknowledgement)
        # - To the editor(s): (settings: {subject_,}review_complete_acknowledgement)
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVIEW_COMPLETE,
            transactional_emails.send_review_complete_acknowledgements,
        )
        # send_reviewer_accepted_or_decline_acknowledgements() is linked to two separate events:
        # ON_REVIEWER_ACCEPTED and ON_REVIEWER_DECLINED
        # The 4 settings are:
        # {subject_,}review_{accept,decline}_acknowledgement
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVIEWER_ACCEPTED,
            transactional_emails.send_reviewer_accepted_or_decline_acknowledgements,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVIEWER_DECLINED,
            transactional_emails.send_reviewer_accepted_or_decline_acknowledgements,
        )
        # send_article_decision() is linked to three separate events:
        # ON_ARTICLE_ACCEPTED, ON_ARTICLE_DECLINED, ON_ARTICLE_UNDECLINED
        # the 6 settings are:
        # {subject_,}review_decision_{accept,decline,undecline}
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_ARTICLE_ACCEPTED,
            transactional_emails.send_article_decision,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_ARTICLE_DECLINED,
            transactional_emails.send_article_decision,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_ARTICLE_UNDECLINED,
            transactional_emails.send_article_decision,
        )

        # When an article is accepted, verify if it is ready for typesetters
        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_ACCEPTED,
            perform_checks_at_acceptance,
        )

        # Disable these three functions
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVISIONS_COMPLETE,
            transactional_emails.send_revisions_complete,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_REVISIONS_COMPLETE,
            transactional_emails.send_revisions_author_receipt,
        )
        events_logic.Events.unregister_for_event(
            events_logic.Events.ON_ARTICLE_UNASSIGNED,
            transactional_emails.send_editor_unassigned_notice,
        )

        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_PUBLISHED,
            clean_prophy_candidates,
        )
        # both editor rejects and marks not_suitable call janeway decline_article
        # and trigger ON_ARTICLE_DECLINED event
        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_DECLINED,
            clean_prophy_candidates,
        )
        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_FILE_UPLOAD,
            convert_manuscript_to_pdf,
        )
