"""WJS Review and related models."""
from typing import Optional

from core import models as core_models
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db import models
from django.db.models import QuerySet
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_fsm import GET_STATE, FSMField, transition
from journal.models import Journal
from model_utils.models import TimeStampedModel
from review.models import ReviewAssignment, ReviewRound, RevisionRequest
from submission.models import Article
from utils.logger import get_logger

from . import permissions
from .reminders.models import Reminder  # noqa F401

logger = get_logger(__name__)

Account = get_user_model()


def process_submission(workflow, **kwargs) -> "ArticleWorkflow.ReviewStates":
    """
    Verify and assign a submitted article to an editor.
    """
    from .events.handlers import dispatch_checks

    article = workflow.article
    success = dispatch_checks(article)
    if success is True:
        return workflow.ReviewStates.EDITOR_SELECTED
    elif success is False:
        return workflow.ReviewStates.EDITOR_TO_BE_SELECTED
    else:
        return workflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES


class ArticleWorkflow(TimeStampedModel):
    class ReviewStates(models.TextChoices):
        EDITOR_TO_BE_SELECTED = "EditorToBeSelected", _("Editor to be selected")
        EDITOR_SELECTED = "EditorSelected", _("Editor selected")
        SUBMITTED = "Submitted", _("Submitted")
        TO_BE_REVISED = "ToBeRevised", _("To be revised")
        WITHDRAWN = "Withdrawn", _("Withdrawn")
        REJECTED = "Rejected", _("Rejected")
        INCOMPLETE_SUBMISSION = "IncompleteSubmission", _("Incomplete submission")
        NOT_SUITABLE = "NotSuitable", _("Not suitable")
        PAPER_HAS_EDITOR_REPORT = "PaperHasEditorReport", _("Paper has editor report")
        ACCEPTED = "Accepted", _("Accepted")
        WRITEME_PRODUCTION = "WritemeProduction", _("Writeme production")
        PAPER_MIGHT_HAVE_ISSUES = "PaperMightHaveIssues", _("Paper might have issues")

    class Decisions(models.TextChoices):
        """Decisions that can be made by the editor."""

        ACCEPT = "accept", _("Accept")
        REJECT = "reject", _("Reject")
        MINOR_REVISION = "minorRevision", _("Minor revision")
        MAJOR_REVISION = "majorRevision", _("Major revision")
        NOT_SUITABLE = "not_suitable", _("Not suitable")

    article = models.OneToOneField("submission.Article", verbose_name=_("Article"), on_delete=models.CASCADE)
    # author start submission of paper
    state = FSMField(default=ReviewStates.INCOMPLETE_SUBMISSION, choices=ReviewStates.choices, verbose_name=_("State"))
    eo_in_charge = models.ForeignKey(
        Account,
        verbose_name=_("EO in charge"),
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = _("Article workflow")
        verbose_name_plural = _("Article workflows")

    @property
    def article_authors(self) -> QuerySet[Account]:
        authors = self.article.authors.all()
        if self.article.correspondence_author:
            authors |= Account.objects.filter(pk=self.article.correspondence_author.pk)
        return authors

    def __str__(self):
        return f"{self.article.id}-{self.state}"

    # director selects editor
    @transition(
        field=state,
        source=ReviewStates.EDITOR_TO_BE_SELECTED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_editor,
        # TODO: conditions=[],
    )
    def director_selects_editor(self):
        pass

    # ed declines assignment
    @transition(
        field=state,
        source=ReviewStates.EDITOR_SELECTED,
        target=ReviewStates.EDITOR_TO_BE_SELECTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def ed_declines_assignment(self):
        pass

    # author submits paper
    @transition(
        field=state,
        source=ReviewStates.INCOMPLETE_SUBMISSION,
        target=ReviewStates.SUBMITTED,
        permission=permissions.is_author,
        # TODO: conditions=[],
    )
    def author_submits_paper(self):
        pass

    # system verifies forgery
    # system detects issues in paper
    # system selects editor - success
    # system selects editor - fail
    # and assigns editor
    @transition(
        field=state,
        source=ReviewStates.SUBMITTED,
        target=GET_STATE(
            process_submission,
            states=[
                ReviewStates.EDITOR_SELECTED,
                ReviewStates.EDITOR_TO_BE_SELECTED,
                ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ],
        ),
        permission=permissions.is_system,
        # TODO: conditions=[],
    )
    def system_process_submission(self):
        pass

    # admin deems issues not important
    # TODO: in the diagram, the automatic selection of the editor is triggered atuomatically
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.EDITOR_TO_BE_SELECTED,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_deems_issues_not_important(self):
        pass

    # editor rejects paper
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.REJECTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_rejects_paper(self):
        pass

    # editor deems paper not suitable
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.NOT_SUITABLE,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_deems_paper_not_suitable(self):
        pass

    # editor requires a revision
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.TO_BE_REVISED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_requires_a_revision(self):
        pass

    # editor accepts paper
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.ACCEPTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_accepts_paper(self):
        pass

    # editor writes editor report
    @transition(
        field=state,
        source=ReviewStates.EDITOR_SELECTED,
        target=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_writes_editor_report(self):
        pass

    # admin opens an appeal
    @transition(
        field=state,
        source=ReviewStates.REJECTED,
        target=ReviewStates.TO_BE_REVISED,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_opens_an_appeal(self):
        pass

    # author submits again
    @transition(
        field=state,
        source=ReviewStates.TO_BE_REVISED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_author,
        # TODO: conditions=[],
    )
    def author_submits_again(self):
        pass

    # admin deems paper not suitable
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.NOT_SUITABLE,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_deems_paper_not_suitable(self):
        pass

    # admin or system requires revision
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.INCOMPLETE_SUBMISSION,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_or_system_requires_revision(self):
        pass

    # editor assign different editor
    @transition(
        field=state,
        source=ReviewStates.EDITOR_SELECTED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_assign_different_editor(self):
        pass


class EditorDecision(TimeStampedModel):
    workflow = models.ForeignKey(
        ArticleWorkflow,
        verbose_name=_("Article workflow"),
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    review_round = models.ForeignKey("review.ReviewRound", verbose_name=_("Review round"), on_delete=models.PROTECT)
    decision = models.CharField(max_length=255, choices=ArticleWorkflow.Decisions.choices)
    decision_editor_report = models.TextField(blank=True, null=True)
    decision_internal_note = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = _("Editor decision")
        verbose_name_plural = _("Editor decisions")
        unique_together = ("workflow", "review_round")

    def __str__(self):
        return f"{self.decision} (Article {self.workflow.article.id}-{self.review_round.round_number})"


class Message(TimeStampedModel):
    """A generic message.

    Could be:
    - a workflow action (paper submitted, revision requested,...)
    - a communication (editor assigns paper, author inquires,...)
    - a note (an EO note, an editor note,...)

    This is very similar to utils.LogEntry, but a list of recipients of the message is added, so that messages can be
    filtered by recipient.

    """

    class MessageTypes(models.TextChoices):
        # generic system actions (STD & SILENT)
        STD = "Standard", _("Standard message (notifications are sent)")
        SILENT = "Silent", _("Silent message (no notification is sent)")

        # Verbose notifications are useful for messages such as
        # - editor removal,
        # - reviewer removal,
        # - acknowledgment / thank-you messages,
        # etc., where the recipient is not required to do anything. So, having the full message in the notification
        # email saves a click to web page just to see an uninteresting message.
        VERBOSE = "Verbose", _("Write all the body in the notification email.")
        # Used for
        # - invite reviewer
        # - request revision
        # - ...
        VERBINE = "Verbose ma non troppo", _("Add the first 10 lines of the body to the message")

        SYSTEM = "System log message", _("A system message")
        HIJACK = "User hijacked action log message", _("A hijacking notification message")

        # No need to replace `message_types` w/ some kind of numeric `message_length` (to indicate, for instance, the
        # number of lines to include into the notification)

    actor = models.ForeignKey(
        Account,
        on_delete=models.DO_NOTHING,
        related_name="authored_messages",
        verbose_name="from",
        help_text="The author of the message (for system message, use wjs-support account)",
        null=False,
    )
    hijacking_actor = models.ForeignKey(
        Account,
        on_delete=models.DO_NOTHING,
        related_name="authored_messages_as_hijacker",
        verbose_name="hijacker",
        help_text="The real author of the message (if actor has been hijacked)",
        null=True,
        blank=True,
    )
    recipients = models.ManyToManyField(
        to=Account,
        through="MessageRecipients",
        related_name="received_messages",
    )
    subject = models.TextField(
        blank=True,
        default="",
        max_length=111,
        verbose_name="subject",
        help_text="A short description of the message or the subject of the email.",
    )
    body = models.TextField(
        blank=True,
        default="",
        max_length=1111,
        help_text="The content of the message.",
    )
    message_type = models.TextField(
        choices=MessageTypes.choices,
        default=MessageTypes.STD,
        verbose_name="type",
        help_text="The type of the message: std messages trigger notifications, silent ones do not.",
    )
    # Do we want to manage very detailed ACLs?
    # :START:
    # nope   acl = models.TextField(
    # nope       default="111",
    # nope       verbose_name="Access Control List",
    # nope       help_text="1 means visible, 0 means not-visible. The position indicates editor, reviewer, author",
    # nope   )
    #        :OR:
    # nope   visible = models.BooleanField(default=True)
    # nope   by_who = models.ForeignKey(Account, on_delete=models.CASCADE)
    #        :OR:
    # with the "through" model (see below)
    # :END:

    # A message should have a "target", i.e. it should be related either to an Article (e.g. communications between
    # editor and reviewer, EO and editor,...) or to a Journal (e.g. communications between editor and director).
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=False,
    )
    object_id = models.PositiveIntegerField(
        blank=False,
        null=False,
    )
    target = GenericForeignKey(
        "content_type",
        "object_id",
    )
    # Attachments
    attachments = models.ManyToManyField(
        to=core_models.File,
        null=True,
        blank=True,
    )
    read_by_eo = models.BooleanField(
        default=False,
        help_text="True when a member of the EO marks as read a message exchanged by other two actors",
    )
    # number of chars to show in a "VERBINE" message
    verbine_lenght = 111

    # TODO: do we need these indexes?
    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        # Including recipients here may give max-recursion error if recipients.add is called before self is saved in DB
        # Was `return ... '; '.join([str(x) for x in self.recipients.all()])`
        return f"{self.actor} {self.notification_line}"

    @property
    def notification_line(self):
        """Return a string suitable to be shown in a notification."""
        return self.subject if self.subject else self.body[: Message.verbine_lenght]

    def to_timeline_line(self, anonymous=False):
        """Return a string suitable to be shown in a timeline."""
        # TODO: better here or in a template?
        # TODO: return a dict? a rendered template? mia nona in cariola?
        message = self.subject if self.subject else self.body[:111]
        if anonymous:
            return {
                "from": "",
                "to": "",
                "message": message,
            }
        else:
            return {
                "from": self.actor,
                "to": ",".join(self.recipients),
                "message": message,
            }

    def get_url(self, recipient: Account) -> str:
        """Return the URL to be embedded in the notification email for the given recipient."""
        if self.message_type == Message.MessageTypes.SILENT:
            logger.error(f"No need to get an URL for silent messages (requested for msg {self.id})")
            return ""

        if self.content_type.model_class() == Journal:
            return reverse("wjs_my_messages")

        assert self.content_type.model_class() == Article
        # TODO: hmmm... recipient not used at the moment...
        return self.target.url

    def get_subject_prefix(self) -> str:
        """Get a prefix string for the notification subject (e.g. [JCOM])."""
        if isinstance(self.target, Article):
            return f"[{self.target.journal.code}]"
        else:
            return f"[{self.target.code}]"

    def emit_notification(self, from_email=None):
        """Send a notification.

        :param from_email is passed directly to django.core.mail.send_mail (therefore, if it's None, the
        DEFAULT_FROM_EMAIL is used).

        """
        # TODO: add to the create function of a custom manager? overkill?
        # TODO: use src/utils/notify.py::notification ?
        # (see also notify_hook loaded per-plugin in src/core/include_urls.py)
        if self.message_type == Message.MessageTypes.SILENT:
            return

        # TODO: move header and footer to journal setting?
        notification_header = _("This is an automatic notification. Please do not reply.\n\n")
        notification_footer = _("\n\nPlease visit {url}\n")

        notification_subject = self.subject if self.subject else self.body[:111]
        notification_subject = f"🦄 {self.get_subject_prefix()} {notification_subject}"

        if self.message_type == Message.MessageTypes.VERBOSE:
            notification_body = self.body
        else:
            notification_body = self.body[:111]

        for recipient in self.recipients.all():
            send_mail(
                notification_subject,
                notification_header
                + notification_body
                + notification_footer.format(
                    url=self.get_url(recipient),
                ),
                # TODO: use fake "no-reply": the mailbox should be real, but with an autoresponder
                from_email,
                [recipient.email],
                fail_silently=False,
            )


class MessageRecipients(models.Model):
    """The m2m relation between a message and its recipients."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE)
    recipient = models.ForeignKey(Account, on_delete=models.CASCADE)

    read = models.BooleanField(
        default=False,
        help_text="True only if the message has been read by this recipient.",
    )
    # Hmmmm... the following won't work...
    protected = models.BooleanField(
        default=False,
        help_text="When True, the name of this recipient will not be shown.",
    )


class EditorRevisionRequest(RevisionRequest):
    """Extend Janeway's RevisionRequest model to add review round reference."""

    review_round = models.OneToOneField("review.ReviewRound", verbose_name=_("Review round"), on_delete=models.PROTECT)
    cover_letter_file = models.FileField(blank=True, null=True, verbose_name=_("Cover letter file"))
    article_history = models.JSONField(blank=True, null=True, verbose_name=_("Article history"))
    manuscript_files = models.ManyToManyField("core.File", null=True, blank=True, related_name="+")
    data_figure_files = models.ManyToManyField("core.File", null=True, blank=True, related_name="+")
    supplementary_files = models.ManyToManyField("core.SupplementaryFile", null=True, blank=True, related_name="+")
    source_files = models.ManyToManyField(
        "core.File",
        blank=True,
        related_name="+",
    )


class WorkflowReviewAssignment(ReviewAssignment):
    """
    Extend Janeway's ReviewAssignment model to add author cover letter permissions.

    This model will usually be accessed by using its reference in ReviewAssignment:

    - `review_assignment.workflowreviewassignment.author_note_file`
    - `review_assignment.workflowreviewassignment.author_note_text`

    because in most cases we are going to use janeway's views and templates as a base where the original model is used.

    This is not a big deal as we don't have performance concerns in these templates.
    """

    author_note_visible = models.BooleanField(_("Author note visible"), default=True)

    @property
    def previous_review_round(self) -> Optional[ReviewRound]:
        """Return the previous review round."""
        if self.review_round.round_number < 2:
            return None
        return ReviewRound.objects.filter(
            article=self.article,
            round_number=self.review_round.round_number - 1,
        ).first()
