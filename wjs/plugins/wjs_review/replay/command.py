"""Replay the automatic editor assignment of historical articles and report original vs new editor."""

import csv
from collections import Counter
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from journal.models import Journal

from ..events.assignment import SharedKeyword
from .logic import ReplayAutomaticAssignment, ReplayReportRow, get_replayable_articles

REPORT_FIELDS = [
    "article_id",
    "title",
    "journal",
    "date_submitted",
    "original_editor",
    "new_editor",
    "match",
    "matched_by_keyword",
    "matching_keywords_count",
    "matching_keywords",
    "max_matching_keyword_weight",
    "keyword_score",
    "monthly_workload",
    "workload_30d",
    "workload_cap",
]

CANDIDATE_FIELDS = [
    "article_id",
    "editor",
    "rank",
    "selected",
    "reason",
    "keyword_score",
    "matching_keywords_count",
    "matching_keywords",
    "monthly_workload",
    "workload_30d",
    "workload_cap",
]

NULL_ARTICLE_WEIGHT = "—"


def _account_label(account) -> str:
    """Format an account as "Full Name <email>", or the empty string when there is none."""
    return f"{account.full_name()} <{account.email}>" if account else ""


def _format_score(score: float | None) -> str:
    """Format a keyword score with three decimals, or the empty string when there is none."""
    return f"{score:.3f}" if score is not None else ""


def _blank_if_none(value) -> str | int:
    """Return the value, or the empty string for None."""
    return "" if value is None else value


def format_shared_keywords(shared_keywords: list[SharedKeyword]) -> str:
    """Format shared keywords as "<word> (article <w_A>, editor <w_E>)", "; "-separated."""
    return "; ".join(
        f"{keyword.word} (article "
        f"{NULL_ARTICLE_WEIGHT if keyword.article_weight is None else keyword.article_weight}, "
        f"editor {keyword.editor_weight})"
        for keyword in shared_keywords
    )


def report_row_to_dict(row: ReplayReportRow) -> dict:
    """Flatten a replay report row into a CSV record."""
    audit = row.audit
    return {
        "article_id": row.article.pk,
        "title": row.article.title,
        "journal": row.article.journal.code,
        "date_submitted": row.article.date_submitted.isoformat(),
        "original_editor": _account_label(row.original_editor),
        "new_editor": _account_label(row.new_editor),
        "match": row.match,
        "matched_by_keyword": audit.matched_by_keyword if audit else "",
        "matching_keywords_count": audit.matching_keywords_count if audit else "",
        "matching_keywords": format_shared_keywords(row.shared_keywords),
        "max_matching_keyword_weight": audit.max_matching_keyword_weight if audit else "",
        "keyword_score": _format_score(audit.keyword_score) if audit else "",
        "monthly_workload": audit.workload_available if audit else "",
        "workload_30d": audit.workload_available_30d if audit else "",
        "workload_cap": audit.workload_cap if audit else "",
    }


def candidates_to_dicts(row: ReplayReportRow) -> list[dict]:
    """Flatten a replay report row's candidates into CSV records: selected first, then in explanation order."""
    return [
        {
            "article_id": row.article.pk,
            "editor": _account_label(candidate.editor),
            "rank": _blank_if_none(candidate.rank),
            "selected": candidate.selected,
            "reason": candidate.reason,
            "keyword_score": _format_score(candidate.keyword_score),
            "matching_keywords_count": candidate.matching_keywords_count,
            "matching_keywords": format_shared_keywords(candidate.shared_keywords),
            "monthly_workload": _blank_if_none(candidate.monthly_workload),
            "workload_30d": _blank_if_none(candidate.workload_30d),
            "workload_cap": _blank_if_none(candidate.workload_cap),
        }
        # Stable sort: keeps the explanation's rank / excluded-by-score order behind the selected editor.
        for candidate in sorted(row.candidates, key=lambda candidate: not candidate.selected)
    ]


def default_candidates_file(output_file: str) -> str:
    """Derive the candidates CSV path from the main report path."""
    stem = output_file[: -len(".csv")] if output_file.endswith(".csv") else output_file
    return f"{stem}_candidates.csv"


