"""Assignment events functions, that are called when an article is submitted.

Journal level configuration is made using the 'WJS_ARTICLE_ASSIGNMENT_FUNCTIONS' setting
"""

from typing import TYPE_CHECKING, Optional

from core.models import AccountRole, Role
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.module_loading import import_string
from submission.models import Article
from utils.logic import get_current_request

from wjs.jcom_profile.constants import EO_GROUP
from wjs.jcom_profile.models import EditorAssignmentParameters

if TYPE_CHECKING:
    from ..models import WjsEditorAssignment


Account = get_user_model()


def get_special_issue_parameters(article):
    """
    Get special issue EditorAssignmentParameters depending on article special issue editors.

    :param article: The assigned article.
    :return: The Editor assignment parameters for a special issue article.
    """
    if article.primary_issue:
        return EditorAssignmentParameters.objects.filter(
            journal=article.journal,
            editor__in=article.primary_issue.editors.all(),
        )


def default_assign_editors_to_articles(**kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign editors to article for review. Default algorithm."""
    from ..logic import BaseAssignToEditor

    article = kwargs["article"]
    if article.primary_issue and article.primary_issue.editors:
        parameters = get_special_issue_parameters(article)
    else:
        editors = AccountRole.objects.filter(
            journal=article.journal,
            role=Role.objects.get(slug="section-editor"),
        ).values_list("user")
        parameters = EditorAssignmentParameters.objects.filter(journal=article.journal, editor__in=editors)
    if parameters:
        request = get_current_request()
        if parameter := parameters.order_by("workload", "id").first():
            assignment = BaseAssignToEditor(
                editor=parameter.editor, article=article, request=request, first_assignment=True
            ).run()
            return assignment


def jcom_assign_editors_to_articles(**kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign editors to article for review. JCOM algorithm."""
    from ..logic import BaseAssignToEditor

    article = kwargs["article"]

    if article.primary_issue and article.primary_issue.editors:
        parameters = get_special_issue_parameters(article)
    else:
        directors = AccountRole.objects.filter(
            journal=article.journal,
            role=Role.objects.get(slug="director"),
        ).values_list("user")
        parameters = EditorAssignmentParameters.objects.filter(journal=article.journal, editor__in=directors)
    if parameters:
        request = get_current_request()
        if parameter := parameters.order_by("workload", "id").first():
            assignment = BaseAssignToEditor(
                editor=parameter.editor, article=article, request=request, first_assignment=True
            ).run()
            return assignment


def assign_editor_random(**kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign a random editor, for test purposes."""
    from ..logic import BaseAssignToEditor

    article = kwargs["article"]

    if (
        selected_editor_id := AccountRole.objects.filter(
            journal=article.journal, role=Role.objects.get(slug="section-editor")
        )
        .values_list("user")
        .order_by("?")
        .first()
    ):
        request = get_current_request()
        selected_editor = Account.objects.get(id=selected_editor_id[0])
        assignment = BaseAssignToEditor(
            editor=selected_editor, article=article, request=request, first_assignment=True
        ).run()
        return assignment


def assign_eo_to_articles(**kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign EO to article based on their workload."""
    article = kwargs["article"]

    eo_users = Account.objects.filter(groups__name=EO_GROUP)
    parameter = (
        EditorAssignmentParameters.objects.filter(journal=article.journal, editor__in=eo_users)
        .order_by("workload", "id")
        .first()
    )
    if parameter:
        return parameter.editor


def assign_eo_random(**kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign a random EO member, for test purposes."""
    return Account.objects.filter(groups__name=EO_GROUP).order_by("?").first()


def dispatch_assignment(**kwargs) -> Optional["WjsEditorAssignment"]:
    """Dispatch editors assignment on journal basis, selecting the requested assignment algorithm."""
    journal = kwargs["article"].journal.code
    assignment_function = import_string(
        settings.WJS_ARTICLE_ASSIGNMENT_FUNCTIONS.get(journal, settings.WJS_ARTICLE_ASSIGNMENT_FUNCTIONS.get(None)),
    )
    return assignment_function(**kwargs)


def dispatch_eo_assignment(**kwargs) -> Optional[Account]:
    """
    Dispatch EO assignment.

    Contrary to :py:function:`wjs_review.events.handlers.dispatch_assignment`, this function directly assigns the EO
    to the article as we don't have a workflow for EO assignment.
    """
    article: Article = kwargs["article"]
    journal = article.journal.code
    assignment_function = import_string(
        settings.WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS.get(
            journal,
            settings.WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS.get(None),
        ),
    )
    eo_user = assignment_function(**kwargs)
    if eo_user:
        article.articleworkflow.eo_in_charge = eo_user
        article.articleworkflow.save()
