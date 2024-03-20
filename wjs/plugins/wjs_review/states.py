"""Workflow states for the review process and their actions."""
# TODO: verify if these state classes can be used as choices for django-fsm workflow

import dataclasses
import urllib

from django.contrib.auth import get_user_model
from django.urls import reverse
from review.models import ReviewAssignment
from submission.models import Article
from utils.logger import get_logger

from wjs.jcom_profile import permissions as jcom_profile__permissions

from . import communication_utils, conditions, permissions
from .models import ArticleWorkflow

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


@dataclasses.dataclass
class ArticleAction:
    """An action that can be done on an Article."""

    permission: callable
    name: str
    label: str
    view_name: str
    tag: str = None
    order: int = 0
    tooltip: str = None
    querystring_params: dict = None
    custom_get_url: str = None

    # TODO: refactor in ArticleAction(BaseAction) ReviewAssignmentAction(BaseAction)?
    # TODO: do we still need tag? let's keep it...

    def as_dict(self, workflow: "ArticleWorkflow", user: Account):
        """Return parameters needed to build the action button."""
        if self.custom_get_url:
            url = self.custom_get_url(self, workflow, user)
        else:
            url = self.get_url(workflow, user)

        return {
            "name": self.name,
            "label": self.label,
            "tooltip": self.tooltip,
            "url": url,
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

    def has_permission(self, workflow: "ArticleWorkflow", user: Account) -> bool:
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
    article_actions = None
    review_assignment_actions = None

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


class EditorToBeSelected(BaseState):  # noqa N801 CapWords convention
    "Editor to be selected"
    article_actions = (
        ArticleAction(
            permission=permissions.is_director,
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
    "Editor selected"
    article_actions = (
        ArticleAction(
            permission=permissions.is_article_editor,
            name="declines assignment",
            label="Decline Assignment",
            view_name="wjs_unassign_assignment",
        ),
        ArticleAction(
            permission=permissions.can_assign_special_issue,
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
        # TODO: drop these? Not currently used in reviewer's templates.
        # :START
        ArticleAction(
            permission=permissions.is_reviewer,
            name="decline",
            label="",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_reviewer,
            name="write report",
            label="",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_reviewer,
            name="postpones rev.report deadline",
            label="",
            view_name="WRITEME!",
        ),
        # :END
        ArticleAction(
            permission=permissions.is_director,
            name="reminds editor",
            label="",
            view_name="WRITEME!",
        ),
    )
    review_assignment_actions = (
        ReviewAssignmentAction(
            condition=conditions.review_not_done,
            name="editor deselect reviewer",
            label="Deselect reviewer",
            view_name="WRITEME!",
            tooltip="Withdraw review assignment",
        ),
        ReviewAssignmentAction(
            condition=conditions.is_late_invitation,
            name="reminds reviewer assignment",
            label="Remind reviewer",
            tooltip="Solicit accept/decline answer from the reviewer",
            view_name="WRITEME!",
        ),
        ReviewAssignmentAction(
            condition=conditions.is_late,
            name="reminds reviewer report",
            label="Remind reviewer",
            tooltip="Solicit a report from the reviewer",
            view_name="WRITEME!",
        ),
        ReviewAssignmentAction(
            condition=conditions.review_not_done,
            name="postpone reviewer due date",
            label="Postpone Reviewer due date",
            view_name="wjs_postpone_reviewer_due_date",
        ),
        ReviewAssignmentAction(
            condition=conditions.review_done,
            name="ask report revision",
            label="",
            view_name="WRITEME!",
        ),
        ReviewAssignmentAction(
            condition=conditions.review_done,
            name="acknowledge report",
            label="Acknowledge report",
            view_name="WRITEME!",
            tooltip="Say thanks to the reviewer",
        ),
    )

    @classmethod
    def article_requires_editor_attention(cls, article: Article, **kwargs) -> str:
        """Rifle through the situations that require attention.

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
        """Tell if the article requires attention by the EO."""
        if attention_flag := conditions.eo_has_unread_messages(article):
            return attention_flag
        if attention_flag := conditions.article_has_old_unread_message(article):
            return attention_flag
        return ""

    @classmethod
    def article_requires_author_attention(cls, article: Article, **kwargs) -> str:
        """Rifle through the situations that require attention."""
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
    "Submitted"


class Withdrawn(BaseState):  # noqa N801 CapWords convention
    "Withdrawn"


class IncompleteSubmission(BaseState):  # noqa N801 CapWords convention
    "Incomplete submission"


class NotSuitable(BaseState):  # noqa N801 CapWords convention
    "Not suitable"


class PaperHasEditorReport(BaseState):  # noqa N801 CapWords convention
    "Paper has editor report"


class Accepted(BaseState):  # noqa N801 CapWords convention
    "Accepted"


class WritemeProduction(BaseState):  # noqa N801 CapWords convention
    "Writeme production"


class ToBeRevised(BaseState):  # noqa N801 CapWords convention
    "To be revised"
    article_actions = (
        ArticleAction(
            permission=permissions.is_article_editor,
            name="reminds author",
            label="",
            view_name="WRITEME!",
        ),
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
            view_name="WRITEME!",
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
        """Rifle through the situations that require attention."""
        if attention_flag := conditions.author_revision_is_late(article):
            return attention_flag
        if attention_flag := conditions.has_unread_message(article, recipient=kwargs["user"]):
            return attention_flag
        return ""

    @classmethod
    def article_requires_author_attention(cls, article: Article, **kwargs) -> str:
        """Rifle through the situations that require attention."""
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


class Rejected(BaseState):  # noqa N801 CapWords convention
    "Rejected"
    article_actions = (
        ArticleAction(
            permission=permissions.is_admin,
            name="opens appeal",
            label="",
            view_name="WRITEME!",
        ),
    )


class PaperMightHaveIssues(BaseState):  # noqa N801 CapWords convention
    "Paper might have issues"
    article_actions = (
        ArticleAction(
            permission=jcom_profile__permissions.is_eo,
            name="requires resubmission",
            label="Requires resubmission",
            view_name="wjs_article_admin_decision",
            querystring_params={"decision": ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION},
        ),
        ArticleAction(
            permission=jcom_profile__permissions.is_eo,
            name="deems not suitable",
            label="Mark as not suitable",
            view_name="wjs_article_admin_decision",
            querystring_params={"decision": ArticleWorkflow.Decisions.NOT_SUITABLE},
        ),
        ArticleAction(
            permission=jcom_profile__permissions.is_eo,
            name="deems issue unimportant",
            label="Queue for review",
            view_name="wjs_article_dispatch_assignment",
        ),
    )


class ReadyForTypesetter(BaseState):
    """Ready for typesetter"""

    article_actions = (
        ArticleAction(
            permission=permissions.is_typesetter,
            name="typ takes in charge",
            label="Take in charge",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_admin,
            name="admin assigns typesetter",
            label="Assign typesetter",
            view_name="WRITEME!",
        ),
    )


class TypesetterSelected(BaseState):
    """Typesetter selected"""

    article_actions = (
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="delete / replace source for galley generation",  # one action? 4 actions?
            label="Manage sources (for galley generation issues)",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="uploads sources",  # this pairs with the one above ⮵
            label="Upload sources",
            view_name="WRITEME!",
        ),
        ArticleAction(
            # - generate galleys:
            #   - PDF - common case
            #   - HTML - JCOM
            #   - EPUB - JCOM
            #   - XML - JHEP, JCAP
            #   - ~~PDF translations~~  # TBV: why not?
            # - used but are not  stepped / bumped:
            #   - DOI
            #   - pubilcation date
            #   - pubid
            permission=permissions.is_article_typesetter,
            name="tests galley generation",
            label="Test galley generation",  # TBD: name "Test publication?"
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="CRUD attachments",
            label="Manage supllementary material",  # CRUD Article.supplementary_files
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="toggle paper non-publishable flag",  # is this the same as one of the checks below? ⮷
            label="toggle paper non-publishable flag",
            view_name="WRITEME!",
        ),
        ArticleAction(
            # typ marks checks such as:
            # - galleys OK",
            # - supplementary material ok",
            # - ...",
            permission=permissions.is_article_typesetter,
            name="marks pre-flight checks",
            label="marks pre-flight checks",
            view_name="WRITEME!",
        ),
        ArticleAction(
            # No estemporary haikus from typ to au
            # TBV: can probably be dropped (see US ID:NA row:260 order:235)
            permission=permissions.is_article_typesetter,
            name="asks non-standard messages for author to EO",
            label="asks non-standard messages for author to EO",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Insert notes",
            label="Insert notes",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Send to Author",
            label="Send to Author",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Contact Author",
            label="Contact Author",
            view_name="WRITEME!",
        ),
        ArticleAction(
            permission=permissions.is_article_typesetter,
            name="Open Gitlab issue",
            label="Open Gitlab issue",
            view_name="WRITEME!",
        ),
    )


class Proofreading(BaseState):
    """Proofreading"""

    article_actions = (
        ArticleAction(
            permission=permissions.is_article_author,
            name="author_sends_corrections",
            label="Send corrections",
            view_name="WRITEME!",
        ),
    )


class ReadyForPublication(BaseState):
    """Ready for publication"""

    article_actions = (
        ArticleAction(
            # TBV: very similar to what typs does in TypesetterSelected!
            #      Double-check might be good.
            #
            # EO/admin marks checks such as:
            # - TA ok
            # - galleys OK",
            # - supplementary material ok",
            # - ...",
            permission=permissions.is_article_typesetter,
            name="marks pre-flight checks",
            label="marks pre-flight checks",
            view_name="WRITEME!",
        ),
    )


class SendToEditorForCheck(BaseState):
    """Send to editor for check"""

    # TBD!
    article_actions = ()


class Published(BaseState):
    """Published"""
