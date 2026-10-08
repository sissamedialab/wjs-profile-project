from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from submission.models import STAGE_ACCEPTED, Article

from ...logic__production import SendProductionXMLToPublisher
from ...metadata_export.mappers import map_ms_no
from ...metadata_export.service import build_production_export_zip
from ...metadata_export.sftp import SFTPSendError


class Command(BaseCommand):
    help = (  # noqa: A003
        "Build an article's production export zip and write it to output_dir as {ms_no}.zip, "
        "or --send it to the publisher instead."
    )

    def add_arguments(self, parser):
        parser.add_argument("article_id", type=int, help="ID of the article to export")
        parser.add_argument(
            "output_dir",
            type=str,
            nargs="?",
            help="Directory to write {ms_no}.zip into. Required unless --send is given.",
        )
        parser.add_argument(
            "--send",
            action="store_true",
            help=(
                "Send the zip to the publisher via SendProductionXMLToPublisher (silently: no "
                "attention condition or EO message is created either way) instead of building "
                "and saving it locally. Mutually exclusive with output_dir."
            ),
        )
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
        output_dir = options["output_dir"]
        send = options["send"]
        if send and output_dir:
            raise CommandError("output_dir is not used with --send; the zip is sent, not saved")
        if not send and not output_dir:
            raise CommandError("output_dir is required unless --send is given")

        try:
            article = Article.objects.get(id=article_id)
        except Article.DoesNotExist:
            raise CommandError(f"Article {article_id} does not exist") from Article.DoesNotExist

        if options["force_accepted"]:
            article.stage = STAGE_ACCEPTED

        if send:
            try:
                SendProductionXMLToPublisher(articleworkflow=article.articleworkflow).run(silent=True)
            except SFTPSendError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(self.style.SUCCESS(f"Sent article {article_id}'s production export zip."))
            return

        zip_bytes = build_production_export_zip(article)
        output_path = Path(output_dir) / f"{map_ms_no(article)}.zip"
        output_path.write_bytes(zip_bytes)
