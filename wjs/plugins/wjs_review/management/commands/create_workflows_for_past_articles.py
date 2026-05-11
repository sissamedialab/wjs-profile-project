from django.core.management.base import BaseCommand
from plugins.wjs_review.models import ArticleWorkflow
from submission import models


class Command(BaseCommand):
    help = "Create ArticleWorkflow linked models if not already created with some default values."  # noqa: A003

    STAGE_MAPPING = {
        models.STAGE_UNSUBMITTED: ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
        models.STAGE_UNASSIGNED: ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED,
        models.STAGE_ASSIGNED: ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
        models.STAGE_UNDER_REVIEW: ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
        models.STAGE_UNDER_REVISION: ArticleWorkflow.ReviewStates.TO_BE_REVISED,
        models.STAGE_REJECTED: ArticleWorkflow.ReviewStates.REJECTED,
        models.STAGE_ACCEPTED: ArticleWorkflow.ReviewStates.ACCEPTED,
        models.STAGE_EDITOR_COPYEDITING: ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
        models.STAGE_AUTHOR_COPYEDITING: ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
        models.STAGE_FINAL_COPYEDITING: ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
        models.STAGE_TYPESETTING: ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
        models.STAGE_TYPESETTING_PLUGIN: ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
        models.STAGE_PROOFING: ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
        models.STAGE_READY_FOR_PUBLICATION: ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
        models.STAGE_PUBLISHED: ArticleWorkflow.ReviewStates.PUBLISHED,
        models.STAGE_PREPRINT_REVIEW: ArticleWorkflow.ReviewStates.PUBLISHED,
        models.STAGE_PREPRINT_PUBLISHED: ArticleWorkflow.ReviewStates.PUBLISHED,
        models.STAGE_ARCHIVED: ArticleWorkflow.ReviewStates.WITHDRAWN,
    }

    def handle(self, *args, **options):
        """
        Create ArticleWorkflow for old imported articles.
        """
        for article in models.Article.objects.all():
            try:
                article.articleworkflow
            except ArticleWorkflow.DoesNotExist:
                state = self.STAGE_MAPPING.get(article.stage, ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES)
                articleworkflow = ArticleWorkflow.objects.create(
                    article=article,
                    state=state,
                )
                print(
                    f"Created ArticleWorkflow linked model for Article pk {article.pk} "
                    f"with state={articleworkflow.state} "
                )
