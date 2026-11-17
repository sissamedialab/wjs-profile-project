"""
Report (and on request repair) articles damaged by overlapping step-8 submissions (specs#3233).

Two overlapping POSTs to the step-8 "Review & submit" view of a revision could:

- revert the article stage to "Under Revision" after the revision had been submitted and the review restarted;
- re-run the first-submission flow on the article, overwriting its submission date, moving its stage and sending
  the submission acknowledgements again.

Detection only uses database data (dates, stages, workflow states, stage log): message subjects come from
per-journal settings and are not reliable.
"""

import dataclasses
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Exists, F, Min, OuterRef, Q, QuerySet
from django.utils import timezone
from review.models import RevisionRequest
from submission.models import (
    STAGE_ASSIGNED,
    STAGE_UNDER_REVIEW,
    STAGE_UNDER_REVISION,
    STAGE_UNSUBMITTED,
    Article,
    ArticleStageLog,
)
from utils.logger import get_logger

from ...models import ArticleWorkflow

logger = get_logger(__name__)

RESUBMITTED = "resubmitted"
STAGE_INCONSISTENT = "stage_inconsistent"
STAGE_MANUAL = "stage_manual"

CONSISTENT_STAGES = (STAGE_ASSIGNED, STAGE_UNDER_REVIEW)


@dataclasses.dataclass(frozen=True)
class Problem:
    """A problem found on an article; ``proposed`` is the repair value, ``None`` when it cannot be repaired."""

    code: str
    description: str
    evidence: str
    field: str | None
    current: Any
    proposed: Any
    warnings: tuple[str, ...] = ()


def _display(value: Any) -> str:
    """Show datetimes in the local timezone, as the editorial office sees them."""
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return str(timezone.localtime(value))
    return str(value)


def _has_open_revision(article: Article) -> bool:
    return article.revisionrequest_set.filter(date_completed__isnull=True).exists()


def _workflow_state(article: Article) -> str | None:
    """Return the workflow state; legacy articles may have no ArticleWorkflow."""
    try:
        return article.articleworkflow.state
    except ArticleWorkflow.DoesNotExist:
        return None


def candidate_articles() -> QuerySet[Article]:
    """Return the articles that may have been damaged by a double submission; ``find_problems`` decides."""
    resubmitted = Q(date_submitted__gt=F("first_revision_requested"))
    stage_inconsistent = Q(articleworkflow__state=ArticleWorkflow.ReviewStates.EDITOR_SELECTED) & ~Q(
        stage__in=CONSISTENT_STAGES,
    )
    under_revision = Q(stage=STAGE_UNDER_REVISION)
    # An open revision request only rules out the stage problems: a re-submitted article may be revised again later
    open_revision = Exists(RevisionRequest.objects.filter(article=OuterRef("pk"), date_completed__isnull=True))
    return (
        Article.objects.annotate(first_revision_requested=Min("revisionrequest__date_requested"))
        .filter(first_revision_requested__isnull=False)
        .filter(resubmitted | ((stage_inconsistent | under_revision) & ~open_revision))
        .select_related("journal", "articleworkflow")
        .order_by("pk")
    )


def _resubmitted_problem(article: Article) -> Problem | None:
    """The first-submission flow was run again after a revision request: the submission date was overwritten."""
    first_request = article.revisionrequest_set.order_by("date_requested").first()
    if not first_request or not article.date_submitted or article.date_submitted <= first_request.date_requested:
        return None
    original = (
        ArticleStageLog.objects.filter(article=article, stage_from=STAGE_UNSUBMITTED).order_by("date_time").first()
    )
    warnings = [
        "the submission acknowledgements were sent again to the authors and co-authors",
        "the article was uploaded again to Prophy",
        "the EO may have been notified again of the access-mode special request",
    ]
    if article.date_published:
        warnings.append("the article is published: check by hand its public metadata (article page, JATS, Crossref)")
    if original:
        description = "re-submitted as a new article after a revision request: submission date overwritten"
        proposed = original.date_time
    else:
        description = "re-submitted as a new article, but no original submission in the stage log: manual fix"
        proposed = None
    return Problem(
        code=RESUBMITTED,
        description=description,
        evidence=f"submitted on {_display(article.date_submitted)}, after the first revision request of "
        f"{_display(first_request.date_requested)}",
        field="date_submitted",
        current=article.date_submitted,
        proposed=proposed,
        warnings=tuple(warnings),
    )


def _stage_problem(article: Article) -> Problem | None:
    """The stage does not match a workflow whose review restarted after the revision was submitted."""
    if _has_open_revision(article) or not article.revisionrequest_set.exists():
        return None
    state = _workflow_state(article)
    if state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED and article.stage not in CONSISTENT_STAGES:
        has_pending_reviews = ArticleWorkflow.objects.with_pending_reviews().filter(article=article).exists()
        return Problem(
            code=STAGE_INCONSISTENT,
            description="stage inconsistent with the workflow (revision submitted, review restarted)",
            evidence=f"stage {article.stage}, workflow {state}, no open revision request",
            field="stage",
            current=article.stage,
            proposed=STAGE_UNDER_REVIEW if has_pending_reviews else STAGE_ASSIGNED,
        )
    if state != ArticleWorkflow.ReviewStates.EDITOR_SELECTED and article.stage == STAGE_UNDER_REVISION:
        return Problem(
            code=STAGE_MANUAL,
            description="stage under revision without an open revision request: needs manual inspection",
            evidence=f"stage {article.stage}, workflow {state}, no open revision request",
            field="stage",
            current=article.stage,
            proposed=None,
        )
    return None


