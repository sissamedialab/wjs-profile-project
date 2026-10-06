"""Archive COUNTER reports and purge old article-access records.

Janeway records one ``metrics.ArticleAccess`` row per article view/download and builds the COUNTER reports (JR1 etc.)
from those raw rows, which makes ``metrics_articleaccess`` grow without bounds (see specs#2900).

Here we:
- archive, for a given (closed) month, the JR1 report (TSV) and a per-article aggregate of the accesses (CSV), so that
  any report can be rebuilt later without the raw rows;
- purge the raw rows of already-archived months, folding them into Janeway's ``HistoricArticleAccess`` lifetime
  counters (as Janeway's ``accesses_to_historic`` does), so that per-article totals stay correct.

All months are UTC months: Janeway's ``get_press_totals`` buckets accesses by the UTC month of their timestamp, so
using month boundaries in any other timezone would misplace (or crash on) accesses near midnight of the first day.
"""

import csv
import dataclasses
import datetime
from pathlib import Path
from typing import List, Tuple

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from metrics import views as metrics_views
from metrics.models import ArticleAccess, HistoricArticleAccess

AGGREGATE_FIELDS = ("article__journal__code", "article_id", "type", "galley_type", "country__code")
AGGREGATE_HEADER = ("month", "journal", "article_id", "type", "galley_type", "country", "accesses")


def month_start(value: datetime.date) -> datetime.datetime:
    """Return the first instant (UTC) of the month of the given date."""
    return datetime.datetime(value.year, value.month, 1, tzinfo=datetime.timezone.utc)


def current_month_start() -> datetime.datetime:
    """Return the first instant (UTC) of the current month."""
    return month_start(timezone.now().astimezone(datetime.timezone.utc))


def month_label(value: datetime.date) -> str:
    """Return the YYYY-MM label of the month of the given date."""
    return value.strftime("%Y-%m")


def archive_paths(archive_dir: Path, month: datetime.date) -> Tuple[Path, Path]:
    """Return the paths of the JR1 report and of the aggregated accesses for the given month."""
    label = month_label(month)
    return archive_dir / f"JR1_{label}.tsv", archive_dir / f"article_accesses_{label}.csv"


@dataclasses.dataclass
class ArchiveCounterMonth:
    """Write the JR1 report and the aggregated accesses of one closed month into the archive directory."""

    month: datetime.date
    archive_dir: Path
    overwrite: bool = False

    @property
    def start(self) -> datetime.datetime:
        """Return the first instant of the month."""
        return month_start(self.month)

    @property
    def end(self) -> datetime.datetime:
        """Return the first instant of the following month."""
        return self.start + relativedelta(months=1)

    @property
    def paths(self) -> Tuple[Path, Path]:
        """Return the paths of the files written by this archiver."""
        return archive_paths(self.archive_dir, self.month)

    @property
    def is_archived(self) -> bool:
        """Tell if all the files for this month are already in the archive."""
        return all(path.exists() for path in self.paths)

    def _check_conditions(self):
        if self.start >= current_month_start():
            raise ValidationError(f"Month {month_label(self.month)} is not over yet: cannot archive it.")
        if self.is_archived and not self.overwrite:
            raise ValidationError(f"Month {month_label(self.month)} is already archived in {self.archive_dir}.")

    def _write_jr1(self, path: Path):
        # Janeway's jr_one looks for accesses in the closed range [start_date, end_date].
        # The request is used only by the XML rendering, not by the TSV one.
        end_date = self.end - datetime.timedelta(microseconds=1)
        response = metrics_views.jr_one(None, "tsv", self.start, end_date)
        path.write_bytes(response.content)

    def _write_aggregate(self, path: Path):
        rows = (
            ArticleAccess.objects.filter(accessed__gte=self.start, accessed__lt=self.end)
            .values(*AGGREGATE_FIELDS)
            .annotate(accesses=Count("id"))
            .order_by(*AGGREGATE_FIELDS)
        )
        label = month_label(self.month)
        with path.open("w", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(AGGREGATE_HEADER)
            for row in rows.iterator():
                writer.writerow([label, *(row[field] for field in AGGREGATE_FIELDS), row["accesses"]])

    def run(self) -> List[Path]:
        """Write the archive files and return their paths."""
        with transaction.atomic():
            self._check_conditions()
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            jr1_path, aggregate_path = self.paths
            # Write to temporary files first, so that an interrupted run doesn't leave a month half-archived (which
            # would then allow its rows to be purged).
            for path, writer in ((jr1_path, self._write_jr1), (aggregate_path, self._write_aggregate)):
                tmp_path = path.with_name(f".{path.name}.tmp")
                writer(tmp_path)
                tmp_path.replace(path)
            return [jr1_path, aggregate_path]


@dataclasses.dataclass
class PurgeArticleAccesses:
    """Delete the ArticleAccess rows older than ``cutoff``, folding them into HistoricArticleAccess.

    Rows are purged only if every month they belong to has been archived (see ArchiveCounterMonth).

    Each batch is folded and deleted in its own transaction: a single transaction over millions of rows would hold
    locks for a long time, while an interrupted run leaves the tables consistent and can simply be run again.
    """

    cutoff: datetime.datetime
    archive_dir: Path
    batch_size: int = 5000

    def _get_queryset(self):
        return ArticleAccess.objects.filter(accessed__lt=self.cutoff)

    def unarchived_months(self) -> List[datetime.date]:
        """Return the months with rows older than the cutoff and without archive files."""
        months = (
            self._get_queryset()
            .annotate(month=TruncMonth("accessed", tzinfo=datetime.timezone.utc))
            .values_list("month", flat=True)
            .distinct()
            .order_by("month")
        )
        return [
            month.date()
            for month in months
            if not all(path.exists() for path in archive_paths(self.archive_dir, month.date()))
        ]

    def _check_conditions(self):
        if missing := self.unarchived_months():
            labels = ", ".join(month_label(month) for month in missing)
            raise ValidationError(f"Refusing to purge accesses of months not archived in {self.archive_dir}: {labels}")

    def _purge_batch(self) -> int:
        with transaction.atomic():
            pks = list(self._get_queryset().order_by("pk").values_list("pk", flat=True)[: self.batch_size])
            if not pks:
                return 0
            batch = ArticleAccess.objects.filter(pk__in=pks)
            per_article = batch.values("article_id").annotate(
                views=Count("pk", filter=Q(type="view")),
                downloads=Count("pk", filter=Q(type="download")),
            )
            for counts in per_article:
                HistoricArticleAccess.objects.get_or_create(article_id=counts["article_id"])
                HistoricArticleAccess.objects.filter(article_id=counts["article_id"]).update(
                    views=F("views") + counts["views"],
                    downloads=F("downloads") + counts["downloads"],
                )
            batch.delete()
            return len(pks)

    def run(self) -> int:
        """Purge the rows and return how many have been deleted."""
        with transaction.atomic():
            self._check_conditions()
        deleted = 0
        while purged := self._purge_batch():
            deleted += purged
        return deleted
