"""Archive monthly COUNTER reports and purge old article-access records.

Meant to run monthly from cron (see install_wjs_cron): it archives the month just ended and purges the accesses older
than the retention period. To archive the past data the first time, use --since, e.g.:

    manage.py archive_counter_reports --since 2023-03

The purge refuses to delete accesses of months that have not been archived.

NB: PostgreSQL doesn't give the space of deleted rows back to the OS: after the first, big purge, run
``VACUUM FULL metrics_articleaccess`` to shrink the table.
"""

import datetime
import os
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from wjs.jcom_profile.counter_archive import (
    ArchiveCounterMonth,
    PurgeArticleAccesses,
    current_month_start,
    month_label,
)


def parse_month(value: str) -> datetime.date:
    """Parse a YYYY-MM string into the first day of that month."""
    try:
        return datetime.datetime.strptime(value, "%Y-%m").date()
    except ValueError as e:
        raise CommandError(f"Invalid month {value!r}: expected YYYY-MM.") from e


class Command(BaseCommand):
    help = "Archive monthly COUNTER reports (JR1 + aggregated accesses) and purge old ArticleAccess rows."  # noqa

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--archive-dir",
            default=os.path.join(settings.BASE_DIR, "files", "counter_archive"),
            help="Where to store the reports. Defaults to %(default)s",
        )
        parser.add_argument(
            "--month",
            type=parse_month,
            help="Month to archive (YYYY-MM). Defaults to the previous month.",
        )
        parser.add_argument(
            "--since",
            type=parse_month,
            help="Archive every month from this one (YYYY-MM) up to --month. Already archived months are skipped.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Re-archive months that are already archived. "
            "Careful: a month whose accesses have already been purged would be overwritten with empty data!",
        )
        parser.add_argument(
            "--retention-months",
            type=int,
            default=4,
            help="Keep the accesses of the current month and of this many previous months. Defaults to %(default)s",
        )
        parser.add_argument("--no-purge", action="store_true", help="Only archive, do not purge old accesses.")

    def handle(self, *args, **options):
        """Command entry point."""
        archive_dir = Path(options["archive_dir"])
        this_month = current_month_start().date()
        last_month = options["month"] or this_month - relativedelta(months=1)
        first_month = options["since"] or last_month
        if first_month > last_month:
            raise CommandError(f"--since {month_label(first_month)} is after {month_label(last_month)}.")
        if options["retention_months"] < 0:
            raise CommandError("--retention-months must not be negative.")

        month = first_month
        while month <= last_month:
            self._archive(ArchiveCounterMonth(month=month, archive_dir=archive_dir, overwrite=options["overwrite"]))
            month += relativedelta(months=1)

        if not options["no_purge"]:
            cutoff = current_month_start() - relativedelta(months=options["retention_months"])
            self._purge(PurgeArticleAccesses(cutoff=cutoff, archive_dir=archive_dir))

    def _archive(self, archiver: ArchiveCounterMonth):
        label = month_label(archiver.month)
        if archiver.is_archived and not archiver.overwrite:
            self.stdout.write(f"{label}: already archived, skipping.")
            return
        try:
            paths = archiver.run()
        except ValidationError as e:
            raise CommandError(" ".join(e.messages)) from e
        self.stdout.write(f"{label}: archived to {', '.join(str(path) for path in paths)}")

    def _purge(self, purger: PurgeArticleAccesses):
        try:
            deleted = purger.run()
        except ValidationError as e:
            raise CommandError(" ".join(e.messages)) from e
        self.stdout.write(f"Purged {deleted} accesses older than {purger.cutoff:%Y-%m-%d}.")
