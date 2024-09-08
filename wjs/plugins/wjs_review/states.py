"""Workflow states for the review process and their actions."""

# TODO: verify if these state classes can be used as choices for django-fsm workflow

import dataclasses
import urllib
from typing import Callable, Optional, Type

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.translation import gettext as _
from faker.utils.text import slugify
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from review.models import ReviewAssignment
from submission.models import Article
from utils.logger import get_logger

from wjs.jcom_profile import permissions as base_permissions

from . import communication_utils, conditions, permissions
from .models import ArticleWorkflow, can_be_set_rfp_wrapper

logger = get_logger(__name__)

Account = get_user_model()


def get_url_with_last_editor_revision_request_pk(
    action: "ArticleAction",
    workflow: "ArticleWorkflow",
    user: Account,
) -> str:
    """
    Given the ArticleAction and ArticleWorkflow, reverse the url of the ArticleAction.view_name but
    using the EditorRevisionRequest's pk.
    """

    latest_revision_request = (
        workflow.article.revisionrequest_set.filter(
            article=workflow.article,
        )
        .order_by(
            "date_requested",
        )
        .last()
    )
    if not latest_revision_request:
        return "#"
    latest_editor_revision_request = latest_revision_request.editorrevisionrequest
    url = reverse(action.view_name, kwargs={"pk": latest_editor_revision_request.id})
    if action.querystring_params is not None:
        url += "?"
        url += urllib.parse.urlencode(action.querystring_params)
    return url


def get_url_with_typesetting_assignment_pk(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account) -> str:
    """From ArticleAction and ArticleWorkflow retrieve the action url with typesetting assignment pk."""
    # Ordering is misleading but from the models' class Meta we have: "ordering = ('-round_number', 'date_created')"
    typesetting_assignment = (
        TypesettingAssignment.objects.filter(
            round__article=workflow.article,
        )
        .order_by("round__round_number")
        .last()
    )
    url = reverse(action.view_name, kwargs={"pk": typesetting_assignment.pk})
    if action.querystring_params is not None:
        url += "?"
        url = f"{url}?{urllib.parse.urlencode(action.querystring_params)}"
    return url


def get_url_with_galleyproofing_pk(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account) -> str:
    """From ArticleAction and ArticleWorkflow retrieve the action url with galleyproofing pk."""
    galleyproofing = (
        GalleyProofing.objects.filter(
            round__article=workflow.article,
        )
        .order_by("round__round_number")
        .last()
    )
    url = reverse(action.view_name, kwargs={"pk": galleyproofing.pk})
    if action.querystring_params is not None:
        url += "?"
        url = f"{url}?{urllib.parse.urlencode(action.querystring_params)}"
    return url


def get_edit_permissions_url_review_assignment(
    action: "ReviewAssignmentAction",
    assignment: ReviewAssignment,
    user: Account,
) -> str:
    """Return the URL of the view to customize the permissions of the review assignment reviewer."""
    url = reverse(
        action.view_name,
        kwargs={
            "user_id": user.pk,
            "pk": assignment.article.articleworkflow.pk,
        },
    )
    if action.querystring_params is not None:
        url += "?"
        url += urllib.parse.urlencode(action.querystring_params)
    return url


def get_review_url(action: "ReviewAssignmentAction", assignment: ReviewAssignment, user: Account) -> str:
    """
    Return the URL of the view that shows the review assignment details.

    URL is selected according to the review assignment state.
    """

    if assignment.is_complete and assignment.decision == "withdrawn":
        return reverse("wjs_review_end", kwargs={"assignment_id": assignment.pk})
    elif assignment.date_accepted and assignment.is_complete:
        return reverse("wjs_review_end", kwargs={"assignment_id": assignment.pk})
    elif assignment.date_accepted:
        return reverse("wjs_review_review", kwargs={"assignment_id": assignment.pk})
    elif assignment.date_declined:
        return reverse("wjs_declined_review", kwargs={"assignment_id": assignment.pk})
    else:
        return reverse("wjs_evaluate_review", kwargs={"assignment_id": assignment.pk})


def get_review_url_with_code(action: "ReviewAssignmentAction", assignment: ReviewAssignment, user: Account) -> str:
    """Return the URL of the view that shows the review assignment details."""
    url = get_review_url(action, assignment, user)
    return f"{url}?code={assignment.access_code}"


