"""Workflow states for the review process and their actions."""

# TODO: verify if these state classes can be used as choices for django-fsm workflow

import dataclasses
import urllib
from typing import Callable, Optional, Type

from django.contrib.auth import get_user_model
from django.urls import reverse
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

    permission: callable
    name: str
    label: str
    view_name: str
    tag: str = None
    is_htmx: bool = False
    order: int = 0
    tooltip: str = None
    querystring_params: dict = None
    disabled: Optional[Callable] = None
    custom_get_url: Optional[Callable] = None
    custom_get_css_class: Optional[Callable] = None
    custom_get_label: Optional[Callable] = None
    condition: Optional[Callable] = None

    # TODO: refactor in ArticleAction(BaseAction) ReviewAssignmentAction(BaseAction)?
    # TODO: do we still need tag? let's keep it...

    def as_dict(self, workflow: "ArticleWorkflow", user: Account):
        """Return parameters needed to build the action button."""
        return {
            "name": self.name,
            "label": self.custom_get_label(self, workflow, user) if self.custom_get_label else self.label,
            "tooltip": self.tooltip,
            "url": self.custom_get_url(self, workflow, user) if self.custom_get_url else self.get_url(workflow, user),
            "tag": self.tag,
            "css_class": self.custom_get_css_class(self, workflow, user) if self.custom_get_css_class else None,
            "is_htmx": self.is_htmx,
            "disabled": self.disabled(self, workflow, user) if self.disabled else None,
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
        else:
            return self.condition(workflow=workflow, user=user)

    def _has_permission(self, workflow: "ArticleWorkflow", user: Account) -> bool:
        """Return true if the user has permission to run this action, given the current status of the article."""
        return self.permission(workflow, user)


@dataclasses.dataclass
class ReviewAssignmentAction:
    """An action that can be done on an ReviewAssignment."""

    condition: callable
    name: str
    label: str
    view_name: str
    tag: str = None
    order: int = 0
    tooltip: str = None

    def as_dict(self, assignment: "ReviewAssignment", user: Account):
        """Return parameters needed to build the action button."""
        return {
            "assignment": assignment,
            "name": self.name,
            "label": self.label,
            "tooltip": self.tooltip,
            "url": self.get_url(assignment, user),
        }

    def get_url(self, assignment: "ReviewAssignment", user: Account) -> str:
        """Return the URL of the view that is the entry point to manage the action."""
        if self.view_name == "WRITEME!":
            return "#"
        return reverse(self.view_name, kwargs={"pk": assignment.id})

    def condition_is_met(self, assignment: "ReviewAssignment", user: Account) -> bool:
        """TODO: examples..."""
        return self.condition(assignment, user)


# Actions organized by states
class BaseState:
    article_actions = (
        ArticleAction(
            permission=permissions.has_eo_role_by_article,
            name="assign eo",
            label="Assign / Reassign EO in charge",
            view_name="wjs_assign_eo",
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


class EditorToBeSelected(BaseState):  # noqa N801 CapWords convention
    """Editor to be selected."""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.has_director_role_by_article,
            name="selects editor",
            label="",
            view_name="WRITEME!",
        ),
    )

    @classmethod
    def article_requires_attention(cls, article: Article, **kwargs) -> str:
        """Articles in this state always require attention (from EO or director)."""
        return conditions.always(article)


class EditorSelected(BaseState):  # noqa N801 CapWords convention
    """Editor selected"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.is_article_editor,
            name="declines assignment",
            label="Decline Assignment",
            view_name="wjs_unassign_assignment",
        ),
        ArticleAction(
            permission=permissions.can_assign_special_issue_by_article,
            name="assigns different editor",
            label="Assign different Editor",
            view_name="wjs_assigns_different_editor",
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
            querystring_params={"decision": ArticleWorkflow.Decisions.MINOR_REVISION},
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
            permission=permissions.is_article_editor,
            name="assigns self as reviewer",
            label="I will review",
            tooltip="Assign myself as reviewer",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_editor,
            name="assigns reviewer",
            label="Select a reviewer",
            view_name="wjs_select_reviewer",
        ),
        ArticleAction(
            permission=permissions.is_special_issue_supervisor,
            name="assign permissions",
            label="Assign permissions",
            view_name="wjs_assign_permission",
        ),
    )
    review_assignment_actions = BaseState.review_assignment_actions + (
        ReviewAssignmentAction(
            condition=conditions.review_not_done,
            name="editor deselect reviewer",
            label="Deselect reviewer",
            view_name="WRITEME!",
            tooltip="Withdraw review assignment",
        ),
        ReviewAssignmentAction(
            condition=conditions.review_not_done,
            name="postpone reviewer due date",
            label="Postpone Reviewer due date",
            view_name="wjs_postpone_reviewer_due_date",
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


class Submitted(BaseState):  # noqa N801 CapWords convention
    """Submitted"""


class Withdrawn(BaseState):  # noqa N801 CapWords convention
    """Withdrawn"""


class IncompleteSubmission(BaseState):  # noqa N801 CapWords convention
    """Incomplete submission"""


class NotSuitable(BaseState):  # noqa N801 CapWords convention
    """Not suitable"""


class PaperHasEditorReport(BaseState):  # noqa N801 CapWords convention
    """Paper has editor report"""


class Accepted(BaseState):  # noqa N801 CapWords convention
    """Accepted"""


class ToBeRevised(BaseState):  # noqa N801 CapWords convention
    """To be revised"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.is_article_editor,
            name="postpone author revision deadline",
            label="",
            view_name="wjs_postpone_revision_request",
            custom_get_url=get_url_with_last_editor_revision_request_pk,
        ),
        ArticleAction(
            permission=permissions.is_article_author,
            name="submits new version",
            label="",
            view_name="WRITEME!",  # point to wjs-review-articles/article/1375/revision/1/
        ),
        ArticleAction(
            permission=permissions.is_article_author,
            name="confirms previous manuscript",
            label="",
            view_name="WRITEME!",
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


class Rejected(BaseState):  # noqa N801 CapWords convention
    """Rejected"""

    article_actions = BaseState.article_actions + (
        ArticleAction(
            permission=permissions.has_admin_role_by_article,
            name="opens appeal",
            label="",
            view_name="WRITEME!",
        ),
    )


class PaperMightHaveIssues(BaseState):  # noqa N801 CapWords convention
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
            view_name="WRITEME!",  # TODO: point to existing view
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
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="tests galley generation",
            label="Test galley generation",
            view_name="wjs_typesetter_galley_generation",
            disabled=galleys_cannot_be_tested,
            custom_get_url=get_url_with_typesetting_assignment_pk,
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="CRUD attachments",
            label="Manage supllementary material",  # CRUD Article.supplementary_files
            view_name="WRITEME!",
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
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Send to Author",
            label="Send to Author",
            view_name="wjs_ready_for_proofreading",
            custom_get_url=get_url_with_typesetting_assignment_pk,
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
            condition=can_be_set_rfp_wrapper,
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
            name="publish",
            label="Publish",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.has_eo_role_by_article,
            name="back to typ",
            label="Back to typ",
            view_name="WRITEME!",
        ),
    )


class SendToEditorForCheck(BaseState):
    """
    Send to editor for check
    """


class Published(BaseState):
    """Published"""
