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
import inspect
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from journal.models import Journal
from submission.models import Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import render_template

from ..communication_utils import get_director_user, get_eo_user
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

    The fields "subject" and "body" are template strings (for django's default template engine).

    """

    code: Reminder.ReminderCodes
    subject: str
    body: str
    actor: str
    recipient: str
    days_after: int = dataclasses.field(default=0)
    """
    Number of days after the due date of the target object when the reminder should be sent.
    """
    days_after_setting: Optional[str] = None
    """
    Date due setting: if set, its value is added to days_after field: it's meant for reminders targeting objects
    which does not have a due date field. In this case we use a setting to determine the target due date, and
    the reminder is sent after days_after days from that date.
    """
    days_after_setting_group: str = dataclasses.field(default="wjs_review")
    clemency_days: int = 0

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

    @classmethod
    def build_context(cls, target):
        """Build a context suitable to render subject and body."""
        # NB: do _not_ "cache" the context on the ReminderSetting instance (self). See above.
        template_context = cls.target_as_dict(target)
        template_context.setdefault("target", target)
        # Let's also provide explicitly article and journal if we have them
        if workflow := getattr(target, "workflow", None):
            template_context.setdefault("article", workflow.article)
            template_context.setdefault("journal", workflow.article.journal)
        return template_context

    def get_rendered_subject(self, target):
        context = self.build_context(target)
        return render_template(self.subject, context)

    def get_rendered_body(self, target):
        context = self.build_context(target)
        return render_template(self.body, context)

    def _get_date_base_setting(self, journal: Journal) -> Optional[int]:
        if self.days_after_setting:
            return get_setting(
                self.days_after_setting_group,
                self.days_after_setting,
                journal,
            ).processed_value

    def get_date_due(self, target, journal):
        base_date = timezone.now()
        if offset_days := self._get_date_base_setting(journal):
            base_date += timezone.timedelta(days=offset_days)
        date_due = getattr(target, "date_due", base_date)
        date_due += timezone.timedelta(days=self.days_after)
        return date_due

    def get_actor(self, target, journal):
        if self.actor == "EO":
            actor = get_eo_user(journal)
        elif self.actor == "director":
            actor = get_director_user(journal)
        else:
            actor = getattr(target, self.actor)
        return actor

    def get_recipient(self, target, journal):
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
    reminders: dict[str, ReminderSetting]

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


class EditorShouldSelectReviewerReminderManager(ReminderManager):
    """Helper class to create and delete reminders for EditorShouldSelectReviewer."""

    reminders = {
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please select reviewer"),
            body="""Dear editor,
            please select a reviewer.
            Bests,
            EO
            """,
            actor="EO",
            recipient="editor",
            days_after=0,
            days_after_setting="default_editor_assign_reviewer_days",
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please select reviewer"),
            body="""Dear editor,
                    please select a reviewer! N'edemo dei ciò!!!
                    Bests,
                    EO
                    """,
            actor="EO",
            recipient="editor",
            days_after=3,
            days_after_setting="default_editor_assign_reviewer_days",
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            subject=_(
                "[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Editor is late in selecting a reviewer"
            ),
            body="""Dear director,
                the editor non se movi. Cosa femo?
                Bests,
                EO
                """,
            actor="EO",
            recipient="director",
            days_after=5,
            days_after_setting="default_editor_assign_reviewer_days",
        ),
    }

    def __init__(self, article: Article, editor: Account):
        self.target = WjsEditorAssignment.objects.get(
            article=article,
            editor=editor,
        )
        self.journal = article.journal


class EditorShouldMakeDecisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for EditorShouldMakeDecision."""

    reminders = {
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please make a decision"),
            body="""Dear ,

            Bests,
            """,
            actor="EO",
            recipient="editor",
            days_after=0,
            days_after_setting="default_editor_make_decision_days",
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please make a decision"),
            body="""Dear ,

            Bests,
            """,
            actor="EO",
            recipient="editor",
            days_after=3,
            days_after_setting="default_editor_make_decision_days",
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
            subject=_(
                "[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Editor il late in making a decision"
            ),
            body="""Dear ,

            Bests,
            """,
            actor="EO",
            recipient="director",
            days_after=5,
            days_after_setting="default_editor_make_decision_days",
        ),
    }

    def __init__(self, article: Article, editor: Account):
        self.target = WjsEditorAssignment.objects.get(
            article=article,
            editor=editor,
        )
        self.journal = article.journal


