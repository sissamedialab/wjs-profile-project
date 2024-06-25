"""Tags and filters specific for Articles.

For generic tags and filters, see module wjs_review.

"""

from datetime import datetime
from typing import Optional

from django import template
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.utils.text import slugify
from plugins.typesetting.models import (
    GalleyProofing,
    TypesettingAssignment,
    TypesettingRound,
)
from submission.models import Article

from wjs.jcom_profile.constants import EO_GROUP

from ..logic import states_when_article_is_considered_in_production
from ..models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

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


@register.simple_tag(takes_context=True)
def last_user_note(context, article, user=None):
    """Return the last note that a user wrote for himself.

    Useful in the pending eo listing main page.
    """
    if not user:
        user = context["request"].user

    personal_notes = (
        Message.objects.filter(
            content_type=ContentType.objects.get_for_model(article),
            object_id=article.pk,
            actor=user,
            recipients=user,  # do not use `__in=[user]`: we want a note written _only_ to the user themselves
        )
        .exclude(message_type=Message.MessageTypes.SYSTEM)
        .order_by("-created")
    )
    return personal_notes.last() or ""


@register.simple_tag()
def last_eo_note(article):
    """Return the last note that any EO wrote on a paper.

    Useful in the EO main page.
    """
    eo_notes = (
        Message.objects.filter(
            content_type=ContentType.objects.get_for_model(article),
            object_id=article.id,
            actor__groups__name=EO_GROUP,
        )
        .exclude(message_type=Message.MessageTypes.SYSTEM)
        .order_by("-created")
    )
    return eo_notes.last() or ""


@register.simple_tag()
def article_state_details(article):
    if article.articleworkflow.state in states_when_article_is_considered_in_production:
        return article.articleworkflow.get_state_display()

    waiting_for_revision = article.active_revision_requests().filter(
        editorrevisionrequest__review_round=article.current_review_round_object(),
    )

    if waiting_for_revision.exists():
        return waiting_for_revision.first().get_type_display()

    elif article.active_reviews.exists():
        return "Assigned to reviewers"

    elif article.completed_reviews.exclude(decision="withdrawn").exists():
        return "Waiting for decision"

    else:
        return article.articleworkflow.get_state_display()


@register.filter
def article_current_editor(article):
    """Return the current editor."""
    # TODO: registering as a `filter` because I don't know how to use it with with otherwise
    # e.g.: {% with editor_assignment_data=article|article_current_editor %}

    try:
        editor_assignment = WjsEditorAssignment.objects.get_current(article)
        return {
            "editor": editor_assignment.editor,
            "days_elapsed": (timezone.now() - editor_assignment.assigned).days,
        }
    except WjsEditorAssignment.DoesNotExist:
        return {
            "editor": "Not assigned",
            # NB: this might not be accurate if there was a previous assignment that has been rejected, but the
            # importance of the delay can be comparable with more common situations (i.e. older papers are _usually_
            # more urgent).
            "days_elapsed": (timezone.now() - article.date_submitted).days,
        }


@register.filter
def article_current_typesetter(article: Article, unfiltered: bool = False) -> TypesettingAssignment:
    """
    Return the current typesetter.

    Call as `{% with typesetter=article|article_current_typesetter %}` to get the current typesetter.

    Use `{% with typesetter=article|article_current_typesetter:True %}` to filter across all typesetting rounds.

    :param article: The article to get the typesetter for.
    :type article: Article
    :param unfiltered: If True, get the latest assignment irrespective of its stage (this is mostly useful for
        published rs where there is not active typesetting round).
    :type unfiltered: bool
    :return: The typesetter of the latest typesetting assignment.
    :rtype: TypesettingAssignment
    """
    try:
        if unfiltered:
            return TypesettingAssignment.objects.filter(round__article=article).latest("assigned").typesetter
        else:
            return TypesettingAssignment.active_objects.filter(round__article=article).latest("assigned").typesetter
    except TypesettingAssignment.DoesNotExist:
        return None


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


@register.simple_tag()
def get_article_classes(workflow: ArticleWorkflow) -> dict[str, str]:
    """Return a string of classes for an article div."""
    state = f"color-state-{slugify(workflow.state)}"
    section = f"color-section-{workflow.article.section.pk}"
    publishable = "bg-success" if workflow.production_flag_no_checks_needed else "bg-danger"
    return {
        "state": state,
        "section": section,
        "publishable": publishable,
    }


@register.filter
def versioned_number(article: Article) -> str:
    """Return the versioned number of the article."""
    if typesetting_round := TypesettingRound.objects.filter(article=article).last():
        return f"{article.pk}/v{typesetting_round.round_number}"
    if article.current_review_round():
        return f"{article.pk}/v{article.current_review_round()}"
    return article.pk


@register.filter
def upcoming_deadline(article: Article) -> Optional[datetime]:
    """Return the upcoming author deadline for an article."""
    if typesetting_round := TypesettingRound.objects.filter(article=article).last():
        try:
            return (
                GalleyProofing.active_objects.filter(
                    proofreader=article.correspondence_author, round=typesetting_round
                )
                .latest("due")
                .due
            )
        except GalleyProofing.DoesNotExist:
            pass
    if review_round := article.current_review_round_object():
        try:
            return (
                EditorRevisionRequest.objects.filter(review_round=review_round, article=article)
                .latest("date_due")
                .date_due
            )
        except EditorRevisionRequest.DoesNotExist:
            pass


@register.filter
def article_completed_review_by_user(article: Article, user: Account) -> Optional[WorkflowReviewAssignment]:
    """Get completed review assignment if user is reviewer of the last review round of the article."""
    try:
        return (
            WorkflowReviewAssignment.objects.filter(
                article=article, reviewer=user, date_complete__isnull=False, date_declined__isnull=True
            )
            .exclude(decision="withdraw")
            .latest("date_complete")
        )
    except WorkflowReviewAssignment.DoesNotExist:
        pass


@register.filter
def article_pending_review_by_user(article: Article, user: Account) -> Optional[WorkflowReviewAssignment]:
    """Get pending review assignment if user is reviewer of the last review round of the article."""
    try:
        return WorkflowReviewAssignment.objects.filter(article=article, reviewer=user, date_complete__isnull=True)
    except WorkflowReviewAssignment.DoesNotExist:
        pass
