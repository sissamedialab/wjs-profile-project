from typing import Any, Dict, List

from django import template
from django.contrib.contenttypes.models import ContentType
from django_fsm import Transition
from plugins.wjs_review.models import ArticleWorkflow
from utils import models as janeway_utils_models

register = template.Library()


@register.simple_tag(takes_context=True)
def get_available_transitions(context: Dict[str, Any], workflow: ArticleWorkflow) -> List[Transition]:
    """Get the available transitions for the given workflow."""
    user = context["request"].user
    return list(workflow.get_available_user_state_transitions(user=user))


@register.filter
def get_article_log_entries(article):
    """Return a list of log entries."""
    # Taken from journal.views.manage_article_log
    if not article:
        return None
    content_type = ContentType.objects.get_for_model(article)
    log_entries = janeway_utils_models.LogEntry.objects.filter(content_type=content_type, object_id=article.pk)
    return log_entries
