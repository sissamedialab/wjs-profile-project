"""
Historical replay of the automatic editor assignment.

Only meant for an imported copy of the production database: resetting articles is destructive by design.
See docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md.
"""

import dataclasses
from datetime import date

import freezegun
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, QuerySet
from django.test import override_settings
from django.utils.translation import gettext_lazy as _
from journal.models import Journal
from review.models import EditorAssignment, ReviewAssignment, ReviewRound
from submission.models import STAGE_UNASSIGNED, Article
from typesetting.models import TypesettingAssignment

from wjs.jcom_profile.models import StaffWorkloadParameters
from wjs.jcom_profile.utils import create_rich_fake_request

from ..communication_utils import get_system_user
from ..events.assignment import (
    ReplayCandidate,
    SharedKeyword,
    default_assign_editors_to_articles,
    dispatch_assignment,
    explain_default_assignment_candidates,
    get_assignment_function,
    get_shared_keywords,
    is_special_issue,
)
from ..models import (
    ArticleWorkflow,
    AutomaticAssignmentAudit,
    EditorDecision,
    EditorRevisionRequest,
    PastEditorAssignment,
    WjsEditorAssignment,
)

Account = get_user_model()


def get_original_editor(article: Article) -> Account | None:
    """
    Return the first editor ever assigned to the article.

    That is the editor the automatic assignment (or the director, if it found nobody) originally picked;
    later reassignments are not what the replay is compared against. Reassigned editors live in
    PastEditorAssignment, the current one in WjsEditorAssignment: the earliest across both wins.
    """
    candidates = []
    past = PastEditorAssignment.objects.filter(article=article).order_by("date_assigned", "pk").first()
    if past:
        candidates.append((past.date_assigned, past.editor))
    current = WjsEditorAssignment.objects.filter(article=article).order_by("assigned", "pk").first()
    if current:
        candidates.append((current.assigned, current.editor))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]


def get_replayable_articles(
    journal: Journal,
    date_from: date | None = None,
    date_to: date | None = None,
    article_ids: list[int] | None = None,
) -> QuerySet[Article]:
    """Return the journal's submitted articles to replay, oldest submission first."""
    articles = Article.objects.filter(
        journal=journal, date_submitted__isnull=False, articleworkflow__isnull=False
    ).exclude(articleworkflow__state=ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION)
    if article_ids is not None:
        articles = articles.filter(pk__in=article_ids)
    if date_from:
        articles = articles.filter(date_submitted__date__gte=date_from)
    if date_to:
        articles = articles.filter(date_submitted__date__lte=date_to)
    return articles.order_by("date_submitted", "pk")


@dataclasses.dataclass
class ResetArticleForReplay:
    """Reset an article to "just submitted, never processed", destroying its editorial history."""

    article: Article

    def _check_conditions(self) -> None:
        if TypesettingAssignment.objects.filter(round__article=self.article).exists():
            raise ValidationError(_("Article reached typesetting: typesetting reset is not supported."))
        # A live assignment always happens after submission processing, never at the exact submission instant:
        # an assignment dated exactly at date_submitted is the replay's fingerprint. Replaying again would
        # report the previous replay's pick as the original editor, since the historical one is gone.
        if WjsEditorAssignment.objects.filter(article=self.article, assigned=self.article.date_submitted).exists():
            raise ValidationError(
                _("Article was already replayed on this instance: re-import the database to replay it again.")
            )

    def run(self) -> None:
        """Delete assignments, review rounds and decisions, and rewind the workflow to SUBMITTED."""
        with transaction.atomic():
            self._check_conditions()
            # Order matters: revision requests PROTECT their decision, and both PROTECT their review round.
            EditorRevisionRequest.objects.filter(article=self.article).delete()
            EditorDecision.objects.filter(workflow__article=self.article).delete()
            # ReviewAssignment.review_round is SET_NULL, so review assignments must be deleted explicitly.
            ReviewAssignment.objects.filter(article=self.article).delete()
            ReviewRound.objects.filter(article=self.article).delete()
            # Deleting the Janeway parent rows removes the WjsEditorAssignment children too.
            EditorAssignment.objects.filter(article=self.article).delete()
            Article.objects.filter(pk=self.article.pk).update(stage=STAGE_UNASSIGNED)
            ArticleWorkflow.objects.filter(article=self.article).update(state=ArticleWorkflow.ReviewStates.SUBMITTED)
            self.article.refresh_from_db()


