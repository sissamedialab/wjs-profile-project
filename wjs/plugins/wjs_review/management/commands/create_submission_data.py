from django.core.management.base import BaseCommand
from plugins.wjs_submission.models import ArticleSubmission
from submission.models import Article


class Command(BaseCommand):
    help = "Create ArticleSubmission linked models if not already created."  # noqa: A003

    def handle(self, *args, **options):
        """
        Create ArticleSubmission linked models if not already created.

        :param args: Arbitrary positional arguments passed to the handler.
        :type args: tuple
        :param options: Arbitrary keyword arguments passed to the handler.
        :type options: dict
        :raises ArticleSubmission.DoesNotExist: Raised when an article does not have an associated
            submission data.
        """
        for article in Article.objects.all():
            try:
                article.submission_data
            except ArticleSubmission.DoesNotExist:
                ArticleSubmission.objects.create(article=article)
