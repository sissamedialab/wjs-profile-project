"""Tags and filters specific for Articles.

For generic tags and filters, see module wjs_review.

"""
from typing import Optional

from django import template
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from submission.models import Article

from ..models import Message

register = template.Library()

Account = get_user_model()


@register.simple_tag()
def latest_completed_revision(article):
    return article.revisionrequest_set.filter(date_completed__isnull=False).order_by("-date_requested").first()


@register.simple_tag()
def review_assignments_of_current_round(article):
    """Return all review assignments of the current round.

    Useful in the editor (and other) main page.
    """
    current_round = article.current_review_round_object()
    return article.reviewassignment_set.filter(
        review_round=current_round,
        date_declined__isnull=True,
    ).order_by("-date_requested")


@register.simple_tag()
def last_editor_note(article, user):
    """Return the last note that an editor wrote for himself.

    Useful in the editor (and other) main page.
    """
    personal_notes = (
        Message.objects.filter(
            content_type=ContentType.objects.get_for_model(article),
            object_id=article.id,
            actor=user,
            recipients=user.id,  # do not use `__in=[user]`: we want a note written _only_ to the editor
        )
        .exclude(message_type=Message.MessageTypes.SYSTEM)
        .order_by("-created")
    )
    return personal_notes.last() or ""


@register.filter
def user_is_coauthor(article: Article, user: Account) -> Optional[bool]:
    """
    Check if user is coauthor of the article.

    If return value is None, it means that the user is not authenticated.
    """
    if user.is_authenticated:
        return article.user_is_author(user) and article.correspondence_author != user
    return None


@register.filter
def user_is_corresponding_author(article: Article, user: Account) -> Optional[bool]:
    """
    Check if user is corresponding author of the article.

    If return value is None, it means that the user is not authenticated.
    """
    if user.is_authenticated:
        return article.user_is_author(user) and article.correspondence_author == user
    return None
