"""Utility functions related to the communication system.

Keeping here also anything that we might want to test easily 🙂.
"""
from typing import Optional, Union

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.http import HttpRequest
from journal.models import Journal
from review import models as review_models
from submission.models import Article
from utils.logger import get_logger
from utils.management.commands.test_fire_event import create_fake_request

from wjs.jcom_profile.apps import GROUP_EO
from wjs.jcom_profile.permissions import is_eo
from wjs.jcom_profile.utils import render_template_from_setting

from .models import Message, MessageRecipients

Account = get_user_model()
logger = get_logger(__name__)


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
    if is_eo(user) or user.is_superuser:
        # if I am an EO/staff, in that case I see all messages, using a dummy filter
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
        username=email,
        first_name="",
        last_name=f"{code.upper()} Editorial Office",
    )
    if created:
        from django.contrib.auth.models import Group

        account.groups.add(Group.objects.get(name=GROUP_EO))
        logger.warning(f"Create system EO account {email}")
    return account


def get_director_user(obj: Union[Article, Journal]) -> Account:
    """Return the director of the journal."""
    if isinstance(obj, Article):
        journal = obj.journal
    else:
        journal = obj
    # TODO: should we set this somewhere more centralized?
    director_slug = "director"
    directors = Account.objects.filter(
        accountrole__role__slug=director_slug,
        accountrole__journal=journal,
    )
    if len(directors) == 1:
        return directors.first()
    elif len(directors) > 1:
        logger.error(
            f"Journal {journal.code} has {len(directors)} directors!"
            " Using the first one and hoping for the best..."
            " Please enroll only one director (manager -> roles -> director -> view enrolled users)",
        )
        return directors.first()
    else:
        logger.error(
            f"Journal {journal.code} has no directors!"
            " Using the EO system user and hoping for the best..."
            " Please enrol one director (manager -> enrol users)",
        )
        return get_eo_user(obj)


def log_silent_operation(article: Article, message_body: str) -> Message:
    """Create a Message to log a system operation.

    The actor of the message will be wjs-support and recipients will be empty.
    """
    system_user = get_system_user()
    content_type = ContentType.objects.get_for_model(article)
    object_id = article.id
    message = Message.objects.create(
        actor=system_user,
        body=message_body,
        message_type=Message.MessageTypes.SILENT,
        content_type=content_type,
        object_id=object_id,
    )
    return message


def log_operation(
    article: Article,
    message_subject: str,
    message_body="",
    actor=None,
    hijacking_actor=None,
    notify_actor=False,
    recipients=None,
    message_type=Message.MessageTypes.STD,
) -> Message:
    """Create a Message to log something. Send out notifications as needed."""
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
        content_type=content_type,
        object_id=object_id,
        hijacking_actor=hijacking_actor,
    )
    if recipients:
        message.recipients.set(recipients)
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
        log_operation(article, hijack_subject, hijack_body, recipients=[actor])
    return message


def get_hijacker(request: HttpRequest) -> Optional[Account]:
    """Return the hijacker of the given message."""
    try:
        # user.is_hijacked is only set if middlewre is activated. during the tests it might not be
        # and in general it's safer to handle the case where it's not set
        if request.user.is_hijacked:
            hijack_history = request.session["hijack_history"]
            if hijack_history:
                hijacker_id = hijack_history[-1]
                return Account.objects.get(pk=hijacker_id)
    except AttributeError:
        pass


def role_for_article(article: Article, user: Account) -> str:
    """Return a role slug that describes the role of the given user on the article."""
    # TODO: is it possible for a user to have more than one role on one article?
    if user.groups.filter(name=GROUP_EO).exists():
        return "eo"

    if review_models.EditorAssignment.objects.filter(editor=user, article=article).exists():
        return "editor"

    if review_models.ReviewAssignment.objects.filter(reviewer=user, article=article).exists():
        return "reviewer"

    if user == article.correspondence_author:
        return "author"

    if user in article.authors.all():
        return "co-author"

    return ""
