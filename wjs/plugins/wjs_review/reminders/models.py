"""WJS Review and related models."""

from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _
from submission.models import Article
from utils.logger import get_logger

logger = get_logger(__name__)

Account = get_user_model()


class Reminder(models.Model):
    """A message sent to someone to remind him that some due date has elapsed."""

    class ReminderCodes(models.TextChoices):
        # specs#618
        REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1 = "REEA1", _("Reviewer should evaluate assignment")
        REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2 = "REEA2", _("Reviewer should evaluate assignment")
        REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3 = "REEA3", _("Reviewer should evaluate assignment")
        REVIEWER_SHOULD_WRITE_REVIEW_1 = "REWR1", _("Reviewer should write review")
        REVIEWER_SHOULD_WRITE_REVIEW_2 = "REWR2", _("Reviewer should write review")
        # specs#619
        EDITOR_SHOULD_SELECT_REVIEWER_1 = "EDSR1", _("Editor should select reviewer")
        EDITOR_SHOULD_SELECT_REVIEWER_2 = "EDSR2", _("Editor should select reviewer")
        EDITOR_SHOULD_SELECT_REVIEWER_3 = "EDSR3", _("Editor should select reviewer")
        EDITOR_SHOULD_MAKE_DECISION_1 = "EDMD1", _("Editor should make decision")
        EDITOR_SHOULD_MAKE_DECISION_2 = "EDMD2", _("Editor should make decision")
        EDITOR_SHOULD_MAKE_DECISION_3 = "EDMD3", _("Editor should make decision")
        # specs#635
        AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_1 = "AUMJR1", _("Author should submit major revision")
        AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2 = "AUMJR2", _("Author should submit major revision")
        AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_1 = "AUMIR1", _("Author should submit minor revision")
        AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2 = "AUMIR2", _("Author should submit minor revision")
        AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1 = "AUTCR1", _("Author should submit technical revision")
        AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2 = "AUTCR2", _("Author should submit technical revision")
        DIRECTOR_SHOULD_ASSIGN_EDITOR_1 = "DIRASED1", _("Director should assign editor")
        DIRECTOR_SHOULD_ASSIGN_EDITOR_2 = "DIRASED2", _("Director should assign editor")

    code = models.CharField(
        max_length=10,
        choices=ReminderCodes.choices,
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_due = models.DateField()
    date_sent = models.DateTimeField(null=True, blank=True)
    disabled = models.BooleanField(default=False)
    clemency_days = models.IntegerField()

    # The "target" of a reminder can be something like a ReviewAssigment (for reminders to reviewers), an
    # EditorAssignment (for reminders to editors), but also just an Article (e.g. for reminders to EO related to
    # articles with no editor assigned).
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
    )
    object_id = models.PositiveIntegerField(
        null=True,
    )
    target = GenericForeignKey(
        "content_type",
        "object_id",
    )

    recipient = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="reminders_that_i_receive")
    # TODO: it's ok to drop a reminder if the recipient disappears, but the actor might be different...
    # Does the business logic prevent this problem?
    # E.g. to delete the editor, one should first re-assign the article and manage the reviewassignments anyway...
    actor = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="reminders_that_i_send")
    hide_actor_name = models.BooleanField(
        default=True,
        help_text="Hide the name of the actor in the message body / subject / From-header",
    )

    # Subject and body are taken from .settings.reminders.
    # That dictionary should contain the template that will be rendered to create the reminder message.
    # The message is rendered when the reminder is created. This should allow for the editing of existing reminders.
    message_subject = models.TextField()
    message_body = models.TextField()
    # TODO: add message_from_header ?

    def __str__(self):
        return self.code

    def get_from_email(self) -> str:
        """Compute the "From:" header for the notifcation email.

        The email is always the same, but the name part changes.
        E.g.
        From: Matteo Gamboz <wjs-support@medialab.sissa.it>
        """
        # TODO: hide name sometimes
        name = self.actor.full_name()
        email = settings.DEFAULT_FROM_EMAIL
        from_header = f"{name} <{email}>"
        return from_header

    def get_related_article(self) -> Optional[Article]:
        """Try to find the article that this reminder is related to."""
        if article := getattr(self.target, "article", None):
            return article
        else:
            return None