def get_do_revision_url(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account) -> str:
    revision_request = conditions.pending_revision_request(workflow, user)
    if revision_request:
        return reverse(
            "do_revisions",
            kwargs={"article_id": workflow.article_id, "revision_id": revision_request.pk},
        )


def get_edit_metadata_revision_url(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account) -> str:
    revision_request = conditions.pending_edit_metadata_request(workflow, user)
    if revision_request:
        return reverse(
            "do_revisions",
            kwargs={"article_id": workflow.article_id, "revision_id": revision_request.pk},
        )


def get_unpulishable_css_class(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account):
    """Return the css class for a button that would change the flag.

    The class is related to the action that would be done, not the state of the article.
    """
    if not workflow.production_flag_no_checks_needed:
        return "btn-success"
    else:
        return "btn-danger"


def get_publishable_label(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account):
    """Return the label for a button that would change the flag.

    The label describes the action that would be done, not the state of the article.
    """
    if workflow.production_flag_no_checks_needed:
        return "Mark as Unpublishable"
    return "Mark as publishable"


def cannot_be_set_rfp_or_galleys_not_present(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account):
    """Return true if the article can be set ready for publication."""
    return not can_be_set_rfp_wrapper(workflow) or galleys_cannot_be_tested(action, workflow, user)


def galleys_cannot_be_tested(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account):
    """Return true if the files needed to generate the latest galleys are missing."""
    typesetting_assignment = (
        TypesettingAssignment.objects.filter(
            round__article=workflow.article,
        )
        .order_by("round__round_number")
        .last()
    )
    return not typesetting_assignment.files_to_typeset.exists()


@dataclasses.dataclass
class ArticleAction:
    """An action that can be done on an Article."""

    # see templates/wjs_review/details/elements/actions_button.html to see what the attributes are for 🙂
    name: str
    label: str
    view_name: str
    permission: Optional[Callable] = None
    tag: str = None
    is_htmx: bool = False
    is_modal: bool = False
    is_post: bool = False
    order: int = 0
    tooltip: str = None
    querystring_params: dict = None
    disabled: Optional[Callable] = None
    custom_get_url: Optional[Callable] = None
    custom_get_css_class: Optional[Callable] = None
    custom_get_label: Optional[Callable] = None
    condition: Optional[Callable] = None
    confirm: Optional[str] = ""

    # TODO: refactor in ArticleAction(BaseAction) ReviewAssignmentAction(BaseAction)?
    # TODO: do we still need tag? let's keep it...

    def __post_init__(self):
        if self.is_modal:
            self.is_htmx = True

    def as_dict(self, workflow: "ArticleWorkflow", user: Account):
        """Return parameters needed to build the action button."""
        return {
            "name": self.name,
            "slug": slugify(self.name),
            "label": self.custom_get_label(self, workflow, user) if self.custom_get_label else self.label,
            "tooltip": self.tooltip,
            "url": self.custom_get_url(self, workflow, user) if self.custom_get_url else self.get_url(workflow, user),
            "tag": self.tag,
            "css_class": self.custom_get_css_class(self, workflow, user) if self.custom_get_css_class else None,
            "is_htmx": self.is_htmx,
            "is_modal": self.is_modal,
            "is_post": self.is_post,
            "confirm": self.confirm,
            "disabled": self.disabled(self, workflow, user) if self.disabled else None,
            "id": id(self),
        }

    def get_url(self, workflow: "ArticleWorkflow", user: Account) -> str:
        """Return the URL of the view that is the entry point to manage the action."""
        if self.view_name == "WRITEME!":
            return "#"
        url = reverse(self.view_name, kwargs={"pk": workflow.id})
        if self.querystring_params is not None:
            url += "?"
            url += urllib.parse.urlencode(self.querystring_params)
        return url

    def is_available(self, workflow: "ArticleWorkflow", user: Account) -> bool:
        """Return true if permission and condition are both met."""
        return self._has_permission(workflow, user) and self._condition_is_met(workflow, user)

    def _condition_is_met(self, workflow: "ArticleWorkflow", user: Account) -> bool:
        """Return true if the action has condition and it evauates to true.

        If there is no condition, the action is considered available.
        """
        if self.condition is None:
            return True
        return self.condition(workflow=workflow, user=user)

    def _has_permission(self, workflow: "ArticleWorkflow", user: Account) -> bool:
        """Return true if the user has permission to run this action, given the current status of the article."""
        return self.permission(workflow, user)


