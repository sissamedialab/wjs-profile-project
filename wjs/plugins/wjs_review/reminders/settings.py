"""Settings for reminders: templates & co.

We decided not to use journal settings because
- there are already very many settings
- don't expect reminders' texts to change often (we estimate less than once a year)
- the texts are templates, which require some care (not all user can change them)
- the texts are templates, which must be synchonized with their context
- we don't expect to have per-journal differences
"""

import abc
import dataclasses
import datetime
import inspect
from functools import cached_property
from typing import Any, Optional

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.template.loader import select_template
from django.utils import formats, timezone
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _
from journal.models import Journal
from review.models import ReviewRound
from submission.models import Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import get_eo_user, render_template

from ..communication_utils import get_director_user
from ..models import (
    Account,
    EditorRevisionRequest,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

logger = get_logger(__name__)


@dataclasses.dataclass
class ReminderSetting:
    """Settings for a reminder.

    Think of the "code" as the ID or class of a reminder.

    The "target" is an object to which the reminder should be "attached" (usually an assigment).

    The fields "actor" and "recipient" are attribute names of the target object (e.g. reviewer or editor).
    There are two special cases:
    - "EO" means to use the EO system user
    - "director" means to get the director of the journal

    The field "subject" is a string, but  "body" is a template strings (for django's default template engine).

    The fields "flag_as..." are passed to the log_operation() function.

    If the list "extracontext" contains any known string, the relative value is added to the context (see the code for
    details).

    """

    code: Reminder.ReminderCodes
    subject: str
    actor: str
    recipient: str
    days_after: int = dataclasses.field(default=0)
    """
    Number of days after the due date of the target object when the reminder should be sent.
    """
    due_date_offset_setting: Optional[str] = None
    """
    Date due setting: if set, its value is added to days_after field: it's meant for reminders targeting objects
    which does not have a due date field. In this case we use a setting to determine the target due date, and
    the reminder is sent after days_after days from that date.
    """
    due_date_offset_setting_group: str = dataclasses.field(default="wjs_review")
    clemency_days: int = 0
    flag_as_read: bool = True
    """Whether to automatically mark as "read" the Message that will be created when the reminder is sent."""
    flag_as_read_by_eo: bool = True
    extracontext: list = None
    journal: str = None

    @property
    def body(self) -> str:
        """The body of the reminder."""

        template = select_template(
            [
                f"wjs_review/reminders/messages/{self.journal}/{self.code}.html",
                f"wjs_review/reminders/messages/default/{self.code}.html",
            ]
        )

        return template.template.source

    @classmethod
    def target_as_dict(cls, target):
        """Use the target to build a context-suitable dictionary."""
        context = {}
        if workflow := getattr(target, "workflow", None):
            context.setdefault("workflow", workflow)
            context.setdefault("article", workflow.article)
            context.setdefault("journal", workflow.article.journal)
        if article := getattr(target, "article", None):
            context.setdefault("article", article)
            context.setdefault("journal", article.journal)
        for attribute in [
            "journal",
        ]:
            if attribute_value := getattr(target, attribute, None):
                context.setdefault(attribute, attribute_value)
        return context

    def build_context(self, target) -> dict[str, Any]:
        """Build a context suitable to render subject and body."""
        # NB: do _not_ "cache" the context on the ReminderSetting instance (self). See above.
        template_context = self.target_as_dict(target)
        template_context.setdefault("target", target)
        # Let's also provide explicitly article and journal if we have them
        if workflow := getattr(target, "workflow", None):
            template_context.setdefault("article", workflow.article)
            template_context.setdefault("journal", workflow.article.journal)
        elif workflow := getattr(target, "articleworkflow", None):
            template_context.setdefault("article", workflow.article)
            template_context.setdefault("journal", workflow.article.journal)
        elif article := getattr(target, "article", None):
            workflow = article.articleworkflow
            template_context.setdefault("article", article)
            template_context.setdefault("journal", article.journal)
        template_context.setdefault("recipient", self.get_recipient(target, workflow.article.journal))
        if self.extracontext:
            for extracontext_string in self.extracontext:
                if extracontext_string == "assigned":
                    assignment_date = getattr(target, "assigned")
                    try:
                        review_round_date = target.review_rounds.latest("date_started").date_started
                    except ReviewRound.DoesNotExist:
                        review_round_date = datetime.datetime.min
                        logger.error(
                            f"Trying to find ed-assigned date of non-existent review round for {target}. Please check!"
                        )
                    assigned = max(assignment_date, review_round_date)
                    # [date assignment of current version to current editor]
                    template_context.setdefault("assigned", self._format_date(assigned))
                elif extracontext_string == "current_editor":
                    # get the current editor of the paper (?) and add it to the context
                    # NB: wanting the editor in the context does _not_ mean that we are writing to the editor!
                    template_context.setdefault("current_editor", getattr(target, "editor"))
                elif extracontext_string == "date_requested":
                    # on [date when editor selected reviewer]
                    template_context.setdefault(
                        "date_requested",
                        self._format_date(getattr(target, "date_requested")),
                    )
                elif extracontext_string == "reviewer":
                    template_context.setdefault("reviewer", getattr(target, "reviewer"))
                elif extracontext_string == "date_due":
                    template_context.setdefault("date_due", self._format_date(getattr(target, "date_due")))

        return template_context

    def _format_date(self, date_value: datetime.datetime | datetime.date) -> str:
        if isinstance(date_value, datetime.datetime):
            return formats.date_format(localtime(date_value), settings.DATE_FORMAT)
        if isinstance(date_value, datetime.date):
            return formats.date_format(date_value, settings.DATE_FORMAT)

    def get_rendered_subject(self, target):
        context = self.build_context(target)
        return render_template(self.subject, context)

    def get_rendered_body(self, target):
        context = self.build_context(target)
        return render_template(self.body, context)

    def _get_date_base_setting(self, journal: Journal) -> Optional[int]:
        if self.due_date_offset_setting:
            return get_setting(
                self.due_date_offset_setting_group,
                self.due_date_offset_setting,
                journal,
            ).processed_value

    def get_date_due(self, target, journal: Journal):
        base_date = timezone.now()
        if offset_days := self._get_date_base_setting(journal):
            base_date += timezone.timedelta(days=offset_days)
        date_due = getattr(target, "date_due", base_date)
        date_due += timezone.timedelta(days=self.days_after)
        return date_due

    def get_actor(self, target, journal: Journal):
        if self.actor == "EO":
            actor = get_eo_user(journal)
        elif self.actor == "director":
            actor = get_director_user(journal)
        else:
            actor = getattr(target, self.actor)
        return actor

    def get_recipient(self, target, journal: Journal):
        if self.recipient == "EO":
            recipient = get_eo_user(journal)
        elif self.recipient == "director":
            recipient = get_director_user(journal)
        elif isinstance(target, EditorRevisionRequest):
            recipient = target.article.correspondence_author
        else:
            recipient = getattr(target, self.recipient)
        return recipient


class ReminderManager(abc.ABC):
    target: models.Model
    journal: Journal

    @classmethod
    @abc.abstractmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        raise NotImplementedError

    @property
    def reminders(self) -> dict[str, ReminderSetting]:
        return self.get_reminders(journal_code=self.journal.code.lower())

    def __debug(self):
        """Tell who is creating a reminder.

        This method is not used in the production code, but it might be useful to develop/debug
        new reminders.
        """
        stack = inspect.stack()
        logger.debug(
            f"Run {self.__class__.__name__} for {self.target} in {stack[2].function}::{stack[1].function}",
        )

    def _create_reminder(
        self,
        reminder: ReminderSetting,
    ) -> Reminder:
        """Auxiliary function that knows how to create a reminder."""
        # TBD: the current solution means that, in case of a configuration problem (i.e. missing the configuration for some
        # reminder), the reminder is _not_ created, we log an error and proceed. We could also raise an exception, but this
        # would disrupt the experience of the user operating the system (e.g. an editor taking a decision).
        subject = reminder.get_rendered_subject(self.target)
        body = reminder.get_rendered_body(self.target)
        date_due = reminder.get_date_due(self.target, self.journal)
        actor = reminder.get_actor(self.target, self.journal)
        recipient = reminder.get_recipient(self.target, self.journal)

        reminder = Reminder.objects.create(
            code=reminder.code,
            message_subject=subject,
            message_body=body,
            content_type=ContentType.objects.get_for_model(self.target),
            object_id=self.target.id,
            date_due=date_due,
            clemency_days=reminder.clemency_days,
            actor=actor,
            recipient=recipient,
        )
        return reminder

    def create(self):
        for reminder in self.reminders.values():
            self._create_reminder(reminder)

    def delete(self):
        Reminder.objects.filter(
            code__in=self.reminders,
            object_id=self.target.pk,
            content_type=ContentType.objects.get_for_model(self.target),
        ).delete()

    @classmethod
    def get_settings(cls, reminder: Reminder):
        article = reminder.get_related_article()
        if not article:
            msg = f"Unknown article for reminder {reminder.pk} ({reminder.code})"
            raise ValueError(msg)
        if cls == ReminderManager:
            mgr = cls.get_manager(reminder, article)
        else:
            mgr = cls

        reminders = mgr.get_reminders(journal_code=article.journal.code.lower())
        return reminders[reminder.code]

    @classmethod
    def get_manager(cls, reminder: Reminder, article: Article):
        for subcls in cls.__subclasses__():
            if reminder.code in subcls.get_reminders(journal_code=article.journal.code.lower()):
                return subcls


class EditorShouldSelectReviewerReminderManager(ReminderManager):
    """Helper class to create and delete reminders for EditorShouldSelectReviewer."""

    def __init__(self, article: Article, editor: Account):
        self.target = WjsEditorAssignment.objects.get(
            article=article,
            editor=editor,
        )
        self.journal = article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1: ReminderSetting(
                code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                subject=_("Reminder: reviewers to select"),
                actor="EO",
                recipient="editor",
                days_after=0,
                due_date_offset_setting="default_editor_assign_reviewer_days",
                extracontext=["assigned"],
                journal=journal_code,
            ),
            Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2: ReminderSetting(
                code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                subject=_("Reminder: reviewers to select urgently"),
                actor="EO",
                recipient="editor",
                days_after=3,
                due_date_offset_setting="default_editor_assign_reviewer_days",
                journal=journal_code,
            ),
            Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3: ReminderSetting(
                code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
                subject=_("Reminder: editor's delay in selecting reviewers"),
                actor="EO",
                recipient="director",
                days_after=7,
                due_date_offset_setting="default_editor_assign_reviewer_days",
                flag_as_read=False,
                extracontext=["current_editor", "assigned"],
                journal=journal_code,
            ),
        }


class EditorShouldMakeDecisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for EditorShouldMakeDecision."""

    def __init__(self, article: Article, editor: Account):
        self.target = WjsEditorAssignment.objects.get(
            article=article,
            editor=editor,
        )
        self.journal = article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1: ReminderSetting(
                code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1,
                subject=_("Reminder: decision to make"),
                actor="EO",
                recipient="editor",
                days_after=0,
                due_date_offset_setting="default_editor_make_decision_days",
                journal=journal_code,
            ),
            Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2: ReminderSetting(
                code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2,
                subject=_("Reminder: decision to make urgently"),
                actor="EO",
                recipient="editor",
                days_after=3,
                due_date_offset_setting="default_editor_make_decision_days",
                journal=journal_code,
            ),
            Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3: ReminderSetting(
                code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
                subject=_("Reminder: editor's delay in making decision"),
                actor="EO",
                recipient="director",
                days_after=7,
                due_date_offset_setting="default_editor_make_decision_days",
                flag_as_read=False,
                extracontext=["current_editor"],
                journal=journal_code,
            ),
        }


class ReviewerShouldEvaluateAssignmentReminderManager(ReminderManager):
    """Helper class to create and delete reminders for ReviewerShouldEvaluateAssignment."""

    def __init__(self, assignment: WorkflowReviewAssignment):
        self.target = assignment
        self.journal = assignment.article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1: ReminderSetting(
                code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
                subject=_("Reminder: Accept/decline Editor's invite"),
                actor="editor",
                recipient="reviewer",
                days_after=0,
                extracontext=["date_requested", "current_editor"],
                journal=journal_code,
            ),
            Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2: ReminderSetting(
                code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
                subject=_("Reminder: Accept/decline Editor's invite (urgent)"),
                actor="editor",
                recipient="reviewer",
                days_after=3,
                extracontext=["date_requested", "current_editor"],
                journal=journal_code,
            ),
            Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3: ReminderSetting(
                code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
                subject=_("Reviewer's delay in accepting invite"),
                actor="EO",
                recipient="editor",
                days_after=5,
                flag_as_read=True,
                extracontext=["date_requested", "reviewer"],
                journal=journal_code,
            ),
        }


class ReviewerShouldWriteReviewReminderManager(ReminderManager):
    """Helper class to create and delete reminders for ReviewerShouldWriteReview."""

    def __init__(self, assignment: WorkflowReviewAssignment):
        self.target = assignment
        self.journal = assignment.article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1: ReminderSetting(
                code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1,
                subject=_("Reminder: your review due date expires today"),
                actor="EO",
                recipient="reviewer",
                days_after=0,
                clemency_days=2,
                extracontext=["current_editor"],
                journal=journal_code,
            ),
            Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2: ReminderSetting(
                code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2,
                subject=_("Reminder: late review"),
                actor="EO",
                recipient="editor",
                days_after=5,
                clemency_days=0,
                flag_as_read=True,
                extracontext=["reviewer"],
                journal=journal_code,
            ),
        }


class AuthorShouldSubmitMajorRevisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for AuthorShouldSubmitMajorRevision."""

    def __init__(self, revision_request: EditorRevisionRequest):
        self.target = revision_request
        self.journal = revision_request.article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_1: ReminderSetting(
                code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_1,
                subject=_("Reminder: revision to submit soon"),
                actor="editor",
                recipient="author",
                days_after=-7,
                flag_as_read=False,
                extracontext=["date_due"],
                journal=journal_code,
            ),
            Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2: ReminderSetting(
                code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2,
                subject=_("Reminder: revision due date expires today"),
                actor="editor",
                recipient="author",
                days_after=0,
                flag_as_read=False,
                journal=journal_code,
            ),
        }


class AuthorShouldSubmitMinorRevisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for AuthorShouldSubmitMinorRevision."""

    def __init__(self, revision_request: EditorRevisionRequest):
        self.target = revision_request
        self.journal = revision_request.article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_1: ReminderSetting(
                code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_1,
                subject=_("Reminder: revision to submit soon"),
                actor="editor",
                recipient="author",
                days_after=-7,
                flag_as_read=False,
                extracontext=["date_due"],
                journal=journal_code,
            ),
            Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2: ReminderSetting(
                code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2,
                subject=_("Reminder: revision due date expires today"),
                actor="editor",
                recipient="author",
                days_after=0,
                flag_as_read=False,
                journal=journal_code,
            ),
        }


class AuthorShouldSubmitTechnicalRevisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for AuthorShouldSubmitTechnicalRevisionReminderManager."""

    def __init__(self, revision_request: EditorRevisionRequest):
        self.target = revision_request
        self.journal = revision_request.article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1: ReminderSetting(
                code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1,
                subject=_("Reminder: metadata to update"),
                actor="editor",
                recipient="author",
                days_after=0,
                extracontext=["date_requested"],
                journal=journal_code,
            ),
            Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2: ReminderSetting(
                code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2,
                subject=_("Reminder: metadata to update urgently"),
                actor="editor",
                recipient="author",
                days_after=1,
                journal=journal_code,
            ),
        }


class DirectorShouldAssignEditorReminderManager(ReminderManager):
    """Helper class to create and delete reminders for DirectorShouldAssignEditorReminderManager."""

    def __init__(self, article: Article):
        self.target = article
        self.journal = article.journal

    @classmethod
    def get_reminders(cls, journal_code) -> dict[str, ReminderSetting]:
        return {
            Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_1: ReminderSetting(
                code=Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_1,
                subject=_("Reminder: editor to select"),
                actor="EO",
                recipient="director",
                days_after=0,
                journal=journal_code,
            ),
            Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2: ReminderSetting(
                code=Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2,
                subject=_("Reminder: editor to select soon"),
                actor="EO",
                recipient="director",
                days_after=5,
                journal=journal_code,
            ),
        }
