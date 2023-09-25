import json
from typing import Any, Dict, List, Optional, TypedDict, Union

from django import template
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import QuerySet
from django_fsm import Transition
from review.models import ReviewAssignment
from submission.models import Article
from utils import models as janeway_utils_models
from utils.models import LogEntry

from .. import states
from ..models import ArticleWorkflow
from ..types import BootstrapButtonProps

register = template.Library()

Account = get_user_model()


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
    state_class = getattr(states, workflow.state)
    if state_class is not None and state_class.article_actions is not None:
        return [
            action.as_dict(workflow, user)
            for action in state_class.article_actions
            if action.has_permission(workflow, user)
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
    state_class = getattr(states, workflow.state)
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
    workflow: ArticleWorkflow,
    tag: Union[str, None] = None,
) -> List[AssignmentAndActions]:
    """Get the available actions on a review assignement."""
    user = context["request"].user
    state_class = getattr(states, workflow.state)

    # I want to return a list of objects with
    # - assignment
    # - list of actions (possibly empty)
    results = []
    for assignment in workflow.article.reviewassignment_set.all():
        actions = None
        if state_class is not None and state_class.review_assignment_actions is not None:
            actions = [
                action.as_dict(workflow, user)
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
def reviewer_btn_props(reviewer: Account, selected: str, workflow: ArticleWorkflow) -> BootstrapButtonProps:
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
def article_requires_attention_tt(workflow: ArticleWorkflow):
    """Inquire with the state-logic class relative to the current workflow state."""
    state_cls = getattr(states, workflow.state)
    return state_cls.article_requires_attention(article=workflow.article)


@register.filter
def assignment_requires_attention_tt(assignment: ReviewAssignment):
    """Tell if the assignment requires attention.

    An empty string means there is nothing important to report.
    """
    state_cls = getattr(states, assignment.article.articleworkflow.state)
    return state_cls.assignment_requires_attention(assignment=assignment)
