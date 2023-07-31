from django.db.models.signals import post_save
from django.dispatch import receiver
from submission.models import Article

from .models import ArticleWorkflow


@receiver(post_save, sender=Article)
def create_workflow_handler(sender, instance, created, **kwargs):
    """Create :py:class:`ArticleWorkflow` when an article is created."""
    if not created:
        return
    ArticleWorkflow.objects.create(article=instance)
