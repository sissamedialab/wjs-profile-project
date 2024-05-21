from core.models import Account
from django import template
from django.db import models

from ..logic__visibility import PermissionChecker
from ..models import ArticleWorkflow, PermissionAssignment

register = template.Library()


@register.simple_tag(takes_context=True)
def user_has_access_to(
    workflow: ArticleWorkflow,
    user: Account,
    target: models.Model,
    permission_type: PermissionAssignment.PermissionType = "",
) -> bool:
    """
    Check if the user has access to the given attribute of the workflow.

    It must be used in the templates before any block containing models linked to the article subject to review.

    .. block:: html

        {% for review in round.reviewassignment_set.all %}
            {% user_has_access_to workflow user review "all" as all_access %}
            {% user_has_access_to workflow user review "no_names" as no_names %}
            {% if no_names %}
                <div class="card-body">
                    Assignment {{ assignment.id }}{% if all_access %} to {{ assignment.reviewer }}{% endif %}.
                </div>
            {% endif %}
        {% endfor %}

    :param workflow: The workflow to check access to.
    :type workflow: ArticleWorkflow
    :param user: The user to check access for.
    :type user: Account
    :param target: The object we are checking permission one.
    :type target: Model
    :param permission_type: The permission set to check access for.
    :type permission_type: PermissionAssignment.PermissionType
    :return: True if the user has access, False otherwise.
    :rtype: bool
    """
    return PermissionChecker()(workflow, user, target, permission_type=permission_type)
