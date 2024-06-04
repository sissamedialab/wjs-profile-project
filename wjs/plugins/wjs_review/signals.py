from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django_fsm.signals import post_transition
from submission.models import Article

from .models import ArticleWorkflow


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
