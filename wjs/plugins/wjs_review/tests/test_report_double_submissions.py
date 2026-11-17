"""Tests for the report_double_submissions management command (specs#3233)."""

import datetime
import threading
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone
from review.models import RevisionRequest
from submission.models import (
    STAGE_ASSIGNED,
    STAGE_UNASSIGNED,
    STAGE_UNDER_REVIEW,
    STAGE_UNDER_REVISION,
    STAGE_UNSUBMITTED,
    Article,
    ArticleStageLog,
)

from ..logic import CreateReviewRound
from ..management.commands import report_double_submissions
from ..management.commands.report_double_submissions import (
    candidate_articles,
    find_problems,
)
from ..models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from ..plugin_settings import STAGE as WJS_REVIEW_STAGE

ORIGINAL_SUBMISSION = timezone.make_aware(datetime.datetime(2026, 2, 11, 13, 44, 21))


def _complete_revision(
    revision: EditorRevisionRequest, workflow_state: str = ArticleWorkflow.ReviewStates.EDITOR_SELECTED
):
    """Mark the revision as submitted and move the workflow on, without going through the submission logic."""
    revision.date_completed = timezone.now()
    revision.save()
    ArticleWorkflow.objects.filter(article=revision.article).update(state=workflow_state)


def _resubmitted(revision: EditorRevisionRequest, stage: str = STAGE_ASSIGNED, with_stage_log: bool = True) -> Article:
    """Reproduce the data left by a double submission that re-ran the first-submission flow on a revision."""
    article = revision.article
    _complete_revision(revision)
    ArticleStageLog.objects.filter(article=article).delete()
    if with_stage_log:
        ArticleStageLog.objects.create(
            article=article,
            stage_from=STAGE_UNSUBMITTED,
            stage_to=STAGE_UNASSIGNED,
            date_time=ORIGINAL_SUBMISSION,
        )
    Article.objects.filter(pk=article.pk).update(
        date_submitted=revision.date_requested + datetime.timedelta(hours=1),
        stage=stage,
    )
    article.refresh_from_db()
    return article


def _stage_reverted(
    revision: EditorRevisionRequest, workflow_state: str = ArticleWorkflow.ReviewStates.EDITOR_SELECTED
):
    """Reproduce the data left by a double submission that reverted the stage to "Under Revision"."""
    _complete_revision(revision, workflow_state)
    Article.objects.filter(pk=revision.article.pk).update(stage=STAGE_UNDER_REVISION)
    revision.article.refresh_from_db()
    return revision.article


def _codes(article: Article) -> list[str]:
    return [problem.code for problem in find_problems(article)]


def _article_data(article: Article) -> tuple:
    article = Article.objects.get(pk=article.pk)
    return article.stage, article.date_submitted


@pytest.mark.django_db
def test_resubmitted_detected(editor_revision: EditorRevisionRequest):
    """A submission date later than the first revision request is restored from the original submission."""
    article = _resubmitted(editor_revision)

    problems = find_problems(article)

    assert [problem.code for problem in problems] == ["resubmitted"]
    problem = problems[0]
    assert problem.field == "date_submitted"
    assert problem.current == article.date_submitted
    assert problem.proposed == ORIGINAL_SUBMISSION
    assert any("acknowledgement" in warning for warning in problem.warnings)
    assert not any("published" in warning for warning in problem.warnings)
    assert article in candidate_articles()


@pytest.mark.django_db
def test_resubmitted_without_stage_log_is_not_repairable(editor_revision: EditorRevisionRequest):
    """Without the original submission in the stage log there is no reliable date: manual fix only."""
    article = _resubmitted(editor_revision, with_stage_log=False)

    (problem,) = find_problems(article)

    assert problem.code == "resubmitted"
    assert problem.proposed is None
    assert "manual" in problem.description


@pytest.mark.django_db
def test_resubmitted_published_article_warns_about_metadata(editor_revision: EditorRevisionRequest):
    article = _resubmitted(editor_revision)
    Article.objects.filter(pk=article.pk).update(date_published=timezone.now())
    article.refresh_from_db()

    (problem,) = find_problems(article)

    assert any("published" in warning for warning in problem.warnings)


@pytest.mark.django_db
def test_stage_reverted_detected(editor_revision: EditorRevisionRequest):
    """An "Under Revision" stage with the revision completed and the review restarted goes back to "Assigned"."""
    article = _stage_reverted(editor_revision)

    problems = find_problems(article)

    assert [problem.code for problem in problems] == ["stage_inconsistent"]
    assert problems[0].field == "stage"
    assert problems[0].current == STAGE_UNDER_REVISION
    assert problems[0].proposed == STAGE_ASSIGNED
    assert article in candidate_articles()


