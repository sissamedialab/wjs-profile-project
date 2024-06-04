"""Generic tags and filters for the wjs_review plugin.

For tags and filters that relate specifically to Articles, see module wjs_articles.

"""

# custom_tags.py in the templatetags directory
import datetime
import json
from typing import Any, Dict, List, Optional, TypedDict, Union

from django import template
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import QuerySet
from django.utils import timezone
from django_fsm import Transition
from journal.models import Journal
from plugins.typesetting.models import TypesettingRound
from plugins.wjs_review.states import BaseState
from review.models import EditorAssignment, ReviewAssignment, ReviewRound
from submission.models import Article
from utils import models as janeway_utils_models
from utils.logger import get_logger
from utils.models import LogEntry

from wjs.jcom_profile.models import EditorAssignmentParameters

from .. import communication_utils, states
from ..custom_types import BootstrapButtonProps
from ..logic import states_when_article_is_considered_in_review
from ..models import ArticleWorkflow, EditorDecision, MessageThread, ProphyAccount
from ..permissions import (
    has_director_role_by_article,
    has_typesetter_role_by_article,
    is_article_author,
    is_article_editor,
    is_article_reviewer,
    is_article_typesetter,
    is_one_of_the_authors,
)
from ..prophy import Prophy

register = template.Library()

Account = get_user_model()

logger = get_logger(__name__)


class AssignmentAndActions(TypedDict):
    """An assignment with its actions."""

    assignment: ReviewAssignment
    actions: List[Any]


@register.simple_tag(takes_context=True)
def get_available_transitions(context: Dict[str, Any], workflow: ArticleWorkflow) -> List[Transition]:
    """Get the available transitions for the given workflow."""
    user = context["request"].user
    return list(workflow.get_available_user_state_transitions(user=user))


@register.simple_tag(takes_context=True)
def get_article_actions(context: Dict[str, Any], workflow: ArticleWorkflow, tag: Union[str, None] = None) -> List[str]:
    """Get the available actions on an article in the given state."""
    user = context["request"].user
    state_class = BaseState.get_state_class(workflow)
    if state_class is not None and state_class.article_actions is not None:
        return [
            action.as_dict(workflow, user)
            for action in state_class.article_actions
            if action.is_available(workflow, user)
            # if action.has_permission(workflow, user) and action.tag == tag
        ]
    else:
        return None


# TODO: this templatetag is not currently used and could be dropped
# It is superseded by get_review_assignments which returns all assignments and their actions.
# Keeping anyway because it might be useful for a reviewer's view of his own assignement.
@register.simple_tag(takes_context=True)
def get_review_assignment_actions(
    context: Dict[str, Any],
    assignment: ReviewAssignment,
    tag: Union[str, None] = None,
) -> List[str]:
    """Get the available actions on a review assignement."""
    article = assignment.article
    workflow = article.articleworkflow
    user = context["request"].user
    state_class = BaseState.get_state_class(workflow)
    if state_class is not None and state_class.review_assignment_actions is not None:
        return [
            action.as_dict(workflow, user)
            for action in state_class.review_assignment_actions
            if action.condition_is_met(assignment, user)
            # if action.has_permission(workflow, user) and action.tag == tag
        ]
    else:
        return None


@register.simple_tag(takes_context=True)
def get_review_assignments(
    context: Dict[str, Any],
    review_round: ReviewRound,
    tag: Union[str, None] = None,
) -> List[AssignmentAndActions]:
    """Get the available actions on a review assignement."""
    user = context["request"].user
    state_class = getattr(states, review_round.article.articleworkflow.state)

    # I want to return a list of objects with
    # - assignment
    # - list of actions (possibly empty)
    results = []
    for assignment in review_round.reviewassignment_set.all().order_by("-review_round__round_number"):
        actions = None
        if state_class is not None and state_class.review_assignment_actions is not None:
            actions = [
                action.as_dict(assignment, user)
                for action in state_class.review_assignment_actions
                if action.condition_is_met(assignment, user)
                # TODO: might need: if action.has_permission(workflow, user) and action.tag == tag
            ]
        results.append(AssignmentAndActions(assignment=assignment, actions=actions))

    return results


@register.filter
def get_article_log_entries(article: Article) -> Optional[QuerySet[LogEntry]]:
    """Return a list of log entries."""
    # Taken from journal.views.manage_article_log
    if not article:
        return None
    content_type = ContentType.objects.get_for_model(article)
    log_entries = janeway_utils_models.LogEntry.objects.filter(content_type=content_type, object_id=article.pk)
    return log_entries


