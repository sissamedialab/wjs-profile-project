"""Fix missing license for a specific journal."""

import pytz
from django.core.management.base import BaseCommand
from journal.models import Journal
from plugins.wjs_review.models import ArticleWorkflow
from submission.models import Article, Licence
from utils.logger import get_logger

logger = get_logger(__name__)
rome_tz = pytz.timezone("Europe/Rome")


class Command(BaseCommand):
    help = "Fix articles with missing license for a specific journal."  # noqa A003

    def add_arguments(self, parser):

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
            help="journal name (choices: JCOM, JCOMAL)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        journal = Journal.objects.get(code=options["journal"])

        logger.info("dry-run mode: %s", dry_run)

        license_obj = Licence.objects.get(journal=journal, short_name="CC BY-NC-ND 4.0")

        qs = Article.objects.filter(
            license__isnull=True,
            journal=journal,
        ).order_by("date_submitted")

        total_missing = qs.count()
        to_fix = 0
        excluded_count = 0
        no_workflow_count = 0
        fixed_ids: list[int] = []

        excluded_states = [
            ArticleWorkflow.ReviewStates.WITHDRAWN,
            ArticleWorkflow.ReviewStates.REJECTED,
            ArticleWorkflow.ReviewStates.NOT_SUITABLE,
            ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
        ]

        for article in qs:
            aw = getattr(article, "articleworkflow", None)
            if not aw:
                no_workflow_count += 1
                state = None
            else:
                state = aw.state

            # skip article in excluded states
            if state in excluded_states:
                excluded_count += 1
                logger.debug(
                    "Skip article %s (state=%s) — excluded from correction",
                    article.id,
                    state,
                )
                continue

            to_fix += 1

            # compact debug log
            preprintid = article.get_identifier("preprintid") or "NO_PREPRINTID"
            section_name = article.section.name if article.section is not None else None
            dt_sub = article.date_submitted.astimezone(rome_tz).date() if article.date_submitted else None

            logger.debug(
                "%s_%s preprint=%s section=%s state=%s date_sub=%s (%s)",
                article.journal.code,
                article.id,
                preprintid,
                section_name,
                state,
                dt_sub,
                rome_tz.zone,
            )

            if not dry_run:
                article.license_id = license_obj.id
                article.save()
                fixed_ids.append(article.id)
                logger.info(f"license {article.license} set for {article.id}")
            else:
                logger.info(f"DRY-RUN: article to be fixed: {article.id}")

        logger.info(
            "Result for %s: found %d articles without license, of which %d corrected (dry-run=%s)",
            journal.code,
            total_missing,
            to_fix if dry_run else len(fixed_ids),
            dry_run,
        )
        logger.info("%d articles skipped for excluded state: %s", excluded_count, excluded_states)
        logger.info("%d articles without workflow", no_workflow_count)