@dataclasses.dataclass
class ReviewAssignmentAction:
    """An action that can be done on an ReviewAssignment."""

    name: str
    label: str
    view_name: str
    condition: Optional[Callable] = None
    tag: str = None
    order: int = 0
    tooltip: str = None
    is_htmx: bool = False
    is_modal: bool = False
    is_post: bool = False
    querystring_params: dict = None
    custom_get_url: Optional[Callable] = None
    permission: Optional[Callable] = None
    confirm: Optional[str] = ""

    def __post_init__(self):
        if self.is_modal:
            self.is_htmx = True

    def as_dict(self, assignment: "ReviewAssignment", user: Account):
        """Return parameters needed to build the action button."""
        if self.custom_get_url:
            url = self.custom_get_url(self, assignment, user)
        else:
            url = self.get_url(assignment, user)
        return {
            "assignment": assignment,
            "slug": slugify(self.name),
            "name": self.name,
            "label": self.label,
            "tooltip": self.tooltip,
            "url": url,
            "is_htmx": self.is_htmx,
            "is_modal": self.is_modal,
            "is_post": self.is_post,
            "confirm": self.confirm,
            "id": id(self),
        }

    def get_url(self, assignment: "ReviewAssignment", user: Account) -> str:
        """Return the URL of the view that is the entry point to manage the action."""
        if self.view_name == "WRITEME!":
            return "#"
        url = reverse(self.view_name, kwargs={"pk": assignment.id})
        if self.querystring_params is not None:
            url += "?"
            url += urllib.parse.urlencode(self.querystring_params)
        return url

    def is_available(self, assignment: "ReviewAssignment", user: Account, tag: str) -> bool:
        """
        Check if the action is available for the user on the given Review assignment.

        :param assignment: the review assignment to check
        :type assignment: ReviewAssignment
        :param user: the current user to check for availability
        :type user: Account
        :param tag: tag (currently unused because not required by the designs) to filter actions in different contexts
        :type tag: str
        :return: True if the action is available
        :rtype: bool
        """
        return self._has_permission(assignment, user) and self._condition_is_met(assignment, user)

    def _condition_is_met(self, assignment: "ReviewAssignment", user: Account) -> bool:
        """
        Check if the review assignment meets the condition checked by :py:attr:`condition` callable.

        It's not meant to check user permissions (see :py:meth:`_has_permission`).

        If :py:attr:`condition` is not set, it defaults to True.

        :param assignment: the review assignment to check
        :type assignment: ReviewAssignment
        :param user: the current user used to match the conditions critera
        :type user: Account
        :return: True if the condition is met
        :rtype: bool
        """
        if self.condition is None:
            return True
        return self.condition(assignment, user)

    def _has_permission(self, assignment: "ReviewAssignment", user: Account) -> bool:
        """
        Check if the user has permission to run this action according to the current status of the article.

        If :py:attr:`permission` is not set, it defaults to True.

        :param assignment: the review assignment to check
        :type assignment: ReviewAssignment
        :param user: the current user to check for permissions
        :type user: Account
        :return: True if the user has permission to run this action
        :rtype: bool
        """
        if self.permission is None:
            return True
        return self.permission(assignment.article.articleworkflow, user)


