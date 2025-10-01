"""Fix missing submission date of imported articles."""

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from journal.models import Journal
from plugins.wjs_review.models import Message
from submission.models import Article
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Fix missing submission date of articles with preprintid."  # noqa A003

    def add_arguments(self, parser):

        # add option journal

        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="dry-run (default: False)",
        )
        parser.add_argument(
            "--journal",
            dest="journal",
            required=True,
            choices=["JCOM", "JCOMAL"],
            help="journal of the articles (choices: JCOM, JCOMAL)",
        )

    def handle(self, *args, **options):
        is_dry_run = options.get("dry_run", False)
        journal = Journal.objects.get(code=options["journal"])
        logger.info("dry-run mode: %s", is_dry_run)
        qs = Article.objects.filter(
            date_submitted__isnull=True, journal=journal, identifier__id_type="preprintid"
        ).order_by("id")

        count = 0
        totalcount = 0

        for article in qs:
            preprintid = article.get_identifier("preprintid")
            if preprintid:
                totalcount += 1
                msg_list = Message.objects.filter(
                    content_type=ContentType.objects.get_for_model(article), object_id=article.id, subject="Submitted"
                ).order_by("created")
                if msg_list:
                    count += 1
                    if not is_dry_run:
                        article.date_submitted = msg_list[0].created
                        article.save()
                        article.refresh_from_db()
                        logger.info(f"Date_submitted modified {article.id}: '{article.date_submitted}'")
                    else:
                        logger.info(f"Found Date_submitted to fix {article.id}: '{msg_list[0].created}'")
        if not is_dry_run:
            logger.info(
                f"Modified in {journal.code}: {count} on {totalcount} articles"
                " with preprintid and missing submission date"
            )
        else:
            logger.info(
                f"Found in {journal.code}: {count} on {totalcount} articles"
                " with preprintid and missing submission date"
            )
