"""Utility functions related to the communication system.

Keeping here also anything that we might want to test easily 🙂.
"""

import datetime
from typing import Optional, Union

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, QuerySet
from journal.models import Journal
from plugins.typesetting.models import TypesettingAssignment
from review import models as review_models
from submission.models import Article
from utils.logger import get_logger
from utils.management.commands.test_fire_event import create_fake_request

from wjs.jcom_profile import constants
from wjs.jcom_profile.permissions import has_director_role, has_eo_role
from wjs.jcom_profile.utils import render_template_from_setting

from .models import Message, MessageRecipients, Reminder, WjsEditorAssignment

Account = get_user_model()
logger = get_logger(__name__)

MESSAGE_TYPE_ICONS = {
    Message.MessageTypes.SYSTEM: "bi-gear-fill",
    Message.MessageTypes.HIJACK: "bi-gear-fill",
    Message.MessageTypes.NOTE: "bi-pencil-fill",
    None: "bi-chat-square-text",
}


def get_messages_related_to_me(user: Account, article: Article) -> QuerySet[Message]:
    """Return a queryset of messages that can be of interest to the given user."""
    content_type = ContentType.objects.get_for_model(article)
    object_id = article.id

    _filter = MessageRecipients.objects.filter(
        Q(
            message=OuterRef("id"),
            recipient=user,
            read=True,
        )
        |
        # Messages written are considered "read"
        # This is useful in the timeline sidebar to easily mute/unmute messages by their "read" status
        Q(
            message=OuterRef("id"),
            message__actor=user,
        ),
    )

    # Get messages for this article...
    by_article = Q(Q(content_type=content_type) & Q(object_id=object_id))
    if user.is_superuser or has_eo_role(user) or has_director_role(journal=article.journal, user=user):
        # if I am a director/EO/staff, in that case I see all messages, using a dummy filter
        by_current_user = Q(pk__gt=0)
    else:
        # if they have some relation with me
        by_current_user = Q(Q(recipients__in=[user]) | Q(actor=user))
    # if they are "generic" messages
    generic_message = Q(recipients__isnull=True)
    messages = (
        Message.objects.filter(by_article & Q(by_current_user | generic_message))
        # Hijack notifications are not shown in the timeline as they are a duplicate of the original message
        .exclude(message_type=Message.MessageTypes.HIJACK)
        .distinct()  # because the same msg can have many recipients
        .annotate(read=Exists(_filter))
        .order_by("-created")
    )
    return messages


def get_system_user() -> Account:
    """Return the system user / technical account (wjs-support)."""
    account, _ = Account.objects.get_or_create(
        email="wjs-support@medialab.sissa.it",
        defaults={
            "first_name": "WJS",
            "last_name": "Support",
            "is_staff": True,
        },
    )
    return account


def get_eo_user(obj: Union[Article, Journal]) -> Account:
    """Return the EO system user."""
    if isinstance(obj, Article):
        code = obj.journal.code.lower()
    else:
        code = obj.code.lower()

    email = f"{code}-eo@{code}.sissa.it"
    account, created = Account.objects.get_or_create(
        email=email,
        defaults={
            "username": email,
            "first_name": "",
            "last_name": f"{code.upper()} Editorial Office",
        },
    )
    if created:
        from django.contrib.auth.models import Group

        account.groups.add(Group.objects.get(name=constants.EO_GROUP))
        logger.warning(f"Create system EO account {email}")
    return account


def get_director_user(obj: Union[Article, Journal]) -> Account:
    """Return the director of the journal."""
    journal = getattr(obj, "journal", obj)
    directors = Account.objects.filter(
        accountrole__role__slug=constants.DIRECTOR_ROLE,
        accountrole__journal=journal,
    )
    main_directors = directors.filter(
        accountrole__role__slug=constants.DIRECTOR_MAIN_ROLE,
    )
    if directors.count() == 1:
        return directors.first()
    elif directors.count() > 1:
        if main_directors.count() > 1:
            logger.error(
                f"Journal {journal.code} has {directors.count()} main directors!"
                " Picking a random one, this can have unintended consequences..."
                " Please enroll only one director (manager -> roles -> director-main -> view enrolled users)",
            )
            return main_directors.first()
        elif main_directors.count() == 1:
            return main_directors.first()
        else:
            logger.error(
                f"Journal {journal.code} has no main director, but multiple directors!"
                " Picking a random one, this can have unintended consequences..."
                " With multiple directors, please enroll at most one main director "
                " (manager -> roles -> director-main -> view enrolled users)",
            )
            return directors.order_by("id").first()
    else:
        logger.error(
            f"Journal {journal.code} has no directors!"
            " Using the EO system user and hoping for the best..."
            " Please enrol one director (manager -> enrol users)",
        )
        return get_eo_user(obj)