@pytest.mark.django_db
def test_stage_reverted_with_pending_reviews_goes_under_review(editor_revision: EditorRevisionRequest, reviewer):
    """With a pending review in the latest round, the right stage is "Under Review"."""
    article = editor_revision.article
    _complete_revision(editor_revision)
    editor = WjsEditorAssignment.objects.get_current(article).editor
    CreateReviewRound(assignment=WjsEditorAssignment.objects.get_current(article)).run()
    WorkflowReviewAssignment.objects.create(
        article=article,
        reviewer=reviewer.janeway_account,
        editor=editor,
        review_round=article.current_review_round_object(),
        date_due=timezone.localdate(),
    )
    Article.objects.filter(pk=article.pk).update(stage=STAGE_UNDER_REVISION)
    article.refresh_from_db()

    (problem,) = find_problems(article)

    assert problem.code == "stage_inconsistent"
    assert problem.proposed == STAGE_UNDER_REVIEW


@pytest.mark.django_db
def test_stage_left_by_resubmission_detected(editor_revision: EditorRevisionRequest):
    """The stage left by a re-run first submission is inconsistent too, and both problems are reported."""
    article = _resubmitted(editor_revision, stage=WJS_REVIEW_STAGE)

    assert sorted(_codes(article)) == ["resubmitted", "stage_inconsistent"]


@pytest.mark.django_db
def test_stage_reverted_other_workflow_state_needs_manual_inspection(editor_revision: EditorRevisionRequest):
    article = _stage_reverted(editor_revision, workflow_state=ArticleWorkflow.ReviewStates.TO_BE_REVISED)

    (problem,) = find_problems(article)

    assert problem.code == "stage_manual"
    assert problem.proposed is None


@pytest.mark.django_db
def test_genuine_revision_is_not_broken(editor_revision: EditorRevisionRequest):
    """An article waiting for the author's revision (open revision request) is fine."""
    assert find_problems(editor_revision.article) == []
    assert editor_revision.article not in candidate_articles()


@pytest.mark.django_db
def test_assigned_article_is_not_broken(assigned_article: Article):
    assert find_problems(assigned_article) == []
    assert assigned_article not in candidate_articles()


@pytest.mark.django_db
def test_report_mode_writes_nothing(editor_revision: EditorRevisionRequest):
    """Without arguments the command only reports: problems, proposed changes and the --repair command line."""
    article = _resubmitted(editor_revision, stage=STAGE_UNDER_REVISION)
    before = _article_data(article)
    out = StringIO()

    call_command("report_double_submissions", stdout=out)

    output = out.getvalue()
    assert f"Article {article.pk} ({article.journal.code})" in output
    assert "resubmitted" in output
    assert "stage_inconsistent" in output
    assert str(ORIGINAL_SUBMISSION) in output
    assert f"{STAGE_UNDER_REVISION} -> {STAGE_ASSIGNED}" in output
    assert f"--repair {article.pk}" in output
    assert _article_data(article) == before


def _counts(article: Article) -> tuple[int, int]:
    return (
        Message.objects.filter(object_id=article.pk).count(),
        ArticleStageLog.objects.filter(article=article).count(),
    )


@pytest.mark.django_db
def test_repair_fixes_the_article(editor_revision: EditorRevisionRequest):
    """--repair restores the submission date and the stage, printing every change; then nothing is left to repair."""
    article = _resubmitted(editor_revision, stage=STAGE_UNDER_REVISION)
    counts = _counts(article)
    out = StringIO()

    call_command("report_double_submissions", "--repair", str(article.pk), stdout=out)

    article.refresh_from_db()
    assert article.date_submitted == ORIGINAL_SUBMISSION
    assert article.stage == STAGE_ASSIGNED
    output = out.getvalue()
    assert f"{STAGE_UNDER_REVISION} -> {STAGE_ASSIGNED}" in output
    assert str(ORIGINAL_SUBMISSION) in output
    # plain field update: no messages, no stage log entries
    assert _counts(article) == counts
    assert find_problems(article) == []
    assert article not in candidate_articles()


@pytest.mark.django_db
def test_repair_only_touches_the_given_articles(editor_revision: EditorRevisionRequest):
    article = _resubmitted(editor_revision)
    other = Article.objects.create(journal=article.journal, title="Other", stage=STAGE_UNDER_REVISION)
    RevisionRequest.objects.create(
        article=other,
        type="minor_revisions",
        date_due=timezone.localdate(),
        date_completed=timezone.now(),
    )
    ArticleWorkflow.objects.filter(article=other).update(state=ArticleWorkflow.ReviewStates.EDITOR_SELECTED)
    other = Article.objects.get(pk=other.pk)
    assert _codes(other) == ["stage_inconsistent"]
    other_before = _article_data(other)

    call_command("report_double_submissions", "--repair", str(article.pk), stdout=StringIO())

    assert find_problems(Article.objects.get(pk=article.pk)) == []
    assert _article_data(other) == other_before


@pytest.mark.django_db
def test_repair_skips_articles_not_repairable(editor_revision: EditorRevisionRequest, assigned_article: Article):
    """Not broken, missing and not repairable ids are skipped with a reason; the others are still repaired."""
    broken = _resubmitted(editor_revision)
    missing_id = Article.objects.order_by("-pk").first().pk + 1000
    out = StringIO()

    with pytest.raises(CommandError) as error:
        call_command(
            "report_double_submissions",
            "--repair",
            str(assigned_article.pk),
            str(missing_id),
            str(broken.pk),
            stdout=out,
        )

    broken.refresh_from_db()
    assert broken.date_submitted == ORIGINAL_SUBMISSION
    assert str(assigned_article.pk) in str(error.value)
    assert str(missing_id) in str(error.value)
    output = out.getvalue()
    assert f"Article {assigned_article.pk}: skipped, not detected as broken" in output
    assert f"Article {missing_id}: skipped, not found" in output