class ReviewerShouldEvaluateAssignmentReminderManager(ReminderManager):
    """Helper class to create and delete reminders for ReviewerShouldEvaluateAssignment."""

    reminders = {
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
            subject=_(
                "[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - review request accept/decline overdue",
            ),
            body="""Dear Marco Caco,
            come xe?

            Bests,
            la tua mammina
            """,
            actor="editor",
            recipient="reviewer",
            days_after=0,
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
            subject=_(
                "[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - review request accept/decline is late",
            ),
            body="""Dear Marco Caco,
            come xe? Cos te fa? Te sta ben???

            'ndemo dei!,
            Toio
            """,
            actor="editor",
            recipient="reviewer",
            days_after=3,
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
            subject=_(
                "[{{ journal.code }}] {{ article.section.name }} {{ article.id }} "
                "- review request accept/decline very late",
            ),
            body="""Dear Toio,
            gavemo perso Marco Caco!
            Cosa femo? Lo lancemo?

            Basi,
            la tua mammina
            """,
            actor="EO",
            recipient="editor",
            days_after=5,
        ),
    }

    def __init__(self, assignment: WorkflowReviewAssignment):
        self.target = assignment
        self.journal = assignment.article.journal


class ReviewerShouldWriteReviewReminderManager(ReminderManager):
    """Helper class to create and delete reminders for ReviewerShouldWriteReview."""

    reminders = {
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - review request overdue"),
            body="""Dear Marco Caco,
            come xe? 'ndemo dei con 'sto report!

            Bests,
            Toio - editor
            """,
            actor="editor",
            recipient="reviewer",
            days_after=0,
            clemency_days=2,
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - review request is late"),
            body="""Dear Toio,
            gavemo perso Marco Caco!
            Cosa femo? Lo lancemo?

            Basi,
            la tua mammina
            """,
            actor="EO",
            recipient="editor",
            days_after=5,
            clemency_days=0,
        ),
    }

    def __init__(self, assignment: WorkflowReviewAssignment):
        self.target = assignment
        self.journal = assignment.article.journal


class AuthorShouldSubmitMajorRevisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for AuthorShouldSubmitMajorRevision."""

    reminders = {
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_1: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_1,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please submit a revision"),
            body="""Dear ,

            Bests,
            """,
            actor="editor",
            recipient="author",
            days_after=-7,
        ),
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please submit a revision"),
            body="""Dear ,

            Bests,
            """,
            actor="editor",
            recipient="author",
            days_after=0,
        ),
    }

    def __init__(self, revision_request: EditorRevisionRequest):
        self.target = revision_request
        self.journal = revision_request.article.journal


class AuthorShouldSubmitMinorRevisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for AuthorShouldSubmitMinorRevision."""

    reminders = {
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_1: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_1,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please submit a revision"),
            body="""Dear ,

            Bests,
            """,
            actor="editor",
            recipient="author",
            days_after=-7,
        ),
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please submit a revision"),
            body="""Dear ,

            Bests,
            """,
            actor="editor",
            recipient="author",
            days_after=0,
        ),
    }

    def __init__(self, revision_request: EditorRevisionRequest):
        self.target = revision_request
        self.journal = revision_request.article.journal


class AuthorShouldSubmitTechnicalRevisionReminderManager(ReminderManager):
    """Helper class to create and delete reminders for AuthorShouldSubmitTechnicalRevisionReminderManager."""

    reminders = {
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please submit a revision"),
            body="""Dear ,

            Bests,
            """,
            actor="editor",
            recipient="author",
            days_after=0,
        ),
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please submit a revision"),
            body="""Dear ,

            Bests,
            """,
            actor="editor",
            recipient="author",
            days_after=1,
        ),
    }

    def __init__(self, revision_request: EditorRevisionRequest):
        self.target = revision_request
        self.journal = revision_request.article.journal


class DirectorShouldAssignEditorReminderManager(ReminderManager):
    """Helper class to create and delete reminders for DirectorShouldAssignEditorReminderManager."""

    reminders = {
        Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_1: ReminderSetting(
            code=Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_1,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please assign an editor"),
            body="""Dear ,

            Bests,
            """,
            actor="EO",
            recipient="director",
            days_after=0,
        ),
        Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2: ReminderSetting(
            code=Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2,
            subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please assign an editor"),
            body="""Dear ,

            Bests,
            """,
            actor="EO",
            recipient="director",
            days_after=3,
        ),
    }

    def __init__(self, article: Article):
        self.target = article
        self.journal = article.journal