def log_operation(
    article: Article,
    message_subject: str,
    message_body: str = "",
    actor: Account = None,
    hijacking_actor: Account = None,
    notify_actor: bool = False,
    recipients: list[Account] = None,
    message_type: Message.MessageTypes = Message.MessageTypes.SYSTEM,
    verbosity: Message.MessageVerbosity = Message.MessageVerbosity.FULL,
    flag_as_read: bool = False,
    flag_as_read_by_eo: bool = False,
) -> Message:
    """
    Create a Message to log something. Send out notifications as needed.

    :param article: the article to which the message refers
    :param message_subject: the subject of the message
    :param message_body: the body of the message
    :param actor: the actor of the message
    :param hijacking_actor: the hijacker of the message
    :param notify_actor: whether to notify the actor
    :param recipients: the recipients of the message
    :param message_type: the type of the message
    :param flag_as_read: whether to flag the message as read
    :param flag_as_read_by_eo: whether to flag the message as read by eo

    :return: the created message
    :rtype: Message
    """
    if not actor:
        actor = get_system_user()
        notify_actor = False

    content_type = ContentType.objects.get_for_model(article)
    object_id = article.id
    message = Message.objects.create(
        actor=actor,
        subject=message_subject,
        body=message_body,
        message_type=message_type,
        verbosity=verbosity,
        content_type=content_type,
        object_id=object_id,
        hijacking_actor=hijacking_actor,
        read_by_eo=flag_as_read_by_eo,
    )
    if recipients:
        message.recipients.set(recipients)
    if flag_as_read:
        MessageRecipients.objects.filter(message=message).update(read=True)
    message.emit_notification()
    if notify_actor and hijacking_actor:
        fake_request = create_fake_request(user=None, journal=article.journal)
        hijack_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="hijack_notification_subject",
            journal=article.journal,
            request=fake_request,
            context={"original_subject": message_subject, "original_body": message_body, "hijacker": hijacking_actor},
            template_is_setting=True,
        )
        hijack_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="hijack_notification_body",
            journal=article.journal,
            request=fake_request,
            context={"original_subject": message_subject, "original_body": message_body, "hijacker": hijacking_actor},
            template_is_setting=True,
        )
        log_operation(
            article,
            hijack_subject,
            hijack_body,
            recipients=[actor],
            verbosity=verbosity,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )
    return message


def role_for_article(article: Article, user: Account) -> str:
    """Return a role slug that describes the role of the given user on the article."""
    # TODO: is it possible for a user to have more than one role on one article?
    if user.groups.filter(name=constants.EO_GROUP).exists():
        return "eo"

    if WjsEditorAssignment.objects.filter(editor=user, article=article).exists():
        return "editor"

    if review_models.ReviewAssignment.objects.filter(reviewer=user, article=article).exists():
        return "reviewer"

    if user == article.correspondence_author:
        return "author"

    if user in article.authors.all():
        return "co-author"

    if TypesettingAssignment.objects.filter(round__article=article, typesetter=user).exists():
        return "typesetter"

    return ""


def update_date_send_reminders(assignment: review_models.ReviewAssignment, new_assignment_date_due: datetime.datetime):
    """Update reminders' sending date when the assignment due date changes.

    As per specs#620:
    - If new due date - old due date (Δt) > clemency time
      - all reminders are marked as not sent and their send date is updated by Δt
    - If new due date - old due date (Δt) <= clemency time
      - all non sent reminders send date is updated by Δt
      - all sent reminder are unchanged
    """
    # The business-logic ensures that all reminders that I have reated to this assignment are "good"
    # reminders. I.e. there is no need to distinguish between REEA and REWR reminders (using, for instance, the
    # assignment.date_accepted). I only need to tweak the date_due of all.

    # TODO: can I turn this in to an SQL "UPDATE"?
    reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
    )
    date_due = assignment.date_due if isinstance(assignment.date_due, datetime.date) else assignment.date_due.date()
    delta = new_assignment_date_due - date_due
    for reminder in reminders:
        if delta.days > reminder.clemency_days:
            reminder.date_sent = None
            reminder.date_due += delta
            reminder.save()
        else:
            if reminder.date_sent:
                continue
            else:
                reminder.date_due += delta
                reminder.save()


def should_notify_actor():
    """Tell if we should notify the actor of the message."""
    from core.middleware import GlobalRequestMiddleware

    request = GlobalRequestMiddleware.get_current_request()
    try:
        return not request.session.get("silent_hijack", False)
    except AttributeError:
        # session might not be available in tests / non sync code, in this case we don't want notifications anyway
        return False


def group_messages_by_version(
    article: Article, messages: QuerySet[Message], filters: Optional[dict[str, str]] = None
) -> dict[str, list[Message]]:
    """
    Group messages by version.

    Version is determined by the date of the review round or typesetting round that the message is related to.

    :param article: the article to which the messages refer
    :type article: Article
    :param messages: the messages to group
    :type messages: QuerySet[Message]
    :param filters: filters to apply to the messages
    :type filters: dict[str, str]
    :return: a dictionary where keys are review or typesetting rounds and values are lists of messages.
        All existing versions are included, even if there are no messages related to them.
    :rtype: dict[str, list[Message]]
    """
    if filters:
        for filter_key, filter_value in filters.items():
            if filter_value:
                messages = messages.filter(**{filter_key: filter_value})
    review_rounds = article.reviewround_set.all().order_by("-date_started")
    typesetting_rounds = article.typesettinground_set.all().order_by("-date_created")
    # Create a cumulative list of dates at which each review and typesetting round has stated and order them
    cutoff_dates = {
        **{tr.date_created: tr for tr in typesetting_rounds},
        **{rr.date_started: rr for rr in review_rounds},
    }
    # Prepare a data structure to accomodate messages grouped by review-date
    timeline = {cutoff_dates[d]: [] for d in list(cutoff_dates.keys())}

    # Scan through the ordered messages. Accumulate them under the current date (head) until they become older than it.
    # Then move to the next younger date and accumulate the messages under that one.
    dates = iter(sorted(cutoff_dates.keys(), reverse=True))  # NB: dates are sorted: newest first
    head = next(dates)
    for message in messages.order_by("-created"):  # NB: sort messages also: newset first
        if message.created >= head:
            timeline[cutoff_dates[head]].append(message)
        else:
            try:
                head = next(dates)
            except StopIteration:
                logger.debug(f"Message {message.pk} ({message.created}) is older that oldest round ({head})")
            timeline[cutoff_dates[head]].append(message)

    return timeline