@register.simple_tag()
def reviewer_btn_props(
    reviewer: Union[Account, ProphyAccount],
    selected: str,
    workflow: ArticleWorkflow,
) -> BootstrapButtonProps:
    """
    Return the properties for the select reviewer button.

    - value: the reviewer pk if no reviewer is selected or the reviewer is not the selected one
    - css_class: btn-success if the reviewer is the selected one, btn-primary otherwise
    - disabled: True if the reviewer cannot be selecte for some reason
    - disabled_cause: The reason why the button has been disabled
    """
    try:
        selected = int(selected)
    except ValueError:
        selected = None
    current_reviewer = bool(selected and reviewer.pk == selected)
    other_reviewer = bool(selected and reviewer.pk != selected)
    no_reviewer_or_not_matching = not selected or reviewer.pk != selected

    disabled = False
    disabled_cause = ""
    if other_reviewer:
        disabled = True
        disabled_cause = "Another reviewer is beeing selected"
    elif not reviewer.is_active:
        disabled = True
        disabled_cause = "This person is not active in the system"
    elif reviewer.wjs_is_author:
        disabled = True
        disabled_cause = "This person is one of the authors"
    elif reviewer.wjs_is_active_reviewer:
        disabled = True
        disabled_cause = "This person is already a reviewer of this paper"

    data: BootstrapButtonProps = {
        "value": json.dumps({"reviewer": reviewer.pk}) if no_reviewer_or_not_matching else "",
        "css_class": "btn-success" if current_reviewer else "btn-primary",
        "disabled": disabled,
        "disabled_cause": disabled_cause,
    }
    return data


@register.simple_tag()
def get_requested_date(user, article):
    review_round = article.current_review_round_object()
    try:
        return ReviewAssignment.objects.get(reviewer=user, review_round=review_round).date_requested
    except ReviewAssignment.DoesNotExist:
        logger.warning(f"Date requested not found for article {article}, round {review_round}")
        return ""


@register.filter
def active_revision_request(article: Article) -> Optional[QuerySet[LogEntry]]:
    """Return the active revision request for the given article."""
    return article.active_revision_requests()


@register.filter
def review_assignment_request_message(assignment: ReviewAssignment):
    """Return the "invitation" message sent by an editor to a reviewer to ask for a review."""
    # These email messages are stored as LogEntries.
    article = assignment.article
    content_type = ContentType.objects.get_for_model(article)
    log_entry = LogEntry.objects.filter(
        content_type=content_type,
        object_id=article.pk,
        actor=assignment.editor,
        types="Review Request",
        is_email=True,
        toaddress__email__in=(assignment.reviewer.email,),
    ).order_by("date")
    return [f"<div>{le} | {le.message_id} | {le.description}</div>" for le in log_entry]


@register.filter
def article_messages(article: Article, user: Account):
    """Return all messages related to this article that the user can see."""
    messages = communication_utils.get_messages_related_to_me(user, article)
    return messages


@register.filter
def article_requires_attention_tt(workflow: ArticleWorkflow, user: Account = None):
    """Inquire with the state-logic class relative to the current workflow state."""
    state_cls = getattr(states, workflow.state)
    return state_cls.article_requires_attention(article=workflow.article, user=user)


@register.filter
def assignment_requires_attention_tt(assignment: ReviewAssignment, user: Account = None):
    """Tell if the assignment requires attention.

    An empty string means there is nothing important to report.
    """
    state_cls = getattr(states, assignment.article.articleworkflow.state)
    return state_cls.assignment_requires_attention(assignment=assignment, user=user)


@register.filter
def role_for_article_tt(article: Article, user: Account) -> str:
    """Return a role slug that describes the role of the given user on the article."""
    return communication_utils.role_for_article(article, user)


