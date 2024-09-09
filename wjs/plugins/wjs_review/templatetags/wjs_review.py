"""Generic tags and filters for the wjs_review plugin.

For tags and filters that relate specifically to Articles, see module wjs_articles.

"""

# custom_tags.py in the templatetags directory
import datetime
import json
from typing import Any, Dict, List, Optional, TypedDict, Union

from django import template
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import QuerySet
from django.utils import timezone
from django_fsm import Transition
from journal.models import ArticleOrdering, Issue, Journal
from plugins.typesetting.models import TypesettingAssignment, TypesettingRound
from review.models import (
    EditorAssignment,
    ReviewAssignment,
    ReviewRound,
    RevisionRequest,
)
from submission.models import Article, Section
from utils import models as janeway_utils_models
from utils.logger import get_logger
from utils.models import LogEntry

from wjs.jcom_profile.models import EditorAssignmentParameters

from .. import communication_utils, permissions, states
from ..communication_utils import MESSAGE_TYPE_ICONS, group_messages_by_version
from ..conditions import needs_extra_article_information
from ..custom_types import BootstrapButtonProps, ReviewAssignmentActionConfiguration
from ..logic import (
    states_when_article_is_considered_in_production,
    states_when_article_is_considered_in_review,
    states_when_article_is_considered_in_review_for_eo_and_director,
)
from ..models import (
    ArticleWorkflow,
    EditorDecision,
    Message,
    MessageThread,
    ProphyAccount,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from ..prophy import Prophy
from ..states import BaseState

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


@register.simple_tag(takes_context=True)
def get_review_assignment_actions(
    context: Dict[str, Any],
    assignment: WorkflowReviewAssignment,
    tag: Union[str, None] = None,
) -> list[ReviewAssignmentActionConfiguration]:
    """Get the available actions on a review assignment."""
    user = context["request"].user
    return assignment.get_actions_for_user(user, tag)


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


@register.filter
def is_production_state(workflow: ArticleWorkflow) -> bool:
    """Return a list of log entries."""
    return workflow.state in states_when_article_is_considered_in_production


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
        disabled_cause = "Another reviewer is being selected"
    elif not reviewer.is_active:
        disabled = True
        disabled_cause = "User is not available to review temporarily or permanently"
    elif reviewer.wjs_is_author:
        disabled = True
        disabled_cause = "User is in the preprint author list"
    elif reviewer.wjs_has_currently_completed_review:
        disabled = True
        disabled_cause = "User has already sent a review for this version"
    elif reviewer.wjs_is_active_reviewer:
        disabled = True
        disabled_cause = "User has already been selected as reviewer for this version"

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


@register.filter()
def get_version_submission_date(article: Article) -> datetime.datetime:
    """
    Return the submission date of the article depending on the current version.

    If current version is 1, return the article submission date, else return the date of the last
    submitted revision request.
    """

    try:
        revision_request = article.completed_revision_requests().latest("date_completed")
        return revision_request.date_completed
    except RevisionRequest.DoesNotExist:
        return article.date_submitted


@register.filter()
def get_status_date(article: Article) -> datetime.datetime:
    """
    Return the relevant date for the current article state.
    """
    workflow = article.articleworkflow
    if workflow.state in (ArticleWorkflow.ReviewStates.TO_BE_REVISED,):
        revision_request = article.active_revision_requests().last()
        if revision_request:
            return revision_request.date_due
    return workflow.modified


@register.filter
def active_revision_request(article: Article) -> Optional[QuerySet[LogEntry]]:
    """Return the active revision request for the given article."""
    return article.active_revision_requests()


@register.filter
def waiting_editor_actions(article: Article) -> bool:
    """Return True if the article is waiting for an editor action."""
    return article.articleworkflow.state_value in (
        ArticleWorkflow.ReviewComputedStates.WAITING_FOR_DECISION,
        ArticleWorkflow.ReviewComputedStates.ASSIGNED_TO_EDITOR,
    )


@register.filter
def waiting_author_actions(article: Article) -> bool:
    """Return True if the article is waiting for an author action."""
    return article.articleworkflow.state_value in (ArticleWorkflow.ReviewStates.TO_BE_REVISED,)


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
def article_messages(article: Article, user: Account) -> QuerySet[Message]:
    """Return all messages related to this article that the user can see."""
    messages = communication_utils.get_messages_related_to_me(user, article)
    return messages


@register.simple_tag()
def timeline_messages(article: Article, user: Account) -> Dict[str, List[Message]]:
    """Return all messages related to this article that the user can see."""
    messages = article_messages(article, user)
    return dict(group_messages_by_version(article, messages))


@register.filter
def article_requires_attention_tt(workflow: ArticleWorkflow, user: Account = None):
    """Inquire with the state-logic class relative to the current workflow state."""
    state_cls = getattr(states, workflow.state)
    return state_cls.article_requires_attention(article=workflow.article, user=user)


@register.filter
def message_type_icon(message: Message) -> str:
    """Return the icon for the message type."""
    return MESSAGE_TYPE_ICONS.get(message.message_type, MESSAGE_TYPE_ICONS[None])


@register.filter
def message_read_by_all(message: Message) -> bool:
    """Return True if the message has been read by all recipients."""
    return message.recipients.filter(messagerecipients__read=False).count() == 0


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
def is_user_article_manager(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is an Editor/Typesetter/Director/EO for the article."""
    return (
        permissions.is_article_editor(article, user)
        or is_user_article_supervisor(article, user)
        or is_user_article_typesetter(article, user)
    )


@register.filter
def is_user_article_editor(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is an Editor for the article."""
    return permissions.is_article_editor(article, user)


@register.filter
def is_user_director(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Director."""
    return permissions.has_director_role_by_article(article, user)


@register.filter
def is_user_article_supervisor(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is the article supervisor Director."""
    return permissions.is_article_supervisor(article, user)


@register.filter
def is_user_article_reviewer(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Reviewer for the article."""
    return permissions.is_article_reviewer(article, user)


@register.filter
def is_user_article_author(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is an Author for the article."""
    return permissions.is_article_author(article, user)


@register.filter
def is_user_one_of_the_authors(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is one of the Article's authors."""
    return permissions.is_one_of_the_authors(article, user)


@register.filter
def is_user_typesetter(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Typesetter for the article."""
    return permissions.has_typesetter_role_by_article(article, user)


@register.filter
def is_user_article_typesetter(article: ArticleWorkflow, user: Account) -> bool:
    """Returns if user is a Typesetter for the article."""
    return permissions.is_article_typesetter(article, user)


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
    if permissions.can_see_other_user_name(
        instance=article.articleworkflow, sender=actor_or_recipient, recipient=user
    ):
        return real_name
    else:
        if permissions.is_article_typesetter(instance=article.articleworkflow, user=actor_or_recipient):
            return "typesetter"
        elif permissions.is_article_editor(instance=article.articleworkflow, user=actor_or_recipient):
            return "editor"
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


@register.simple_tag()
def article_order(issue: Issue, section: Section, article: Article) -> int:
    """Return the order of the article in the issue."""
    try:
        return ArticleOrdering.objects.get(article=article, issue=issue, section=section).order
    except ArticleOrdering.DoesNotExist:
        return 0


@register.simple_tag()
def journal_with_language_content(journal: Journal) -> bool:
    """
    Check if journal requires english content.

    :param journal: The journal to check access to.
    :type journal: Journal
    :return True if the journal has english language, False otherwise.
    :rtype: bool
    """
    return journal.code in settings.WJS_JOURNALS_WITH_ENGLISH_CONTENT


@register.simple_tag()
def article_needs_extra_article_information(article: Article, user: Account) -> bool:
    """
    Check if article needs extra information.

    :param article: The article to check presence of extra information to.
    :type article: Article
    :param user: Unused.
    :type user: Account
    :return True if the article needs extra information, False otherwise.
    :rtype: bool
    """
    return needs_extra_article_information(article.articleworkflow, user)


@register.simple_tag()
def article_has_extra_article_information(article: Article) -> bool:
    """
    Tell if article has all extra information.

    :param article: The article to check presence of extra information to.
    :type article: Article
    :return True if the article has extra information, False otherwise.
    :rtype: bool
    """
    # FIXME: Replace this basic check with condition in https://gitlab.sissamedialab.it/wjs/specs/-/issues/874
    return bool(article.meta_image and article.articleworkflow.social_media_short_description)


@register.simple_tag()
def current_typesetting_assignment(article: Article) -> Optional[TypesettingRound]:
    """Return the current typesetting assignment for the given article."""
    return TypesettingAssignment.objects.filter(round__article=article).order_by("-round__round_number").last()


@register.simple_tag()
def current_editor_assigment(article: Article) -> Optional[TypesettingRound]:
    """Return the current editor assignment for the given article."""
    return WjsEditorAssignment.objects.filter(
        article=article, review_rounds=article.current_review_round_object()
    ).first()


@register.simple_tag()
def special_issue_paper_pending(issue: Issue) -> int:
    """Return the number of articles still in review in the issue."""
    return issue.articles.filter(
        articleworkflow__state__in=states_when_article_is_considered_in_review_for_eo_and_director
    ).count()


@register.simple_tag()
def special_issue_paper_published(issue: Issue) -> int:
    """Return the number of articles already published in the issue."""
    return issue.articles.filter(articleworkflow__state__in=[ArticleWorkflow.ReviewStates.PUBLISHED]).count()


@register.simple_tag()
def special_issue_paper_others(issue: Issue) -> int:
    """Return the number of articles in non-interesting states in the issue."""
    return issue.articles.count() - special_issue_paper_pending(issue) - special_issue_paper_published(issue)