def find_problems(article: Article) -> list[Problem]:
    """Return the problems left on the article by a double submission."""
    return [problem for problem in (_resubmitted_problem(article), _stage_problem(article)) if problem]


class Command(BaseCommand):
    help = (  # noqa: A003
        "List the articles damaged by overlapping step-8 submissions (specs#3233), explaining for each the problem "
        "and the proposed fix. Nothing is changed unless the articles to repair are given with --repair."
    )

    def add_arguments(self, parser):
        """Add ``--repair``: without it the command only reports."""
        parser.add_argument(
            "--repair",
            nargs="+",
            type=int,
            metavar="ARTICLE_ID",
            help="Repair the given articles (only the problems with a proposed fix; nothing else is touched).",
        )

    def handle(self, *args, **options):
        """Report the broken articles, or repair the ones given with ``--repair``."""
        if options["repair"]:
            self._repair(options["repair"])
        else:
            self._report()

    def _lock_article(self, article_id: int) -> Article | None:
        """
        Lock the article and its workflow until the end of the transaction, and return the article read under lock.

        The workflow is locked first, as the wjs_review logic does before changing the article, to keep the same
        lock order. ``of=("self",)`` is required: the workflow is joined with a nullable outer join, which cannot be
        locked. Must be called inside a transaction.
        """
        list(ArticleWorkflow.objects.select_for_update().filter(article_id=article_id).values_list("pk", flat=True))
        return (
            Article.objects.select_for_update(of=("self",))
            .select_related("journal", "articleworkflow")
            .filter(pk=article_id)
            .first()
        )

    def _repair(self, article_ids: list[int]):
        """
        Repair the given articles, each in its own transaction, re-checking first that it is still broken.

        The article and its workflow are locked while the fix is computed and written, so a concurrent change waits
        for the repair instead of being overwritten with a fix computed on stale data.

        Raises:
          CommandError: listing the ids that were not repaired (not found, not broken, nothing repairable).

        """
        skipped = []
        for article_id in article_ids:
            with transaction.atomic():
                article = self._lock_article(article_id)
                if article is None:
                    self.stdout.write(f"Article {article_id}: skipped, not found")
                    skipped.append(article_id)
                    continue
                # Detection runs again: an article is repaired only if it is still broken
                problems = find_problems(article)
                repairable = [problem for problem in problems if problem.proposed is not None]
                if not problems:
                    self.stdout.write(f"Article {article_id}: skipped, not detected as broken")
                    skipped.append(article_id)
                    continue
                if not repairable:
                    self.stdout.write(f"Article {article_id}: skipped, nothing can be repaired automatically")
                    skipped.append(article_id)
                    continue
                for problem in repairable:
                    # Plain field update: no events, no workflow transitions, no stage log entry
                    Article.objects.filter(pk=article.pk).update(**{problem.field: problem.proposed})
                    change = f"{problem.field}: {_display(problem.current)} -> {_display(problem.proposed)}"
                    self.stdout.write(f"Article {article_id}: [{problem.code}] {change}")
                    logger.info(f"report_double_submissions: repaired article {article_id} {change}")
        if skipped:
            raise CommandError(f"Articles not repaired: {', '.join(str(pk) for pk in skipped)}")

    def _write_article(self, article: Article, problems: list[Problem]):
        """Write an article, its problems, the proposed fixes and the warnings."""
        self.stdout.write(
            f"Article {article.pk} ({article.journal.code}) - {article.title}\n"
            f"  stage: {article.stage}, workflow: {_workflow_state(article) or 'none'}",
        )
        for problem in problems:
            self.stdout.write(f"  [{problem.code}] {problem.description}")
            self.stdout.write(f"    evidence: {problem.evidence}")
            if problem.proposed is None:
                self.stdout.write(f"    fix: none proposed, {problem.field} must be checked by hand")
            else:
                self.stdout.write(
                    f"    fix: {problem.field}: {_display(problem.current)} -> {_display(problem.proposed)}",
                )
            for warning in problem.warnings:
                self.stdout.write(f"    warning: {warning}")

    def _report(self):
        """List every broken article and end with the ``--repair`` command line for the repairable ones."""
        repairable = []
        broken = 0
        for article in candidate_articles():
            if problems := find_problems(article):
                broken += 1
                self._write_article(article, problems)
                if any(problem.proposed is not None for problem in problems):
                    repairable.append(article.pk)
        self.stdout.write(f"{broken} broken article(s) found.")
        if repairable:
            ids = " ".join(str(pk) for pk in repairable)
            self.stdout.write(f"To repair: python manage.py report_double_submissions --repair {ids}")