# Actions organized by states
class BaseState:
    article_actions = (
        ArticleAction(
            permission=permissions.has_eo_role_by_article,
            name="assign eo",
            label="Assign / Reassign EO in charge",
            view_name="wjs_assign_eo",
            is_modal=True,
        ),
        ArticleAction(
            permission=permissions.is_article_author,
            name="withdraw preprint",
            label="Withdraw",
            view_name="wjs_author_withdraw_preprint",
            condition=conditions.can_withdraw_preprint,
        ),
    )
    review_assignment_actions = ()

    @classmethod
    def article_requires_attention(cls, article: Article, user: Account) -> str:
        """Dispatch the request to per-user functions.

        An article, in a certain state, requires attention from a certain role in certain conditions. For instance: an
        article assigned to an editor, but without andy reviewers requires immediate attention by the editor, but
        requires attention by EO/director only when all automatic reminders to the editor have been sent.

        """
        role = communication_utils.role_for_article(article, user)
        # Since this method will be called by a "child" class, here `cls` will refer to that class (the real state)
        if func := getattr(cls, f"article_requires_{role}_attention", None):
            return func(article=article, user=user)
        else:
            logger.debug(
                f"In {cls}. Article {article.id} does not require attention by {role} {user.full_name()}",
            )
            return ""

    @classmethod
    def assignment_requires_attention(cls, assignment: ReviewAssignment, user: Account) -> str:
        """Dispatch the request to per-user functions."""
        article = assignment.article
        role = communication_utils.role_for_article(article, user)
        if func := getattr(cls, f"assignment_requires_{role}_attention", None):
            return func(assignment=assignment, user=user)
        else:
            logger.debug(
                f"In {cls}. Assignment {assignment.id} does not require attention by {role} {user.full_name()}",
            )
            return ""

    @classmethod
    def get_state_class(cls, workflow: ArticleWorkflow) -> Type["BaseState"]:
        return globals()[workflow.state]

    @classmethod
    def get_action_by_name(cls, name: str) -> Optional[ArticleAction]:
        for action in cls.article_actions:
            if action.name == name:
                return action
        return None


class EditorToBeSelected(BaseState):
    """Editor to be selected."""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.is_article_supervisor,
            name="assign editor",
            label="Assign Editor",
            view_name="wjs_assign_editor",
        ),
    )

    @classmethod
    def article_requires_attention(cls, article: Article, **kwargs) -> str:
        """Articles in this state always require attention (from EO or director)."""
        return conditions.always(article)


