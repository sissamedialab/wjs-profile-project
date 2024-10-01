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
from typing import Any, Optional

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

    The field "subject" is a string, but  "body" is a template strings (for django's default template engine).

    The fields "flag_as..." are passed to the log_operation() function.

    If the list "extracontext" contains any known string, the relative value is added to the context (see the code for
    details).

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
    flag_as_read: bool = True
    """Whether to automatically mark as "read" the Message that will be created when the reminder is sent."""
    flag_as_read_by_eo: bool = True
    extracontext: list = None

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
                    # [date assignment of current version to current editor]
                    template_context.setdefault("assigned", getattr(target, "assigned"))
                elif extracontext_string == "current_editor":
                    # get the current editor of the paper (?) and add it to the context
                    # NB: wanting the editor in the context does _not_ mean that we are writing to the editor!
                    template_context.setdefault("current_editor", getattr(target, "editor"))
                elif extracontext_string == "date_requested":
                    # on [date when editor selected reviewer]
                    template_context.setdefault("date_requested", getattr(target, "date_requested"))
                elif extracontext_string == "reviewer":
                    template_context.setdefault("reviewer", getattr(target, "reviewer"))
                elif extracontext_string == "date_due":
                    template_context.setdefault("date_due", getattr(target, "date_due"))

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

    @classmethod
    def get_settings(cls, reminder: Reminder):
        if cls == ReminderManager:
            mgr = cls.get_manager(reminder)
        else:
            mgr = cls
        return mgr.reminders[reminder.code]

    @classmethod
    def get_manager(cls, reminder: Reminder):
        for subcls in cls.__subclasses__():
            if reminder.code in subcls.reminders:
                return subcls


class EditorShouldSelectReviewerReminderManager(ReminderManager):
    """Helper class to create and delete reminders for EditorShouldSelectReviewer."""

    reminders = {
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
            subject=_("Reminder: reviewers to select"),
            body="""Dear Dr. {{ recipient.full_name }},<br>
<br>
kindly select 2 reviewers as soon as possible from this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.<br>
<br>
This preprint was assigned to you to handle as Editor in charge on {{ assigned }}.<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="editor",
            days_after=0,
            days_after_setting="default_editor_assign_reviewer_days",
            extracontext=["assigned"],
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
            subject=_("Reminder: reviewers to select urgently"),
            body="""Dear Dr. {{ recipient.full_name }},<br>
<br>
This is to remind you that this {{ article.section.name }} needs to be assigned to 2 reviewers urgently.<br>
<a href="{{ article.articleworkflow.url }}">Go to web page</a><br>
<br>
<br>
Thank you very much and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="editor",
            days_after=3,
            days_after_setting="default_editor_assign_reviewer_days",
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            subject=_("Reminder: editor's delay in selecting reviewers"),
            body="""Dear Editor-in-chief,<br>
<br>
This {{ article.section.name }} was assigned to {{ current_editor.full_name }} on {{ assigned }} but they have not yet selected any reviewer.<br>
<br>
Please either take action from this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>. or let us know if you need help.<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="director",
            days_after=5,
            days_after_setting="default_editor_assign_reviewer_days",
            flag_as_read=False,
            flag_as_read_by_eo=False,
            extracontext=["current_editor", "assigned"],
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
            subject=_("Reminder: decision to make"),
            body="""Dear Dr. {{ recipient.full_name }},<br>
<br>
You action is needed to either make a decision or contact another reviewer from this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.<br>
<br>
We note that at least one reviewer's report is available.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="editor",
            days_after=0,
            days_after_setting="default_editor_make_decision_days",
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2,
            subject=_("Reminder: decision to make urgently"),
            body="""Dear Dr. {{ recipient.full_name }},<br>
<br>
This is to remind you that your editor decision is needed urgently. If needed, kindly select another reviewer.<br>
<br>
Go to <a href="{{ article.articleworkflow.url }}">web page</a><br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="editor",
            days_after=3,
            days_after_setting="default_editor_make_decision_days",
        ),
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3: ReminderSetting(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
            subject=_("Reminder: editor's delay in making decision"),
            body="""Dear Editor-in-chief,<br>
<br>
{{ current_editor.full_name }} has received at least one review but has neither made a decision nor selected additional reviewers.<br>
<br>
Please either step in or let us know what we should do from this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="director",
            days_after=5,
            days_after_setting="default_editor_make_decision_days",
            flag_as_read=False,
            flag_as_read_by_eo=False,
            extracontext=["current_editor"],
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
            subject=_("Reminder: Accept/decline Editor's invite"),
            body="""Dear colleague,<br>
<br>
This is to remind you that I need your feedback regarding the invite to review I sent you on {{ date_requested }}.<br>
<br>
Please access all information and files about this {{ article.section.name }} and accept or decline the invite as soon as possible from this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a> to let me know if I can count on your review.<br>
<br>
If you cannot review this manuscript at this time, please decline the invitation and we would be very grateful if you could suggest alternative reviewers.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ current_editor.full_name }}<br>
{{ journal.code }} Editor
""",
            actor="editor",
            recipient="reviewer",
            days_after=0,
            extracontext=["date_requested", "current_editor"],
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
            subject=_("Reminder: Accept/decline Editor's invite (urgent)"),
            body="""Dear colleague,<br>
<br>
Unfortunately I have not yet received your feedback on whether or not you will review the {{ article.section.name }} I sent you on {{ date_requested }}.<br>
<br>
I would be very grateful if you could accept/decline our invitation to review urgently. Information and files about the {{ article.section.name }} are available via its <a href="{{ article.articleworkflow.url }}">web page</a>.<br>
<br>
If you cannot review this manuscript at this time, please decline the invitation and we would be very grateful if you could suggest alternative reviewers.<br>
<br>
{{ journal.code }} knows how important reviewers' work is and greatly appreciates their kind cooperation.<br>
<br>
<br>
Thank you in advance and best regards,<br>
<br>
<br>
{{ current_editor.full_name }}<br>
{{ journal.code }} Editor
""",
            actor="editor",
            recipient="reviewer",
            days_after=3,
            extracontext=["date_requested", "current_editor"],
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
            subject=_("Reviewer's delay in accepting invite"),
            body="""Dear Dr. {{ recipient.full_name }},<br>
<br>
The reviewer {{ reviewer.full_name }} has not yet accepted/declined your invite to review this {{ article.section.name }}.<br>
<br>
You selected them on {{ date_assigned }}.<br>
<br>
We would be grateful if you could step in (send a personal message, change reviewer, assign yourself as reviewer, etc.) from this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="editor",
            days_after=5,
            flag_as_read=True,
            flag_as_read_by_eo=False,
            extracontext=["date_requested", "reviewer"],
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
            subject=_("Reminder: your review due date expires today"),
            body="""Dear colleague,<br>
<br>
We hope that you will be able to send us your review by the end of the day from this  <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.<br>
<br>
From the same page you can:
<ul>
<li> write to the Editor-in-charge  {{ current_editor }} if you need an extension of your review due date
<li> decline to review this {{ article.section.name }} in case anything unexpected happened that makes it really impossible for you to fulfill your promise.
</ul>
Thank you very much in advance for your cooperation and kind regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="reviewer",
            days_after=0,
            clemency_days=2,
            extracontext=["current_editor"],
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2: ReminderSetting(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2,
            subject=_("Reminder: late review"),
            body="""Dear Dr. {{ recipient.full_name }},<br>
<br>
Unfortunately Dr. {{ reviewer.full_name }} has not yet sent us their review, despite our reminder.<br>
<br>
We would be grateful if you could send them a personal message and/or select another reviewer as soon as possible from this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="editor",
            days_after=5,
            clemency_days=0,
            flag_as_read=True,
            flag_as_read_by_eo=False,
            extracontext=["reviewer"],
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
            subject=_("Reminder: revision to submit soon"),
            body="""Dear Author,<br>
<br>
This is to remind you that your revised {{ article.section.name }}'s due date will expire on {{ date_due }}.<br>
<br>
In case you foresee a necessary delay [...]
please contact me from your <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a> to request an extension.<br>
<br>
Please be aware that unsubmitted revisions with no communications from authors are withdrawn from the Journal.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editor in charge
""",
            actor="editor",
            recipient="author",
            days_after=-7,
            flag_as_read=False,
            flag_as_read_by_eo=True,
            extracontext=["date_due"],
        ),
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2,
            subject=_("Reminder: revision due date expires today"),
            body="""Dear Author,<br>
<br>
This is to remind you that your revised {{ article.section.name }}'s due date expires today. [...]
<br>
<br>
Please either submit it by the end of the day or let me know from your <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a> if there are any problems.<br>
<br>
Please be aware that unsubmitted revisions with no communications from authors are withdrawn from the Journal.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editor in charge
""",
            actor="editor",
            recipient="author",
            days_after=0,
            flag_as_read=False,
            flag_as_read_by_eo=True,
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
            subject=_("Reminder: revision to submit soon"),
            body="""Dear Author,<br>
<br>
This is to remind you that your revised {{ article.section.name }}'s due date will expire on {{ date_due }}.<br>
<br>
In case you foresee a necessary delay [...]
please contact me from your <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a> to request an extension.<br>
<br>
Please be aware that unsubmitted revisions with no communications from authors are withdrawn from the Journal.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editor in charge
""",
            actor="editor",
            recipient="author",
            days_after=-7,
            flag_as_read=False,
            flag_as_read_by_eo=True,
            extracontext=["date_due"],
        ),
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2,
            subject=_("Reminder: revision due date expires today"),
            body="""Dear Author,<br>
<br>
This is to remind you that your revised {{ article.section.name }}'s due date expires today. [...]
<br>
<br>
Please either submit it by the end of the day or let me know from your <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a> if there are any problems.<br>
<br>
Please be aware that unsubmitted revisions with no communications from authors are withdrawn from the Journal.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editor in charge
""",
            actor="editor",
            recipient="author",
            days_after=0,
            flag_as_read=False,
            flag_as_read_by_eo=True,
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
            subject=_("Reminder: metadata to update"),
            body="""Dear Author,<br>
<br>
On {{ date_requested }} I allowed you to update your {{ article.section.name }} metadata.<br>
<br>
Please do so urgently from your <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editor in charge
""",
            actor="editor",
            recipient="author",
            days_after=0,
            extracontext=["date_requested"],
        ),
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2: ReminderSetting(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2,
            subject=_("Reminder: metadata to update urgently"),
            body="""Dear Author,<br>
<br>
Please update your {{ article.section.name }} metadata urgently from its <a href="{{ article.articleworkflow.url }}">web page</a>.<br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editor in charge
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
            subject=_("Reminder: editor to select"),
            body="""Dear Editor-in-chief,<br>
<br>
This is to remind you that this {{ article.section.name }} needs to be assigned to an editor in charge as soon as possible.<br>
<br>
Go to <a href="{{ article.articleworkflow.url }}">web page</a><br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="director",
            days_after=0,
        ),
        Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2: ReminderSetting(
            code=Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2,
            subject=_("Reminder: editor to select soon"),
            body="""Dear Editor-in-chief,<br>
<br>
This is another reminder to kindly ask you to assign this {{ article.section.name }} to an editor in charge as soon as possible.<br>
<br>
Go to <a href="{{ article.articleworkflow.url }}">web page</a><br>
<br>
<br>
Thank you and best regards,<br>
<br>
{{ journal.code }} Editorial Office
""",
            actor="EO",
            recipient="director",
            days_after=3,
        ),
    }

    def __init__(self, article: Article):
        self.target = article
        self.journal = article.journal
