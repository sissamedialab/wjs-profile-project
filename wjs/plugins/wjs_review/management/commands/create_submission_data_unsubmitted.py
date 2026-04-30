from django.core.management.base import BaseCommand
from plugins.wjs_submission.access_mode import noop
from plugins.wjs_submission.models import ArticleSubmission
from submission.models import STAGE_UNSUBMITTED, Article


class Command(BaseCommand):
    help = "Create ArticleSubmission linked models if not already created with some default values."  # noqa: A003

    def handle(self, *args, **options):
        """
        Create ArticleSubmission linked models if not already created with some default values.

        :param args: Arbitrary positional arguments passed to the handler.
        :type args: tuple
        :param options: Arbitrary keyword arguments passed to the handler.
        :type options: dict
        """
        for article in Article.objects.filter(stage=STAGE_UNSUBMITTED):
            try:
                article.submission_data
            except ArticleSubmission.DoesNotExist:
                access_mode = noop(user=None, article=article).access_mode
                use_of_ai_field = article.fieldanswer_set.filter(field__name="Use of AI").first()
                article_submission = ArticleSubmission.objects.create(
                    article=article,
                    cas=ArticleSubmission.CasDeclaration.NO,
                    das=ArticleSubmission.DasDeclaration.NO,
                    access_mode=access_mode,
                    use_of_ai_flag=bool(use_of_ai_field.answer) if use_of_ai_field else False,
                )
                print(
                    f"Created ArticleSubmission linked model for Article pk {article.pk} "
                    f"with access_mode={article_submission.access_mode} "
                    f"and use_of_ai_flag={article_submission.use_of_ai_flag}"
                )
