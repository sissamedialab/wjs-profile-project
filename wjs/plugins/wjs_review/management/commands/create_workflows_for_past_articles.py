from django.core.management.base import BaseCommand, CommandError
from plugins.wjs_review.models import ArticleWorkflow
from submission import models


class Command(BaseCommand):
    help = "Create ArticleWorkflow linked models if not already created with some default values."  # noqa: A003

    def handle(self, *args, **options):
        """Create ArticleWorkflow for old imported articles."""
        for article in models.Article.objects.all():
            try:
                article.articleworkflow
            except ArticleWorkflow.DoesNotExist:
                if article.stage != models.STAGE_PUBLISHED:
                    raise CommandError(
                        f"Article pk {article.pk} has stage {article.stage}, expected {models.STAGE_PUBLISHED}."
                        " Please check for import errors.",
                    )
                articleworkflow = ArticleWorkflow.objects.create(
                    article=article,
                    state=ArticleWorkflow.ReviewStates.PUBLISHED,
                )
                print(
                    f"Created ArticleWorkflow linked model for Article pk {article.pk} "
                    f"with state={articleworkflow.state} ",
                )
