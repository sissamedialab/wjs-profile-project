from typing import Any, Dict, List

from django import template
from django_fsm import Transition
from plugins.wjs_review.models import ArticleWorkflow

register = template.Library()


@register.simple_tag(takes_context=True)
def get_available_transitions(context: Dict[str, Any], workflow: ArticleWorkflow) -> List[Transition]:
    """Get the available transitions for the given workflow."""
    user = context["request"].user
    return list(workflow.get_available_user_state_transitions(user=user))