class EditorSelected(BaseState):
    """Editor selected"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.is_article_editor,
            name="declines assignment",
            label="Decline Assignment",
            view_name="wjs_unassign_assignment",
            is_modal=True,
        ),
        ArticleAction(
            permission=permissions.is_article_supervisor,
            name="assign different editor",
            label="Assign different Editor",
            view_name="wjs_assign_editor",
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="accepts",
            label="Accept",
            view_name="wjs_article_decision",
            querystring_params={"decision": "accept"},
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="rejects",
            label="Reject",
            view_name="wjs_article_decision",
            querystring_params={"decision": "reject"},
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="deems not suitable",
            label="Not suitable",
            view_name="wjs_article_decision",
            querystring_params={"decision": "not_suitable"},
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="make decision",
            label="Make decision",
            view_name="wjs_article_decision",
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="request technical revision",
            label="Request Technical revision",
            view_name="wjs_article_decision",
            querystring_params={"decision": ArticleWorkflow.Decisions.TECHNICAL_REVISION},
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="request minor revision",
            label="Request Minor revision",
            view_name="wjs_article_decision",
            querystring_params={"decision": ArticleWorkflow.Decisions.MINOR_REVISION},
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="request major revision",
            label="Request Major revision",
            view_name="wjs_article_decision",
            querystring_params={"decision": ArticleWorkflow.Decisions.MAJOR_REVISION},
        ),
        ArticleAction(
            condition=conditions.user_can_be_assigned_as_reviewer,
            permission=permissions.is_article_editor,
            name="assigns self as reviewer",
            label="I will review",
            tooltip="Assign myself as reviewer",
            view_name="wjs_editor_assigns_themselves_as_reviewer",
            is_modal=True,
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="assigns reviewer",
            label="Select a reviewer",
            view_name="wjs_select_reviewer",
        ),
    )
    review_assignment_actions = BaseState.review_assignment_actions + (
        ReviewAssignmentAction(
            permission=permissions.is_person_working_on_article,
            name="see review",
            label="Details",
            view_name="wjs_assign_permission",
            custom_get_url=get_review_url,
        ),
        ReviewAssignmentAction(
            permission=permissions.is_article_supervisor,
            name="bypass reviewer",
            label="See as reviewer",
            view_name="wjs_assign_permission",
            custom_get_url=get_review_url_with_code,
        ),
        ReviewAssignmentAction(
            permission=permissions.is_article_editor_or_eo,
            condition=conditions.review_not_done,
            name="postpone reviewer due date",
            label="Change Reviewer due date",
            view_name="wjs_postpone_reviewer_due_date",
            is_modal=True,
        ),
        ReviewAssignmentAction(
            permission=permissions.is_article_supervisor,
            condition=conditions.review_not_done,
            name="disable reminders",
            label="Disable reminders",
            view_name="WRITEME!",
        ),
        ReviewAssignmentAction(
            permission=permissions.is_article_supervisor,
            condition=conditions.review_not_done,
            name="set visibility",
            label='Set visibility rights <i class="bi bi-sliders"></i>',
            view_name="wjs_assign_permission",
            custom_get_url=get_edit_permissions_url_review_assignment,
        ),
        ReviewAssignmentAction(
            permission=permissions.is_article_editor_or_eo,
            condition=conditions.review_not_done,
            name="editor deselect reviewer",
            label="Deselect reviewer",
            view_name="wjs_deselect_reviewer",
        ),
    )

    @classmethod
    def article_requires_editor_attention(cls, article: Article, **kwargs) -> str:
        """
        Rifle through the situations that require attention.

        Return True as soon as one is found.
        This can be use to highlight a paper that requires some action.

        Warning: States also have a assignment_requires_attention function,
        but that works on a review assignment.

        """
        if attention_flag := conditions.needs_assignment(article):
            return attention_flag
        if attention_flag := conditions.all_assignments_completed(article):
            return attention_flag
        if attention_flag := conditions.editor_as_reviewer_is_late(article):
            return attention_flag
        if attention_flag := conditions.any_reviewer_is_late_after_reminder(article):
            return attention_flag
        # The `conditions.one_review_assignment_late(article)` is more invasive: it reports all late assignments, not
        # just the editors'
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""

    @classmethod
    def assignment_requires_editor_attention(cls, assignment: ReviewAssignment, user: Account = None) -> str:
        """Rifle through the situations that require attention.

        Return True as soon as one is found.
        """
        if attention_flag := conditions.is_late_invitation(assignment, user=None):
            return attention_flag
        if attention_flag := conditions.is_late(assignment, user=None):
            return attention_flag
        return ""

    @classmethod
    def article_requires_eo_attention(cls, article: Article, **kwargs) -> str:
        """
        Tell if the article requires attention by the EO.
        """
        if attention_flag := conditions.eo_has_unread_messages(article):
            return attention_flag
        if attention_flag := conditions.article_has_old_unread_message(article):
            return attention_flag
        return ""

    @classmethod
    def article_requires_director_attention(cls, article: Article, **kwargs) -> str:
        """
        Tell if the article requires attention by the directors.
        """
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""

    @classmethod
    def article_requires_author_attention(cls, article: Article, **kwargs) -> str:
        """
        Rifle through the situations that require attention.
        """
        if attention_flag := conditions.author_revision_is_late(article):
            return attention_flag
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""

    @classmethod
    def article_requires_reviewer_attention(cls, article: Article, **kwargs) -> str:
        """Rifle through the situations that require attention."""
        if attention_flag := conditions.reviewer_report_is_late(article):
            return attention_flag
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""


class Submitted(BaseState):
    """Submitted"""


class Withdrawn(BaseState):
    """Withdrawn"""


class IncompleteSubmission(BaseState):
    """Incomplete submission"""


class NotSuitable(BaseState):
    """Not suitable"""


class PaperHasEditorReport(BaseState):
    """Paper has editor report"""


class Accepted(BaseState):
    """Accepted"""


class ToBeRevised(BaseState):
    """To be revised"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            condition=conditions.pending_revision_request,
            permission=permissions.is_article_editor,
            name="postpone author revision deadline",
            label="",
            view_name="wjs_postpone_revision_request",
            custom_get_url=get_url_with_last_editor_revision_request_pk,
            is_modal=True,
        ),
        ArticleAction(
            condition=conditions.pending_edit_metadata_request,
            permission=permissions.is_article_editor,
            name="postpone author edit metadata deadline",
            label="",
            view_name="wjs_postpone_revision_request",
            custom_get_url=get_url_with_last_editor_revision_request_pk,
            is_modal=True,
        ),
        ArticleAction(
            condition=conditions.pending_revision_request,
            permission=permissions.is_article_author,
            name="submits new version",
            label="",
            view_name="do_revisions",
            custom_get_url=get_do_revision_url,
        ),
        ArticleAction(
            condition=conditions.pending_revision_request,
            permission=permissions.is_article_author,
            name="confirms previous manuscript",
            label="",
            view_name="do_revisions",
            custom_get_url=get_do_revision_url,
        ),
        ArticleAction(
            condition=conditions.pending_edit_metadata_request,
            permission=permissions.is_article_author,
            name="edit metadata",
            label="",
            view_name="do_revisions",
            custom_get_url=get_edit_metadata_revision_url,
        ),
    )

    @classmethod
    def article_requires_editor_attention(cls, article: Article, **kwargs) -> str:
        """
        Rifle through the situations that require attention.
        """
        if attention_flag := conditions.author_revision_is_late(article):
            return attention_flag
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""

    @classmethod
    def article_requires_author_attention(cls, article: Article, **kwargs) -> str:
        """
        Rifle through the situations that require attention.
        """
        if attention_flag := conditions.author_revision_is_late(article):
            return attention_flag
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""

    @classmethod
    def article_requires_reviewer_attention(cls, article: Article, **kwargs) -> str:
        """
        Rifle through the situations that require attention.
        """
        if attention_flag := conditions.reviewer_report_is_late(article):
            return attention_flag
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""