@register.filter
def is_user_article_editor(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is an Editor for the article."""
    return is_article_editor(article, user)


@register.filter
def is_user_director(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Director."""
    return has_director_role_by_article(article, user)


@register.filter
def is_user_article_reviewer(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Reviewer for the article."""
    return is_article_reviewer(article, user)


@register.filter
def is_user_article_author(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is an Author for the article."""
    return is_article_author(article, user)


@register.filter
def is_user_one_of_the_authors(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is one of the Article's authors."""
    return is_one_of_the_authors(article, user)


@register.filter
def is_user_typesetter(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Typesetter for the article."""
    return has_typesetter_role_by_article(article, user)


@register.filter
def is_user_article_typesetter(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Typesetter for the article."""
    return is_article_typesetter(article, user)


@register.filter
def reviewer_review_assignments(article: ArticleWorkflow, user: Account):
    """
    Returns the list of Review Assignments assigned to the current user.
    """
    return ReviewAssignment.objects.filter(reviewer=user, article=article)


@register.filter
def jwt_token_url(article: Article, user: Account) -> str:
    """return generated jwt_token for article"""
    p = Prophy(article)
    return p.jwt_token_url(user)


@register.filter
def has_prophy_candidates(article: Article) -> bool:
    """True if article has prophy candidates"""
    p = Prophy(article)
    return p.article_has_prophycandidates()


@register.filter
def days_since(date: Union[datetime.datetime, datetime.date]) -> int:
    """Return the number of days elapsed since the given date."""
    return (timezone.now() - date).days


@register.filter
def typesetting_rounds(article: Article, user: Account = None):
    """
    Returns the list of Typesetting Rounds linked to Typesetting Assignments assigned to the current user, if specified
    If no user is specified, returns all Typesetting Rounds for the given article.
    """
    query = TypesettingRound.objects.filter(article=article)
    if user is not None:
        query = query.filter(typesettingassignment__typesetter=user)
    return query


@register.simple_tag
def last_major_revision(article: ArticleWorkflow):
    """Returns Article's last major revision"""
    return EditorDecision.objects.filter(workflow=article, decision=ArticleWorkflow.Decisions.MINOR_REVISION).last()


@register.simple_tag(takes_context=True)
def hide_real_name(context, actor_or_recipient: Account, to: Account, on: Article) -> str:
    """Hide/show a message recipient/actor's name."""
    # The arguments names "to" and "on" allow for a readable template tag, e.g.:
    # {% display_recipient recipient to=user on=article %}
    # but here we are aliasing "to" and "on" onto something easier to understand in the code
    user = to
    article = on

    real_name = str(actor_or_recipient)
    if is_article_author(instance=article.articleworkflow, user=user):
        if is_article_typesetter(instance=article.articleworkflow, user=actor_or_recipient):
            return "typesetter"
        elif is_article_editor(instance=article.articleworkflow, user=actor_or_recipient):
            return "editor"
        else:
            return real_name
    else:
        return real_name


@register.filter
def should_message_be_forwarded(message):
    """Return True if a message should be forwarded."""
    return (
        message.to_be_forwarded_to
        and not message.children.filter(relation_type=MessageThread.MessageRelation.FORWARD).exists()
    )


@register.simple_tag()
def get_max_workload(editor: Account, journal: Journal) -> int:
    """Get the maximum workload for the given editor and journal."""
    # We don't expect a DoesNotExist, and even less MultipleObjectsReturned, but just in case...
    try:
        eap = EditorAssignmentParameters.objects.get(editor=editor, journal=journal)
    except EditorAssignmentParameters.DoesNotExist:
        logger.error(f"Editor {editor} is not correctly setup on {journal.code}")
        return 0
    except EditorAssignmentParameters.MultipleObjectsReturned:
        logger.error(f"Editor {editor} has multiple configurations on {journal.code}. Using first. Please check.")
        eap = EditorAssignmentParameters.objects.filter(editor=editor, journal=journal).first()
    return eap.workload


@register.simple_tag()
def get_current_workload(editor: Account, journal: Journal) -> int:
    """Get the current workload (the number of pending editor assignments) for the given editor and journal."""
    editor_assignments = EditorAssignment.objects.filter(
        editor=editor,
        article__journal=journal,
        # To filter pending EditorAssignment objects, filter the ones with the Articleworkflow in these states
        article__articleworkflow__state__in=states_when_article_is_considered_in_review,
    )
    return editor_assignments.count()


@register.simple_tag()
def get_editor_keywords(editor: Account, journal: Journal) -> List[str]:
    """Get the keywords for the given editor and journal."""
    # We don't expect a DoesNotExist, and even less MultipleObjectsReturned, but just in case...
    try:
        eap = EditorAssignmentParameters.objects.get(editor=editor, journal=journal)
    except EditorAssignmentParameters.DoesNotExist:
        logger.error(f"Editor {editor} is not correctly setup on {journal.code}")
        return []
    except EditorAssignmentParameters.MultipleObjectsReturned:
        logger.error(f"Editor {editor} has multiple configurations on {journal.code}. Using first. Please check.")
        eap = EditorAssignmentParameters.objects.filter(editor=editor, journal=journal).first()
    return [k.word for k in eap.keywords.all()]