@dataclasses.dataclass
class ReplayReportRow:
    """Outcome of replaying one article."""

    article: Article
    original_editor: Account | None
    new_editor: Account | None = None
    audit: AutomaticAssignmentAudit | None = None
    skipped_reason: str = ""
    # The winner's shared keywords, on the default keyword path only.
    shared_keywords: list[SharedKeyword] = dataclasses.field(default_factory=list)
    # Every editor sharing a keyword, on the default non-special-issue path only.
    candidates: list[ReplayCandidate] = dataclasses.field(default_factory=list)

    @property
    def match(self) -> bool:
        """Tell whether the replay picked the historical editor."""
        return self.new_editor is not None and self.new_editor == self.original_editor


@dataclasses.dataclass
class ReplayAutomaticAssignment:
    """
    Replay the automatic assignment of a batch of articles, in submission order.

    The whole batch is snapshotted and reset before any article is replayed: otherwise the historical
    assignments of not-yet-replayed articles would still count against their editors' workload.
    Each article is then replayed with "now" frozen at its submission date, so both the monthly window and
    the new assignment's date match what happened historically, and workload accumulates across the batch.

    Only meant for an imported copy of production, with outgoing email and the Q-cluster/cron (reminders)
    disabled: the replay creates reminders dated at historical submission times and leaves any pre-existing
    ones for the same article orphaned.

    Known limitations: replayed articles stay ``EDITOR_SELECTED`` for the rest of the batch, so the
    open-articles metric (the special-issue and JCOM paths only) only ever grows across the replay —
    historically those articles were eventually decided and freed capacity back up, and that metric also sees
    today's states of articles outside the batch. On the default path the monthly/30-day windows count
    assignments by date, and the WITHDRAWN exclusion uses the article's current state. The replay also uses
    today's ``StaffWorkloadParameters`` (workload, brake_on, enabled, vacancies), today's role memberships
    and today's editor keywords, not the historical ones in effect at submission time.
    """

    articles: QuerySet[Article]

    def _reset_all(self, articles: list[Article]) -> tuple[list[ReplayReportRow], list[ReplayReportRow]]:
        replayable, skipped = [], []
        for article in articles:
            original_editor = get_original_editor(article)
            row = ReplayReportRow(article=article, original_editor=original_editor)
            if original_editor is None:
                # Nothing to compare the replay against, and nothing to reset either: leave the article
                # untouched instead of resetting/replaying it (which would also let it be re-run forever,
                # since ResetArticleForReplay's already-replayed fingerprint never applies to it).
                row.skipped_reason = str(
                    _("Article never had an editor assigned historically: nothing to compare against.")
                )
                skipped.append(row)
                continue
            try:
                ResetArticleForReplay(article=article).run()
            except ValidationError as e:
                row.skipped_reason = " ".join(e.messages)
                skipped.append(row)
            else:
                replayable.append(row)
        return replayable, skipped

    def _replay(self, row: ReplayReportRow) -> None:
        article = row.article
        # The replay's audit row is dated at date_submitted, so live audit rows (written later) would outrank it
        # in latest_for_article: identify it as the one created after this point instead.
        last_audit_pk = AutomaticAssignmentAudit.objects.filter(article=article).aggregate(last=Max("pk"))["last"] or 0
        with freezegun.freeze_time(article.date_submitted):
            create_rich_fake_request(article.journal, settings, user=get_system_user(article.journal))
            # Right before dispatch, under the same frozen time: the explanation sees what the algorithm sees.
            if get_assignment_function(article.journal) is default_assign_editors_to_articles and not (
                is_special_issue(article)
            ):
                row.candidates = explain_default_assignment_candidates(article)
            assignment = dispatch_assignment(article=article)
            # Mirror models.process_submission, which moves the workflow once assignment is attempted.
            ArticleWorkflow.objects.filter(article=article).update(
                state=(
                    ArticleWorkflow.ReviewStates.EDITOR_SELECTED
                    if assignment
                    else ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
                )
            )
        if assignment:
            row.new_editor = assignment.editor
            row.audit = (
                AutomaticAssignmentAudit.objects.filter(article=article, pk__gt=last_audit_pk).order_by("-pk").first()
            )
            if row.audit and row.audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT:
                winner_parameters = StaffWorkloadParameters.objects.filter(
                    user=row.new_editor, journal=article.journal
                )
                row.shared_keywords = next(iter(get_shared_keywords(article, winner_parameters).values()), [])
        for candidate in row.candidates:
            candidate.selected = candidate.editor == row.new_editor

    def run(self) -> list[ReplayReportRow]:
        """Reset and replay the batch; return one report row per article, in submission order."""
        articles = list(self.articles.order_by("date_submitted", "pk"))
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            with transaction.atomic():
                replayable, skipped = self._reset_all(articles)
                for row in replayable:
                    self._replay(row)
        order = {article.pk: index for index, article in enumerate(articles)}
        return sorted(replayable + skipped, key=lambda row: order[row.article.pk])
