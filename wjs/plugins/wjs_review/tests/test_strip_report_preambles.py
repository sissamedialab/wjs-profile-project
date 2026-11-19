"""Tests for the strip_report_preambles management command (specs#3456)."""

from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from review.models import ReviewRound
from submission.models import Article

from ..management.commands.strip_report_preambles import strip_preamble
from ..models import (
    ArticleWorkflow,
    EditorDecision,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

FULL_DOCUMENT = "\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\nThe report.\n\\end{document}\n\n"
FULL_DOCUMENT_WITH_BANNERS = r"""\documentclass{article}
\begin{document}
\parindent=0pt
{\Large\scshape Report on JCOM_001A_0125}
\bigskip
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%        write your report in LaTeX       %%%%%%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%  starting in the next empty line below  %%%%%%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%   (no need of \begin{document} etc.)    %%%%%%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

The report.

Second paragraph.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%              end of report             %%%%%%%%%%%%%%%%%%%%%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
\vfill
Signed
\end{document}
"""


@pytest.mark.parametrize(
    "text,expected",
    [
        (FULL_DOCUMENT, "The report.\n"),
        (FULL_DOCUMENT_WITH_BANNERS, "The report.\n\nSecond paragraph.\n"),
        (
            "%%%%\n%%%%   (no need of \\begin{document} etc.)  %%%%\n%%%%\n"
            "The report.\n"
            "%%%%\n%%%%  end of report  %%%%\n",
            "The report.\n",
        ),
        ("\\documentclass{article}\n\\begin {document}\nThe report.", "The report."),
        ("The report.\n\\end{document}", "The report.\n"),
        ("The report.", None),
        ("The \\end{document} report.", None),
        ("", None),
        (None, None),
    ],
)
def test_strip_preamble(text, expected):
    """The preamble and a trailing \\end{document} are stripped; None is returned if nothing changes."""
    assert strip_preamble(text) == expected, f"Unexpected result stripping {text!r}"


def _editor_decision(article: Article, report: str) -> EditorDecision:
    workflow = article.articleworkflow
    return EditorDecision.objects.create(
        workflow=workflow,
        editor=WjsEditorAssignment.objects.get_current(workflow).editor,
        review_round=article.reviewround_set.order_by("round_number").last(),
        decision=ArticleWorkflow.Decisions.MINOR_REVISION,
        decision_editor_report=report,
    )


def _set_tex_report(assignment: WorkflowReviewAssignment, report: str):
    assignment.report_form_answers["author_review_tex"] = report
    assignment.save()


@pytest.mark.django_db
def test_strip_report_preambles_confirmed(review_assignment: WorkflowReviewAssignment):
    """When the operator confirms, reviewer and editor reports are saved without preamble."""
    article = review_assignment.article
    _set_tex_report(review_assignment, FULL_DOCUMENT)
    decision = _editor_decision(article, FULL_DOCUMENT)

    out = StringIO()
    with mock.patch("builtins.input", return_value="y") as mocked_input:
        call_command("strip_report_preambles", article.pk, stdout=out)

    assert mocked_input.call_count == 2, "The operator must be asked once per report"
    review_assignment.refresh_from_db()
    decision.refresh_from_db()
    assert (
        review_assignment.report_form_answers["author_review_tex"] == "The report.\n"
    ), "Reviewer report not stripped"
    assert decision.decision_editor_report == "The report.\n", "Editor report not stripped"
    output = out.getvalue()
    assert f"Reviewer {review_assignment.reviewer.full_name()} - " in output, "Reviewer not shown"
    assert (
        f"Editor {decision.editor.full_name()} - {decision.created.date().isoformat()}" in output
    ), "Editor not shown"
    assert "—" * 80 in output, "Em-dash separator not shown"
    assert "🙁" * 80 + "\n" + "🙂" * 80 in output, "Original/stripped separator not shown"
    assert "\\documentclass{article}" in output, "Original report not shown"


@pytest.mark.django_db
@pytest.mark.parametrize("answer", ["", "n", "no", "whatever"])
def test_strip_report_preambles_not_confirmed(review_assignment: WorkflowReviewAssignment, answer: str):
    """Unless the operator answers yes, nothing is saved (No is the default)."""
    article = review_assignment.article
    _set_tex_report(review_assignment, FULL_DOCUMENT)
    decision = _editor_decision(article, FULL_DOCUMENT)

    with mock.patch("builtins.input", return_value=answer):
        call_command("strip_report_preambles", article.pk, stdout=StringIO())

    review_assignment.refresh_from_db()
    decision.refresh_from_db()
    assert review_assignment.report_form_answers["author_review_tex"] == FULL_DOCUMENT, "Reviewer report was saved"
    assert decision.decision_editor_report == FULL_DOCUMENT, "Editor report was saved"


@pytest.mark.django_db
def test_strip_report_preambles_leaves_clean_reports(review_assignment: WorkflowReviewAssignment):
    """Reports without preamble are left untouched."""
    _set_tex_report(review_assignment, "The report.")

    out = StringIO()
    with mock.patch("builtins.input") as mocked_input:
        call_command("strip_report_preambles", review_assignment.article.pk, stdout=out)

    mocked_input.assert_not_called()

    review_assignment.refresh_from_db()
    assert review_assignment.report_form_answers["author_review_tex"] == "The report.", "Clean report was changed"
    assert "Nothing to strip" in out.getvalue(), "Untouched report not reported"


@pytest.mark.django_db
def test_strip_report_preambles_refuses_papers_not_in_review(review_assignment: WorkflowReviewAssignment):
    """Papers that are not in review are refused and not changed."""
    article = review_assignment.article
    _set_tex_report(review_assignment, FULL_DOCUMENT)
    ArticleWorkflow.objects.filter(article=article).update(state=ArticleWorkflow.ReviewStates.ACCEPTED)

    with pytest.raises(CommandError, match="Refusing to process a paper that does not need any change"):
        call_command("strip_report_preambles", article.pk, stdout=StringIO())

    review_assignment.refresh_from_db()
    assert review_assignment.report_form_answers["author_review_tex"] == FULL_DOCUMENT, "Refused report was changed"


@pytest.mark.django_db
def test_strip_report_preambles_missing_article():
    """A missing article is reported."""
    with pytest.raises(CommandError, match="does not exist"):
        call_command("strip_report_preambles", 999999, stdout=StringIO())


def _complete(assignment: WorkflowReviewAssignment):
    WorkflowReviewAssignment.objects.filter(pk=assignment.pk).update(is_complete=True, date_complete=timezone.now())


def _suggest(journal_code: str) -> list[str]:
    out = StringIO()
    call_command("strip_report_preambles", journal_code, "--suggest", stdout=out)
    return out.getvalue().split()


@pytest.mark.django_db
def test_suggest_lists_articles_with_completed_review_in_latest_round(review_assignment: WorkflowReviewAssignment):
    """Articles in EditorSelected with a completed review in the latest round are suggested, nothing is changed."""
    article = review_assignment.article
    _set_tex_report(review_assignment, FULL_DOCUMENT)
    _complete(review_assignment)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED, "Unexpected fixture state"

    with mock.patch("builtins.input") as mocked_input:
        suggested = _suggest(article.journal.code)

    assert suggested == [str(article.pk)], "Article with completed review not suggested"
    mocked_input.assert_not_called()
    review_assignment.refresh_from_db()
    assert review_assignment.report_form_answers["author_review_tex"] == FULL_DOCUMENT, "Report changed by --suggest"


@pytest.mark.django_db
def test_suggest_skips_pending_reviews(review_assignment: WorkflowReviewAssignment):
    """Articles whose reviews are not completed are not suggested."""
    assert _suggest(review_assignment.article.journal.code) == [], "Article with pending review suggested"


@pytest.mark.django_db
def test_suggest_skips_completed_reviews_of_previous_rounds(review_assignment: WorkflowReviewAssignment):
    """Completed reviews of a previous round do not count."""
    article = review_assignment.article
    _complete(review_assignment)
    ReviewRound.objects.create(article=article, round_number=review_assignment.review_round.round_number + 1)
    assert _suggest(article.journal.code) == [], "Article with a completed review in an old round suggested"


@pytest.mark.django_db
def test_suggest_skips_other_states(review_assignment: WorkflowReviewAssignment):
    """Articles that are not in EditorSelected are not suggested."""
    article = review_assignment.article
    _complete(review_assignment)
    ArticleWorkflow.objects.filter(article=article).update(state=ArticleWorkflow.ReviewStates.TO_BE_REVISED)
    assert _suggest(article.journal.code) == [], "Article not in EditorSelected suggested"


@pytest.mark.django_db
def test_suggest_missing_journal():
    """A missing journal is reported."""
    with pytest.raises(CommandError, match="Journal NOPE does not exist"):
        call_command("strip_report_preambles", "NOPE", "--suggest", stdout=StringIO())


def test_invalid_article_id():
    """Without --suggest the positional argument must be an article ID."""
    with pytest.raises(CommandError, match="Invalid article ID"):
        call_command("strip_report_preambles", "JCOM", stdout=StringIO())
