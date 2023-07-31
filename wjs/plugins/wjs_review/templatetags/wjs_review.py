import json
from typing import Any, Dict, List, Optional

from django import template
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import QuerySet
from django_fsm import Transition
from submission.models import Article
from utils import models as janeway_utils_models
from utils.models import LogEntry

from ..models import ArticleWorkflow
from ..types import BootstrapButtonProps

register = template.Library()

Account = get_user_model()


@register.simple_tag(takes_context=True)
def get_available_transitions(context: Dict[str, Any], workflow: ArticleWorkflow) -> List[Transition]:
    """Get the available transitions for the given workflow."""
    user = context["request"].user
    return list(workflow.get_available_user_state_transitions(user=user))


@register.filter
def get_article_log_entries(article: Article) -> Optional[QuerySet[LogEntry]]:
    """Return a list of log entries."""
    # Taken from journal.views.manage_article_log
    if not article:
        return None
    content_type = ContentType.objects.get_for_model(article)
    log_entries = janeway_utils_models.LogEntry.objects.filter(content_type=content_type, object_id=article.pk)
    return log_entries


@register.simple_tag()
def reviewer_btn_props(reviewer: Account, selected: str) -> BootstrapButtonProps:
    """
    Return the properties for the select reviewer button.

    - value: the reviewer pk if no reviewer is selected or the reviewer is not the selected one
    - css_class: btn-success if the reviewer is the selected one, btn-primary otherwise
    - disabled: True if the reviewer is not active or if another reviewer is selected
    """
    try:
        selected = int(selected)
    except ValueError:
        selected = None
    current_reviewer = bool(selected and reviewer.pk == selected)
    other_reviewer = bool(selected and reviewer.pk != selected)
    no_reviewer_or_not_matching = not selected or reviewer.pk != selected
    data: BootstrapButtonProps = {
        "value": json.dumps({"reviewer": reviewer.pk}) if no_reviewer_or_not_matching else "",
        "css_class": "btn-success" if current_reviewer else "btn-primary",
        "disabled": other_reviewer or not reviewer.is_active,
    }
    return data
