"""Settings for reminders: templates & co.

We decided not to use journal settings because
- there are already very many settings
- don't expect reminders' texts to change often (we estimate less than once a year)
- the texts are templates, which require some care (not all user can change them)
- the texts are templates, which must be synchonized with their context
- we don't expect to have per-journal differences
"""

import dataclasses
import inspect
from typing import Any, List

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from journal.models import Journal
from review.models import EditorAssignment, ReviewAssignment
from utils.logger import get_logger

from wjs.jcom_profile.utils import render_template

from ..communication_utils import get_director_user, get_eo_user
from ..models import WorkflowReviewAssignment
from .models import Reminder

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
    days_after: int  # NB: the number of days _after_ the due_date of the target!
    target_obj: Any = None
    clemency_days: int = 0

    # TODO: make into classmethod?
    # TODO: make all these methods into classmethods?
    def target_as_dict(self):
        """Use the target to build a context-suitable dictionary."""
        context = {}
        if workflow := getattr(self.target_obj, "workflow", None):
            context.setdefault("workflow", workflow)
            context.setdefault("article", workflow.article)
            context.setdefault("journal", workflow.article.journal)
        if article := getattr(self.target_obj, "article", None):
            context.setdefault("article", article)
            context.setdefault("journal", article.journal)
        for attribute in [
            "journal",
        ]:
            if attribute_value := getattr(self.target_obj, attribute, None):
                context.setdefault(attribute, attribute_value)
        return context

    def build_context(self):
        """Build a context suitable to render subject and body."""
        # NB: do _not_ "cache" the context on the ReminderSetting instance (self). See above.
        template_context = self.target_as_dict()
        template_context.setdefault("target", self.target_obj)
        # Let's also provide explicitly article and journal if we have them
        if workflow := getattr(self.target_obj, "workflow", None):
            template_context.setdefault("article", workflow.article)
            template_context.setdefault("journal", workflow.article.journal)
        return template_context

    def get_rendered_subject(self, target):
        self.target_obj = target
        context = self.build_context()
        return render_template(self.subject, context)

    def get_rendered_body(self, target):
        self.target_obj = target
        context = self.build_context()
        return render_template(self.body, context)

    def get_date_due(self, target):
        self.target_obj = target
        date_due = getattr(self.target_obj, "date_due", timezone.now())
        date_due += timezone.timedelta(days=self.days_after)
        return date_due

    def get_actor(self, target, journal):
        self.target_obj = target
        if self.actor == "EO":
            actor = get_eo_user(journal)
        elif self.actor == "director":
            actor = get_director_user(journal)
        else:
            actor = getattr(self.target_obj, self.actor)
        return actor

    def get_recipient(self, target, journal):
        self.target_obj = target
        if self.recipient == "EO":
            recipient = get_eo_user(journal)
        elif self.recipient == "director":
            recipient = get_director_user(journal)
        else:
            recipient = getattr(self.target_obj, self.recipient)
        return recipient


reminders_configuration = {
    "DEFAULT": {},
}


def new_reminder(reminder: ReminderSetting, journal="DEFAULT"):
    """Auxiliary function to store a reminder in the "reminders" dictionary by its code."""
    storage = reminders_configuration.setdefault(journal, {})
    if reminder.code in storage:
        logger.warning(
            f"Adding settings for reminder {reminder.code} for journal {journal}, but it's already there."
            " You probably have some setup errors."
        )
    storage[reminder.code] = reminder


new_reminder(
    ReminderSetting(
        code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
        subject=_(
            "[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - review request accept/decline overdue",
        ),
        body="""Dear Marco Caco,
            come xe?

            Bests,
            la tua mammina
            """,
        actor="EO",
        recipient="reviewer",
        days_after=1,
    )
)

new_reminder(
    ReminderSetting(
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
    )
)

new_reminder(
    ReminderSetting(
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
        days_after=6,
    )
)

new_reminder(
    ReminderSetting(
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
        clemency_days=3,
    )
)

new_reminder(
    ReminderSetting(
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
        days_after=4,
        clemency_days=3,
    )
)

new_reminder(
    ReminderSetting(
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
    )
)

new_reminder(
    ReminderSetting(
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
    )
)

new_reminder(
    ReminderSetting(
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
    )
)

new_reminder(
    ReminderSetting(
        code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1,
        subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please make a decision"),
        body="""Dear ,

            Bests,
            """,
        actor="EO",
        recipient="editor",
        days_after=0,
    )
)

new_reminder(
    ReminderSetting(
        code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2,
        subject=_("[{{ journal.code }}] {{ article.section.name }} {{ article.id }} - Please make a decision"),
        body="""Dear ,

            Bests,
            """,
        actor="EO",
        recipient="editor",
        days_after=3,
    )
)

new_reminder(
    ReminderSetting(
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
    )
)

# // DEFAULTs ends here!
# Per-journal custom: new_reminder(..., journal="JCOM")


def create_reminder(
    journal: Journal,
    target: [ReviewAssignment, EditorAssignment],
    reminder_code: Reminder.ReminderCodes,
) -> Reminder:
    """Auxiliary function that knows how to create a reminder."""
    storage = reminders_configuration.get(journal.code, reminders_configuration["DEFAULT"])
    reminder_configuration: ReminderSetting = storage.get(reminder_code)

    # TBD: the current solution means that, in case of a configuration problem (i.e. missing the configuration for some
    # reminder), the reminder is _not_ created, we log an error and proceed. We could also raise an exception, but this
    # would disrupt the experience of the user operating the system (e.g. an editor taking a decision).
    if not reminder_configuration:
        logger.error(f"No configuration for reminder {reminder_code} ({journal.code}); working on {target}.")
        return None

    subject = reminder_configuration.get_rendered_subject(target)
    body = reminder_configuration.get_rendered_body(target)
    date_due = reminder_configuration.get_date_due(target)
    actor = reminder_configuration.get_actor(target, journal)
    recipient = reminder_configuration.get_recipient(target, journal)

    reminder = Reminder.objects.create(
        code=reminder_configuration.code,
        message_subject=subject,
        message_body=body,
        content_type=ContentType.objects.get_for_model(target),
        object_id=target.id,
        date_due=date_due,
        clemency_days=reminder_configuration.clemency_days,
        actor=actor,
        recipient=recipient,
    )
    return reminder


def create_EDMD_reminders(review_assignment: ReviewAssignment):  # noqa N802
    """Nomen omen"""
    target = EditorAssignment.objects.get(
        article=review_assignment.article,
        editor=review_assignment.editor,
    )
    journal = review_assignment.article.journal
    create_reminder(journal, target, Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1)
    create_reminder(journal, target, Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2)
    create_reminder(journal, target, Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3)
    # TODO: drop me before going production:
    stack = inspect.stack()
    logger.debug(
        f"Created editor-should-make-decision reminders for {target} in {stack[2].function}::{stack[1].function}",
    )


def create_EDSR_reminders(review_assignment: ReviewAssignment):  # noqa N802
    """Nomen omen"""
    target = EditorAssignment.objects.get(
        article=review_assignment.article,
        editor=review_assignment.editor,
    )
    journal = review_assignment.article.journal
    create_reminder(journal, target, Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1)
    create_reminder(journal, target, Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2)
    create_reminder(journal, target, Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3)
    # TODO: drop me before going production:
    stack = inspect.stack()
    logger.debug(
        f"Created editor-should-select-reviewer reminders for {target} in {stack[2].function}::{stack[1].function}",
    )
