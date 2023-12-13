"""Assignment events functions, that are called when an article is submitted.

Journal level configuration is made using the 'WJS_ARTICLE_ASSIGNMENT_FUNCTIONS' setting
"""
from typing import Optional

from core.models import AccountRole, Role
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.module_loading import import_string
from review import models as review_models
from review.logic import assign_editor
from utils.logic import get_current_request

from wjs.jcom_profile.models import EditorAssignmentParameters

Account = get_user_model()


def get_special_issue_parameters(article):
    """
    Get special issue EditorAssignmentParameters depending on article special issue editors.

    :param article: The assigned article.
    :return: The Editor assignment parameters for a special issue article.
    """
    return EditorAssignmentParameters.objects.filter(
        journal=article.journal,
        editor__in=article.articlewrapper.special_issue.editors.all(),
    )


def default_assign_editors_to_articles(**kwargs) -> Optional[review_models.EditorAssignment]:
    """Assign editors to article for review. Default algorithm."""
    article = kwargs["article"]
    if article.articlewrapper.special_issue and article.articlewrapper.special_issue.editors:
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
            assignment, created = assign_editor(
                article,
                parameter.editor,
                "editor",
                request,
                False,
            )
            return assignment


def jcom_assign_editors_to_articles(**kwargs) -> Optional[review_models.EditorAssignment]:
    """Assign editors to article for review. JCOM algorithm."""
    article = kwargs["article"]

    if article.articlewrapper.special_issue and article.articlewrapper.special_issue.editors:
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
            assignment, created = assign_editor(
                article,
                parameter.editor,
                "editor",
                request,
                False,
            )
            return assignment


def dispatch_assignment(**kwargs) -> Optional[review_models.EditorAssignment]:
    """Dispatch editors assignment on journal basis, selecting the requested assignment algorithm."""
    journal = kwargs["article"].journal.code
    if journal in settings.WJS_ARTICLE_ASSIGNMENT_FUNCTIONS:
        return import_string(settings.WJS_ARTICLE_ASSIGNMENT_FUNCTIONS.get(journal))(**kwargs)
    else:
        return import_string(settings.WJS_ARTICLE_ASSIGNMENT_FUNCTIONS.get(None))(**kwargs)


def dispatch_eo_assignment(**kwargs) -> Optional[Account]:
    """Dispatch EO assignment."""
    # TODO: writeme in #608
    from wjs.jcom_profile.apps import GROUP_EO

    return Account.objects.filter(groups__name=GROUP_EO).order_by("?").first()
