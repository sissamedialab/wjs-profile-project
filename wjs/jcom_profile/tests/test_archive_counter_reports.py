"""Test the command that archives COUNTER reports and purges old article accesses."""

import csv
import datetime

import pytest
from dateutil.relativedelta import relativedelta
from django.core import management
from django.core.cache import cache as django_cache
from django.core.management.base import CommandError
from metrics.models import ArticleAccess, HistoricArticleAccess

from wjs.jcom_profile.counter_archive import (
    archive_paths,
    current_month_start,
    month_label,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the cache, since Janeway's get_press_totals caches its results by date range."""
    django_cache.clear()


def _months_ago(months: int) -> datetime.datetime:
    return current_month_start() - relativedelta(months=months)


def _access(article, when: datetime.datetime, access_type: str = "view", galley_type: str = "pdf") -> ArticleAccess:
    return ArticleAccess.objects.create(
        article=article,
        type=access_type,
        identifier="some-session-id",
        accessed=when,
        galley_type=galley_type,
    )


def _read_aggregate(path):
    with path.open() as csv_file:
        return list(csv.DictReader(csv_file))


@pytest.mark.django_db
def test_archive_previous_month(tmp_path, article):
    """By default, the month just ended is archived, and only its accesses are counted."""
    last_month = _months_ago(1)
    _access(article, last_month)
    _access(article, last_month + datetime.timedelta(days=3))
    _access(article, last_month + datetime.timedelta(days=5), access_type="download")
    # Accesses of other months must not be counted
    _access(article, last_month - datetime.timedelta(microseconds=1))
    _access(article, current_month_start())

    management.call_command("archive_counter_reports", f"--archive-dir={tmp_path}", "--no-purge")

    jr1_path, aggregate_path = archive_paths(tmp_path, last_month.date())
    assert jr1_path.exists(), f"JR1 report {jr1_path} not written"
    jr1_rows = list(csv.reader(jr1_path.open(), delimiter="\t"))
    journal_row = next(row for row in jr1_rows if row and row[0] == article.journal.name)
    # Reporting Period Total / HTML (i.e. views) / PDF (i.e. downloads)
    assert journal_row[7:10] == ["3", "2", "1"], f"Unexpected JR1 totals in {journal_row}"

    rows = _read_aggregate(aggregate_path)
    counts = {row["type"]: row for row in rows}
    assert len(rows) == 2, f"Expected one row per access type, got {rows}"
    assert counts["view"]["accesses"] == "2", f"Unexpected views row {counts['view']}"
    assert counts["download"]["accesses"] == "1", f"Unexpected downloads row {counts['download']}"
    assert counts["view"]["month"] == month_label(last_month), f"Unexpected month in {counts['view']}"
    assert counts["view"]["journal"] == article.journal.code, f"Unexpected journal in {counts['view']}"
    assert counts["view"]["article_id"] == str(article.pk), f"Unexpected article in {counts['view']}"

    assert ArticleAccess.objects.count() == 5, "No access should be purged with --no-purge"


@pytest.mark.django_db
def test_archived_months_are_skipped(tmp_path, article):
    """Re-running the command does not overwrite already archived months."""
    last_month = _months_ago(1)
    _access(article, last_month)
    management.call_command("archive_counter_reports", f"--archive-dir={tmp_path}", "--no-purge")
    __, aggregate_path = archive_paths(tmp_path, last_month.date())
    aggregate_path.write_text("untouched")

    management.call_command("archive_counter_reports", f"--archive-dir={tmp_path}", "--no-purge")

    assert aggregate_path.read_text() == "untouched", "An already archived month has been overwritten"


@pytest.mark.django_db
def test_cannot_archive_current_month(tmp_path, article):
    """The current month is not over, so it cannot be archived."""
    with pytest.raises(CommandError, match="not over yet"):
        management.call_command(
            "archive_counter_reports",
            f"--archive-dir={tmp_path}",
            f"--month={month_label(current_month_start())}",
            "--no-purge",
        )
    assert not list(tmp_path.iterdir()), "Nothing should be archived"


@pytest.mark.django_db
def test_archive_since_and_purge(tmp_path, article):
    """Archive a range of months and purge the accesses older than the retention period into historic counters."""
    old_view = _access(article, _months_ago(3))
    old_download = _access(article, _months_ago(3), access_type="download")
    kept = [_access(article, _months_ago(1)), _access(article, current_month_start())]

    management.call_command(
        "archive_counter_reports",
        f"--archive-dir={tmp_path}",
        f"--since={month_label(_months_ago(3))}",
        "--retention-months=1",
    )

    for months in (3, 2, 1):
        for path in archive_paths(tmp_path, _months_ago(months).date()):
            assert path.exists(), f"Archive file {path} not written"
    remaining = set(ArticleAccess.objects.values_list("pk", flat=True))
    assert remaining == {access.pk for access in kept}, "Only accesses within the retention period should remain"
    assert not remaining & {old_view.pk, old_download.pk}, "Old accesses should be purged"
    historic = HistoricArticleAccess.objects.get(article=article)
    assert (historic.views, historic.downloads) == (1, 1), "Purged accesses should be folded into historic counters"


@pytest.mark.django_db
def test_purge_adds_to_existing_historic_counters(tmp_path, article):
    """Purged accesses are added to the existing historic counters."""
    HistoricArticleAccess.objects.create(article=article, views=10, downloads=20)
    _access(article, _months_ago(1))

    management.call_command("archive_counter_reports", f"--archive-dir={tmp_path}", "--retention-months=0")

    historic = HistoricArticleAccess.objects.get(article=article)
    assert (historic.views, historic.downloads) == (11, 20), "Historic counters should be incremented"
    assert not ArticleAccess.objects.exists(), "All accesses before the current month should be purged"


@pytest.mark.django_db
def test_purge_refuses_unarchived_months(tmp_path, article):
    """Accesses of months that have not been archived are never purged."""
    _access(article, _months_ago(3))
    _access(article, _months_ago(1))

    with pytest.raises(CommandError, match=month_label(_months_ago(3))):
        management.call_command("archive_counter_reports", f"--archive-dir={tmp_path}", "--retention-months=0")

    assert ArticleAccess.objects.count() == 2, "No access should be purged"
    assert not HistoricArticleAccess.objects.filter(article=article, views__gt=0).exists(), "No access folded"