class Command(BaseCommand):
    """Replay the automatic editor assignment of historical articles and write a CSV report."""

    help = (  # noqa: A003
        "DESTRUCTIVE - run only on an imported copy of production, with outgoing email and the Q-cluster/cron "
        "(reminders) disabled. Reset the selected articles and replay their automatic editor assignment in "
        "submission order, writing an original-vs-new CSV report plus a candidates CSV (--candidates-file, default "
        "<output>_candidates.csv) explaining, for the default keyword algorithm, why each editor sharing a keyword "
        "with the article won or lost. Skipped articles are not written to either CSV: they are listed on stdout "
        "with their reason. Uses today's workload/role/keyword settings, "
        "not the historical ones; on the special-issue and JCOM paths open articles only accumulate across the "
        "replayed batch, while on the default path workload windows count assignments by date and the WITHDRAWN "
        "exclusion uses the article's current state. The batch runs in one transaction: any unexpected error rolls "
        "back the whole batch and no CSV is written."
    )

    def add_arguments(self, parser) -> None:
        """Declare the command's CLI arguments."""
        parser.add_argument("--journal", required=True, help="Journal code")
        parser.add_argument("--date-from", type=date.fromisoformat, help="First submission date (YYYY-MM-DD)")
        parser.add_argument("--date-to", type=date.fromisoformat, help="Last submission date (YYYY-MM-DD)")
        parser.add_argument("--article-ids", type=int, nargs="+", help="Explicit article ids to replay")
        parser.add_argument("--output-file", help="CSV report path (default: timestamped file in cwd)")
        parser.add_argument(
            "--candidates-file",
            help="Candidates CSV path (default: the report path with .csv replaced by _candidates.csv)",
        )
        parser.add_argument(
            "--confirm-copy-instance",
            action="store_true",
            help="Confirm this is a disposable copy of production: articles WILL be reset",
        )

    def handle(self, *args, **options) -> None:
        """Validate the selection, replay the batch, and write the CSV report."""
        if not options["confirm_copy_instance"]:
            raise CommandError("Refusing to run: pass --confirm-copy-instance (copy instances only).")
        if not options["article_ids"] and not (options["date_from"] and options["date_to"]):
            raise CommandError("Pass either --article-ids or both --date-from and --date-to.")
        try:
            journal = Journal.objects.get(code=options["journal"])
        except Journal.DoesNotExist as e:
            raise CommandError(f"Journal '{options['journal']}' does not exist") from e

        articles = get_replayable_articles(
            journal,
            date_from=options["date_from"],
            date_to=options["date_to"],
            article_ids=options["article_ids"],
        )
        rows = ReplayAutomaticAssignment(articles=articles).run()

        output_file = options["output_file"] or f"replay_automatic_assignment_{timezone.now():%Y%m%d_%H%M%S}.csv"
        candidates_file = options["candidates_file"] or default_candidates_file(output_file)
        replayed = [row for row in rows if not row.skipped_reason]
        with open(output_file, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(report_row_to_dict(row) for row in replayed)
        with open(candidates_file, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CANDIDATE_FIELDS)
            writer.writeheader()
            for row in replayed:
                writer.writerows(candidates_to_dicts(row))

        outcome = Counter(
            "skipped" if row.skipped_reason else "unassigned" if row.new_editor is None else row.match for row in rows
        )
        self.stdout.write(f"Processed: {len(rows)}")
        self.stdout.write(f"Matching original editor: {outcome[True]}")
        self.stdout.write(f"Different editor: {outcome[False]}")
        self.stdout.write(f"No editor found: {outcome['unassigned']}")
        self.stdout.write(f"Skipped: {outcome['skipped']}")
        self.stdout.write(f"Report written to {output_file}")
        self.stdout.write(f"Candidates written to {candidates_file}")
        for row in rows:
            if row.skipped_reason:
                self.stdout.write(f"Skipped {row.article.pk}: {row.skipped_reason}")
