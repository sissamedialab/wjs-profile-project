"""Utility functions related to the communication system.

Keeping here also anything that we might want to test easily 🙂.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, QuerySet
from submission.models import Article

from .models import Message

Account = get_user_model()


def get_messages_related_to_me(user: Account, article: Article) -> QuerySet[Message]:
    """Return a queryset of messages that can be of interest to the given user."""
    content_type = ContentType.objects.get_for_model(article)
    object_id = article.id

    messages = Message.objects.filter(
        # Get messages for this article...
        Q(Q(content_type=content_type) & Q(object_id=object_id))
        # ...but only...
        & Q(
            # ...if they have some relation with me
            Q(Q(recipients__in=[user]) | Q(actor=user))
            # ...or if they are "generic" messages
            | Q(recipients__isnull=True),
        ),
    ).order_by("created")
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
    message_body: str,
    actor=None,
    recipients=None,
    message_type=Message.MessageTypes.STD,
    message_subject="",
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
