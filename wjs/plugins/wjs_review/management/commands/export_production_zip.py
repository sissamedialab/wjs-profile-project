from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from submission.models import STAGE_ACCEPTED, Article

from ...metadata_export.mappers import map_ms_no
from ...metadata_export.service import build_production_export_zip


class Command(BaseCommand):
    help = "Build an article's production export zip and write it to output_dir as {ms_no}.zip."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument("article_id", type=int, help="ID of the article to export")
        parser.add_argument("output_dir", type=str, help="Directory to write {ms_no}.zip into")
        parser.add_argument(
            "--force-accepted",
            action="store_true",
            help=(
                "Treat the article as accepted for every stage-gated mapper (e.g. "
                "map_decision_status/map_decision_label), regardless of its actual stage. "
                "In-memory only -- never persisted to the database. For manually inspecting "
                "the zip's packaging against an article that isn't really accepted yet."
            ),
        )

    def handle(self, *args, **options):
        article_id = options["article_id"]
        try:
            article = Article.objects.get(id=article_id)
        except Article.DoesNotExist:
            raise CommandError(f"Article {article_id} does not exist") from Article.DoesNotExist

        if options["force_accepted"]:
            article.stage = STAGE_ACCEPTED

        zip_bytes = build_production_export_zip(article)
        output_path = Path(options["output_dir"]) / f"{map_ms_no(article)}.zip"
        output_path.write_bytes(zip_bytes)
