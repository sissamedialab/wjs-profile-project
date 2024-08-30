from typing import Optional

from core.models import Account
from django import template
from django.db import models

from .. import conditions
from ..custom_types import ButtonSize
from ..logic__visibility import PermissionChecker
from ..models import ArticleWorkflow, PermissionAssignment

register = template.Library()


@register.inclusion_tag(takes_context=True, filename="wjs_review/base/elements/hijack.html")
def hijack_button(context: dict, user: Account, size: ButtonSize = "small") -> dict:
    """
    Render a hijack button to impersonate the user.

    .. block:: html

        {% hijack_button user=<target-user> %}

    :param context: The context to render the hijack button.
    :type context: dict
    :param user: The user to impersonate.
    :type user: Account
    :param size: The size of the button.
    :type size: ButtonSize
    :return: Context to render the hijack button.
    :rtype: dict
    """
    sizes = {
        "small": "btn-sm",
        "medium": "",
        "large": "btn-lg",
    }
    context["target_user"] = user
    context["display_classes"] = f"btn-warning {sizes[size]}"
    return context


@register.simple_tag(takes_context=True)
def user_has_access_to(
    context: dict,
    workflow: ArticleWorkflow,
    user: Account,
    target: models.Model,
    permission_type: PermissionAssignment.PermissionType = "",
    review_round: Optional[int] = None,
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
    :param review_round: Review round number to check access for. If 0 current review round is used,
        if None review round check is not used.
    :type review_round: Optional[int]
    :return: True if the user has access, False otherwise.
    :rtype: bool
    """
    return PermissionChecker()(workflow, user, target, permission_type=permission_type, review_round=review_round)


@register.simple_tag(takes_context=True)
def user_can_set_permission(
    context: dict,
    workflow: ArticleWorkflow,
    user: Account,
) -> bool:
    """
    Check if the user can edit permissions on the workflow.

    Permission is only available:

    - current article editor
    - director
    - EO

    .. block:: html

        {% user_can_set_permission workflow user as can_set_permission %}
        {% if can_set_permission %}<button>Set permission</button>{% endif %}

    :param workflow: The workflow to check access to.
    :type workflow: ArticleWorkflow
    :param user: The user to check access for.
    :type user: Account
    :return: True if the user can edit permission, False otherwise.
    :rtype: bool
    """

    return bool(conditions.can_edit_permissions(workflow, user))
