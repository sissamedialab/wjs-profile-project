"""Assignment events functions, that are called when an article is submitted.

Journal level configuration is made using the 'WJS_ARTICLE_ASSIGNMENT_FUNCTIONS' setting
"""
from typing import Optional

from django.conf import settings
from django.utils.module_loading import import_string
from review import models as review_models


def get_special_issue_parameters(article):
    """
    Get special issue EditorAssignmentParameters depending on article special issue editors.

    :param article: The assigned article.
    :return: The Editor assignment parameters for a special issue article.
    """
    from wjs.jcom_profile.models import EditorAssignmentParameters

    return EditorAssignmentParameters.objects.filter(
        journal=article.journal,
        editor__in=article.articlewrapper.special_issue.editors.all(),
    )


def default_assign_editors_to_articles(**kwargs) -> Optional[review_models.EditorAssignment]:
    """Assign editors to article for review. Default algorithm."""
    from review.logic import assign_editor
    from utils.logic import get_current_request

    from wjs.jcom_profile.models import EditorAssignmentParameters

    article = kwargs["article"]
    parameters = None
    if article.articlewrapper.special_issue:
        if article.articlewrapper.special_issue.editors:
            parameters = get_special_issue_parameters(article)
    else:
        parameters = EditorAssignmentParameters.objects.filter(journal=article.journal)
    if parameters:
        request = get_current_request()
        if parameters.order_by("workload").first():
            assignment, created = assign_editor(
                article,
                parameters.order_by("workload").first().editor,
                "editor",
                request,
                False,
            )
            return assignment


def jcom_assign_editors_to_articles(**kwargs) -> Optional[review_models.EditorAssignment]:
    """Assign editors to article for review. JCOM algorithm."""
    from core.models import AccountRole, Role
    from review.logic import assign_editor
    from utils.logic import get_current_request

    from wjs.jcom_profile.models import EditorAssignmentParameters

    article = kwargs["article"]
    parameters = None

    if article.articlewrapper.special_issue:
        if article.articlewrapper.special_issue.editors:
            parameters = get_special_issue_parameters(article)
    else:
        directors = AccountRole.objects.filter(
            journal=article.journal,
            role=Role.objects.get(slug="director"),
        ).values_list("user")
        parameters = EditorAssignmentParameters.objects.filter(journal=article.journal, editor__in=directors)
    if parameters:
        request = get_current_request()
        if parameters.order_by("workload").first():
            assignment, created = assign_editor(
                article,
                parameters.order_by("workload").first().editor,
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
