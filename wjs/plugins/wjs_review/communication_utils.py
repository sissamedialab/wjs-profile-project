"""Utility functions related to the communication system.

Keeping here also anything that we might want to test easily 🙂.
"""

from typing import Union

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, QuerySet
from journal.models import Journal
from review import models as review_models
from submission.models import Article
from utils.logger import get_logger

from wjs.jcom_profile.apps import GROUP_EO

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

    messages = (
        Message.objects.filter(
            # Get messages for this article...
            Q(Q(content_type=content_type) & Q(object_id=object_id))
            # ...but only...
            & Q(
                # ...if they have some relation with me
                Q(Q(recipients__in=[user]) | Q(actor=user))
                # ...or if they are "generic" messages
                | Q(recipients__isnull=True),
            ),
        )
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


def get_eo_user(obj: Union[Article, Journal]):
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
    recipients=None,
    message_type=Message.MessageTypes.STD,
) -> Message:
    """Create a Message to log something. Send out notifications as needed."""
    if not actor:
        actor = get_system_user()

    content_type = ContentType.objects.get_for_model(article)
    object_id = article.id
    message = Message.objects.create(
        actor=actor,
        subject=message_subject,
        body=message_body,
        message_type=message_type,
        content_type=content_type,
        object_id=object_id,
    )
    if recipients:
        message.recipients.set(recipients)
    message.emit_notification()
    return message


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