class Rejected(BaseState):
    """Rejected"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=conditions.is_appeal_available,
            name="admin opens an appeal",
            label="Open Appeal",
            view_name="wjs_open_appeal",
            is_modal=True,
        ),
    )


class UnderAppeal(BaseState):
    """Under appeal after rejection"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.is_article_author,
            name="author submits appeal",
            label="Submit appeal",
            view_name="do_revisions",
            custom_get_url=get_do_revision_url,
        ),
    )

    @classmethod
    def article_requires_eo_attention(cls, article: Article, **kwargs) -> str:
        """
        Tell if the article requires attention by the EO.
        """
        if attention_flag := conditions.author_revision_is_late(article):
            return attention_flag
        return ""


class PaperMightHaveIssues(BaseState):
    """Paper might have issues"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=base_permissions.has_eo_role,
            name="requires resubmission",
            label="Requires resubmission",
            view_name="wjs_article_admin_decision",
            querystring_params={"decision": ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION},
        ),
        ArticleAction(
            permission=base_permissions.has_eo_role,
            name="deems not suitable",
            label="Mark as not suitable",
            view_name="wjs_article_admin_decision",
            querystring_params={"decision": ArticleWorkflow.Decisions.NOT_SUITABLE},
        ),
        ArticleAction(
            permission=base_permissions.has_eo_role,
            name="deems issue unimportant",
            label="Queue for review",
            view_name="wjs_article_dispatch_assignment",
        ),
    )


class ReadyForTypesetter(BaseState):
    """
    Ready for typesetter
    """

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.has_typesetter_role_by_article,
            name="typ takes in charge",
            label="Take in charge",
            view_name="wjs_typ_take_in_charge",
        ),
    )


class TypesetterSelected(BaseState):
    """
    Typesetter selected
    """

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="uploads sources",  # this pairs with the one above ⮵
            label="Upload sources",
            view_name="wjs_typesetter_upload_files",
            custom_get_url=get_url_with_typesetting_assignment_pk,
            is_modal=True,
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="tests galley generation",
            label="Test galley generation",
            view_name="wjs_typesetter_galley_generation",
            disabled=galleys_cannot_be_tested,
            custom_get_url=get_url_with_typesetting_assignment_pk,
            is_post=True,
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter_or_eo,
            name="CRUD attachments",
            label="Manage supplementary material",
            view_name="wjs_article_esm_files",
            is_modal=True,
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="toggle paper non-publishable flag",
            label="Mark Unpublishable",
            view_name="wjs_toggle_publishable",
            custom_get_label=get_publishable_label,
            confirm=_("Are you sure you want to mark the paper as unpublishable?"),
            is_post=True,
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Send to Author",
            label="Send to Author",
            view_name="wjs_ready_for_proofreading",
            custom_get_url=get_url_with_typesetting_assignment_pk,
            confirm=_("Are you sure you want to send the paper to the author?"),
            is_post=True,
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Contact Author",
            label="Contact Author",
            view_name="wjs_message_write_to_auwm",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Open Gitlab issue",
            label="Open Gitlab issue",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_author,
            name="write_to_typesetter",
            label="Write to typesetter",
            view_name="wjs_message_write_to_typ",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter_and_paper_can_go_rfp,
            name="typesetter_deems_paper_ready_for_publication",
            label="Paper is ready for publication",
            view_name="wjs_review_rfp",
            disabled=cannot_be_set_rfp_or_galleys_not_present,
            confirm=_("Are you sure you want to set the paper ready for publication?"),
            is_post=True,
        ),
    )

    @classmethod
    def article_requires_typesetter_attention(cls, article: Article, user: Account, **kwargs) -> str:
        """
        Tell if the article requires attention by the typesetter.
        """
        assignment = (
            TypesettingAssignment.objects.filter(
                round__article=article,
                typesetter=user,
            )
            .order_by("round__round_number")
            .last()
        )
        if attention_flag := conditions.is_typesetter_late(assignment):
            return attention_flag
        return ""

    @classmethod
    def article_requires_eo_attention(cls, article: Article, **kwargs) -> str:
        """
        Tell if the article requires attention by the EO.
        """
        assignment = (
            TypesettingAssignment.objects.filter(
                round__article=article,
            )
            .order_by("round__round_number")
            .last()
        )
        if attention_flag := conditions.is_typesetter_late(assignment):
            return attention_flag
        return ""


class Proofreading(BaseState):
    """
    Proofreading.

    In this state, the author to review the typesetted galleys and
    send corrections back to the typesetter.
    """

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.is_article_author,
            name="author_add_extra_information",
            label="Send extra article information",
            view_name="wjs_article_additional_info",
            condition=conditions.needs_extra_article_information,
        ),
        ArticleAction(
            permission=permissions.is_article_author,
            name="author_sends_corrections",
            label="Send corrections",
            view_name="wjs_list_annotated_files",
            custom_get_url=get_url_with_galleyproofing_pk,
        ),
        ArticleAction(
            permission=permissions.is_article_author,
            name="write_to_typesetter",
            label="Write to typesetter",
            view_name="wjs_message_write_to_typ",
        ),
        ArticleAction(
            permission=permissions.is_article_author_and_paper_can_go_rfp,
            name="author_deems_paper_ready_for_publication",
            label="Paper is ready for publication",
            view_name="wjs_review_rfp",
            condition=can_be_set_rfp_wrapper,
            is_post=True,
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Contact Author",
            label="Contact Author",
            view_name="wjs_message_write_to_auwm",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="toggle paper non-publishable flag",
            label="Mark Unpublishable",
            view_name="wjs_toggle_publishable",
            is_htmx=True,
            custom_get_css_class=get_unpulishable_css_class,
            custom_get_label=get_publishable_label,
        ),
    )

    @classmethod
    def article_requires_typesetter_attention(cls, article: Article, user: Account, **kwargs) -> str:
        """
        Tell if the article requires attention by the typesetter.
        """
        assignment = (
            GalleyProofing.objects.filter(
                round__article=article,
                proofreader=article.correspondence_author,
                round__typesettingassignment__typesetter=user,
            )
            .order_by("round__round_number")
            .last()
        )
        if attention_flag := conditions.is_author_proofing_late(assignment):
            return attention_flag
        return ""

    @classmethod
    def article_requires_eo_attention(cls, article: Article, **kwargs) -> str:
        """
        Tell if the article requires attention by the EO.
        """
        assignment = (
            GalleyProofing.objects.filter(
                round__article=article,
                proofreader=article.correspondence_author,
            )
            .order_by("round__round_number")
            .last()
        )
        if attention_flag := conditions.is_author_proofing_late(assignment):
            return attention_flag
        return ""


class ReadyForPublication(BaseState):
    """
    Ready for publication
    """

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.has_eo_role_by_article,
            name="begin publication",
            label="Publish",
            tooltip="Begin the publication process",
            view_name="wjs_review_begin_publication",
            confirm=_("Are you sure you want to start the publication process?"),
            is_post=True,
        ),
        ArticleAction(
            permission=permissions.has_eo_role_by_article,
            name="Send paper back to Typesetter",
            label="Send paper back to Typesetter",
            tooltip="Ask the typesetter for some changes...",
            view_name="wjs_send_back_to_typ",
        ),
    )


class PublicationInProgress(BaseState):
    """
    Publication in progress
    """

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.has_eo_role_by_article,
            name="finish publication",
            label="Finish publication",
            tooltip="Retry the publication process",
            view_name="wjs_review_finish_publication",
            confirm=_("Are you sure you want to retry the publication process?"),
            is_post=True,
        ),
    )


class SendToEditorForCheck(BaseState):
    """
    Send to editor for check
    """


class Published(BaseState):
    """Published"""
