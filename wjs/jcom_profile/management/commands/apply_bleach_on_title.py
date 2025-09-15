"""Apply bleach to all title."""

from django.core.management.base import BaseCommand
from submission.models import STAGE_UNSUBMITTED, Article
from utils.logger import get_logger

from wjs.jcom_profile.models import WjsSimpleBleach

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Apply WjsSimpleBleach to all article and Apply changes only when needed."  # noqa A003

    def add_arguments(self, parser):

        parser.add_argument(
            "--dry",
            action="store_true",
            dest="dry",
            default=False,
            help="Dry run (default: False)",
        )

    def handle(self, *args, **options):
        is_dry = options.get("dry", False)
        logger.info("dry mode: %s", is_dry)
        qs = Article.objects.all()
        count = 0
        totalcount = 0

        for art in qs:
            totalcount += 1
            originaltitle = art.title
            if not originaltitle and art.stage != STAGE_UNSUBMITTED:
                logger.warning(f"Error article {art.id} is empty")
            bleachedtitle = WjsSimpleBleach().to_python(art.title)
            if art.title != bleachedtitle:
                count += 1
                if not is_dry:
                    art.title = bleachedtitle
                    art.save()
                    logger.info(f"Title modified! {art.id}: '{originaltitle}' -> '{bleachedtitle}'")
                else:
                    logger.info(f"Found title to clean! {art.id}: '{originaltitle}' -> '{bleachedtitle}'")
        if not is_dry:
            logger.info(f"Modified: {count} on {totalcount}")
        else:
            logger.info(f"Found: {count} on {totalcount}")
