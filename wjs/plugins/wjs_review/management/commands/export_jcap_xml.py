from django.core.management.base import BaseCommand, CommandError
from submission.models import Article

from ...metadata_export.service import serialize_article_to_metadata_xml


class Command(BaseCommand):
    help = "Serialize an article to JCAP-style metadata XML and print it to stdout."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument("article_id", type=int, help="ID of the article to export")

    def handle(self, *args, **options):
        article_id = options["article_id"]
        try:
            article = Article.objects.get(id=article_id)
        except Article.DoesNotExist:
            raise CommandError(f"Article {article_id} does not exist") from Article.DoesNotExist

        xml = serialize_article_to_metadata_xml(article)
        self.stdout.write(xml)
