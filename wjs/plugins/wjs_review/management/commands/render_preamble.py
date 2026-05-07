from django.core.management.base import BaseCommand, CommandError
from submission.models import Article

from ...logic__production import HandleDownloadRevisionFiles
from ...models import LatexPreamble


class Command(BaseCommand):
    help = "Render the LaTeX preamble for the given article's journal and print it to stdout."  # noqa A003

    def add_arguments(self, parser):
        parser.add_argument("article_id", type=int, help="ID of the article")

    def handle(self, *args, **options):
        article_id = options["article_id"]
        try:
            article = Article.objects.get(id=article_id)
        except Article.DoesNotExist:
            raise CommandError(f"Article {article_id} does not exist")

        if not LatexPreamble.objects.filter(journal=article.journal).exists():
            raise CommandError(f"No LaTeX preamble template found for journal {article.journal.code}")

        rendered, _ = HandleDownloadRevisionFiles._generate_automatic_preamble(article)
        self.stdout.write(rendered)
