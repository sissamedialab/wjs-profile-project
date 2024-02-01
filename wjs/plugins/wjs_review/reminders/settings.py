"""Settings for reminders: templates & co.

We decided not to use journal settings because
- there are already very many settings
- don't expect reminders' texts to change often (we estimate less than once a year)
- the texts are templates, which require some care (not all user can change them)
- the texts are templates, which must be synchonized with their context
- we don't expect to have per-journal differences
"""

import dataclasses
from typing import Any

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from utils.logger import get_logger

from wjs.jcom_profile.utils import render_template

from ..communication_utils import get_eo_user
from .models import Reminder

logger = get_logger(__name__)


@dataclasses.dataclass
class ReminderSetting:
    """Settings for a reminder.

    Think of the "code" as the ID of a reminder.
    The "target" is attribute name of one of the logic service attributes (usually assigment or article).
    The fields "actor" and "recipient" are attribute names of the target object.
    # TODO: might be better to use the attribute name of something in the service? E.g. review_asignment.editor...

    The fields subject and body are template strings (for django's default template engine).
    """

    code: Reminder.ReminderCodes
    subject: str
    body: str
    actor: str
    recipient: str
    days_after: int
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
        else:
            actor = getattr(self.target_obj, self.actor)
        return actor

    def get_recipient(self, target, journal):
        self.target_obj = target
        if self.recipient == "EO":
            recipient = get_eo_user(journal)
        else:
            recipient = getattr(self.target_obj, self.recipient)
        return recipient


reminders = {
    "DEFAULT": {},
}


def new_reminder(reminder: ReminderSetting, journal="DEFAULT"):
    """Auxiliary function to store a reminder in the "reminders" dictionary by its code."""
    storage = reminders.setdefault(journal, {})
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
        days_after=4,
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
        days_after=7,
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
        days_after=9,
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
        days_after=7,
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
        days_after=11,
    )
)

# // DEFAULTs ends here!
# Per-journal custom: new_reminder(..., journal="JCOM")