@pytest.mark.django_db
def test_repair_skips_problems_without_a_proposed_fix(editor_revision: EditorRevisionRequest):
    article = _resubmitted(editor_revision, with_stage_log=False)
    before = _article_data(article)
    out = StringIO()

    with pytest.raises(CommandError):
        call_command("report_double_submissions", "--repair", str(article.pk), stdout=out)

    assert _article_data(article) == before
    assert f"Article {article.pk}: skipped, nothing can be repaired automatically" in out.getvalue()


@pytest.mark.django_db
def test_resubmitted_with_new_open_revision_is_reported(editor_revision: EditorRevisionRequest):
    """A later, still open revision request does not hide an overwritten submission date."""
    article = _resubmitted(editor_revision)
    RevisionRequest.objects.create(article=article, type="minor_revisions", date_due=timezone.localdate())

    assert article in candidate_articles()
    assert _codes(article) == ["resubmitted"]


@pytest.mark.django_db
def test_article_without_workflow_does_not_break_the_report(editor_revision: EditorRevisionRequest):
    """Legacy articles may have no ArticleWorkflow: they are reported, not a crash."""
    journal = editor_revision.article.journal
    article = Article.objects.create(journal=journal, title="Legacy", stage=STAGE_ASSIGNED)
    ArticleWorkflow.objects.filter(article=article).delete()
    revision = RevisionRequest.objects.create(
        article=article,
        type="minor_revisions",
        date_due=timezone.localdate(),
        date_completed=timezone.now(),
    )
    ArticleStageLog.objects.create(
        article=article,
        stage_from=STAGE_UNSUBMITTED,
        stage_to=STAGE_UNASSIGNED,
        date_time=ORIGINAL_SUBMISSION,
    )
    Article.objects.filter(pk=article.pk).update(date_submitted=revision.date_requested + datetime.timedelta(hours=1))
    out = StringIO()

    call_command("report_double_submissions", stdout=out)

    output = out.getvalue()
    assert f"Article {article.pk} ({journal.code})" in output
    assert "workflow: none" in output
    assert _codes(Article.objects.get(pk=article.pk)) == ["resubmitted"]


WAIT = 60


def _repair_while_paused(article: Article, concurrent_write) -> dict:
    """
    Run ``--repair`` on the article and pause it while it computes the fix; meanwhile run ``concurrent_write``.

    ``concurrent_write`` runs in its own thread (hence its own DB connection) and must wait for the repair to end.
    """
    paused = threading.Event()
    release = threading.Event()
    original_find_problems = report_double_submissions.find_problems
    results = {}

    def find_problems(article):
        problems = original_find_problems(article)
        paused.set()
        assert release.wait(WAIT)
        return problems

    def repair():
        try:
            call_command("report_double_submissions", "--repair", str(article.pk), stdout=StringIO())
        except Exception as e:  # noqa: BLE001
            results["repair"] = e
        finally:
            connection.close()

    def write():
        try:
            concurrent_write()
        finally:
            connection.close()

    with mock.patch.object(report_double_submissions, "find_problems", find_problems):
        repair_thread = threading.Thread(target=repair)
        repair_thread.start()
        assert paused.wait(WAIT), f"repair never reached the pause point: {results.get('repair')!r}"
        write_thread = threading.Thread(target=write)
        write_thread.start()
        write_thread.join(3)
        results["write_was_blocked"] = write_thread.is_alive()
        release.set()
        repair_thread.join(WAIT)
        write_thread.join(WAIT)
    return results


@pytest.mark.django_db(transaction=True)
def test_repair_locks_the_article(editor_revision: EditorRevisionRequest):
    """A change to the article while the repair computes the fix waits for the repair, and is not overwritten by it."""
    article = _stage_reverted(editor_revision)

    results = _repair_while_paused(
        article,
        lambda: Article.objects.filter(pk=article.pk).update(stage=STAGE_UNDER_REVIEW),
    )

    assert "repair" not in results, results["repair"]
    assert results["write_was_blocked"], "the article must be locked while the repair computes and writes the fix"
    article.refresh_from_db()
    # The concurrent change runs after the repair, so it is kept
    assert article.stage == STAGE_UNDER_REVIEW


@pytest.mark.django_db(transaction=True)
def test_repair_locks_the_workflow(editor_revision: EditorRevisionRequest):
    """The detection reads the workflow state: a workflow transition waits for the repair too."""
    article = _stage_reverted(editor_revision)

    results = _repair_while_paused(
        article,
        lambda: ArticleWorkflow.objects.filter(article=article).update(
            state=ArticleWorkflow.ReviewStates.TO_BE_REVISED,
        ),
    )

    assert "repair" not in results, results["repair"]
    assert results["write_was_blocked"], "the workflow must be locked while the repair computes and writes the fix"
