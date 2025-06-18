from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django_fsm.signals import post_transition
from hijack.signals import hijack_started
from submission.models import Article, Section

from .models import ArticleWorkflow, WjsSection


@receiver(post_save, sender=Article)
def create_workflow_handler(sender, instance, created, **kwargs):
    """Create :py:class:`ArticleWorkflow` when an article is created."""
    if not created:
        return
    ArticleWorkflow.objects.create(article=instance)


@receiver(post_transition, sender=ArticleWorkflow)
def log_state_change(instance, **kwargs):
    instance.latest_state_change = timezone.now()
    instance.save()


@receiver(post_save, sender=Section)
def create_section_handler(sender, instance, created, **kwargs):
    """Create :py:class:`WjsSection` when a new Section is created."""
    if not created:
        return
    WjsSection(section=instance).save_base(raw=True)


@receiver(hijack_started, sender=None)
def store_current_url_into_session(**kwargs) -> None:
    """
    Store the current URL into the web session.

    Useful to be used as "next" URL when releasing the hijack.
    Use as:
    {% load wjs_tags %}  <!-- neede for "get_value" -->
    ...next={{ request.session|get_value:'hijack_start_url'|default:'/' }}
    """
    request = kwargs["request"]
    # We allow for missing "referer" mainly for tests
    request.session["hijack_start_url"] = request.headers.get("referer", "/")
