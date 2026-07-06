from django.core.management.base import BaseCommand
from plugins.wjs_review.models import ArticleWorkflow
from submission.models import STAGE_ARCHIVED, STAGE_REJECTED, Article


class Command(BaseCommand):
    help = "Fix Old REJECTED / WITHDRAWN stage."  # noqa

    def handle(self, *args, **options):
        """
        For all Articles with articleworkflow state REJECTED or WITHDRAWN,
        ensure to set article.stage to STAGE_REJECTED or STAGE_ARCHIVED.
        """
        for article in Article.objects.filter(
            articleworkflow__state__in=[ArticleWorkflow.ReviewStates.REJECTED, ArticleWorkflow.ReviewStates.WITHDRAWN]
        ):
            if article.articleworkflow.state == ArticleWorkflow.ReviewStates.REJECTED:
                article.stage = STAGE_REJECTED
            else:
                article.stage = STAGE_ARCHIVED
            article.save()
