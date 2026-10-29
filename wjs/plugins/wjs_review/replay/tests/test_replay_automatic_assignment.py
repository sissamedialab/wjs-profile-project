"""Tests for the historical replay of automatic editor assignment."""

import csv
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone
from review.models import ReviewAssignment, ReviewRound
from submission.models import STAGE_UNASSIGNED, Article, FrozenAuthor, KeywordArticle

from wjs.jcom_profile.models import StaffKeyword, StaffWorkloadParameters

from ...logic import BaseAssignToEditor
from ...models import (
    ArticleWorkflow,
    AutomaticAssignmentAudit,
    EditorDecision,
    EditorRevisionRequest,
    PastEditorAssignment,
    WjsEditorAssignment,
)
from ...tests.conftest import _accept_article
from ..command import candidates_to_dicts, report_row_to_dict
from ..logic import (
    ReplayAutomaticAssignment,
    ReplayReportRow,
    ResetArticleForReplay,
    get_original_editor,
    get_replayable_articles,
)

DEFAULT_ASSIGNMENT = {None: "plugins.wjs_review.events.assignment.default_assign_editors_to_articles"}
JCOM_ASSIGNMENT = {None: "plugins.wjs_review.events.assignment.jcom_assign_editors_to_articles"}


def _submitted(articles, start):
    """Mark articles as submitted one day apart from ``start``, in list order."""
    for offset, article in enumerate(articles):
        article.date_submitted = start + timedelta(days=offset)
        article.primary_issue = None
        article.save()
        article.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        article.articleworkflow.save()
    return articles


@pytest.mark.django_db
def test_get_original_editor_current_assignment_only(assigned_article):
    """With no reassignment, the current WjsEditorAssignment's editor is the first editor."""
    expected = WjsEditorAssignment.objects.get_current(assigned_article).editor

    assert get_original_editor(assigned_article) == expected, "sole assignment is the first one"


@pytest.mark.django_db
def test_get_original_editor_returns_first_editor_after_reassignment(assigned_article, editors):
    """After a reassignment, the first (past) editor is returned, not the current one."""
    current = WjsEditorAssignment.objects.get_current(assigned_article)
    first_editor = editors[0]
    PastEditorAssignment.objects.create(
        article=assigned_article,
        editor=first_editor,
        date_assigned=current.assigned - timedelta(days=10),
        date_unassigned=current.assigned - timedelta(days=1),
    )
    # A later past assignment must not win either.
    PastEditorAssignment.objects.create(
        article=assigned_article,
        editor=editors[1],
        date_assigned=current.assigned - timedelta(days=1),
        date_unassigned=current.assigned,
    )

    assert get_original_editor(assigned_article) == first_editor, "earliest assignment wins"


@pytest.mark.django_db
def test_get_original_editor_none_without_assignments(article):
    """An article that was never assigned has no original editor."""
    assert get_original_editor(article) is None, "no assignment, no original editor"


@pytest.mark.django_db
def test_reset_clears_review_state(fake_request, assigned_article_with_reviewer):
    """Reset deletes editor assignments, review assignments, rounds and decisions, and rewinds the workflow."""
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article_with_reviewer).editor
    article = _accept_article(fake_request, assigned_article_with_reviewer)
    assert EditorDecision.objects.filter(workflow=article.articleworkflow).exists(), "precondition: decision"

    ResetArticleForReplay(article=article).run()
    article.refresh_from_db()

    assert not WjsEditorAssignment.objects.filter(article=article).exists(), "editor assignments deleted"
    assert not ReviewAssignment.objects.filter(article=article).exists(), "review assignments deleted"
    assert not ReviewRound.objects.filter(article=article).exists(), "review rounds deleted"
    assert not EditorDecision.objects.filter(workflow=article.articleworkflow).exists(), "decisions deleted"
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.SUBMITTED, "workflow rewound"
    assert article.stage == STAGE_UNASSIGNED, "article stage rewound"


@pytest.mark.django_db
def test_reset_clears_revision_request(editor_revision):
    """Reset succeeds on an article with a revision request: the request PROTECTs its decision and round."""
    article = editor_revision.article
    assert EditorDecision.objects.filter(workflow=article.articleworkflow).exists(), "precondition: decision"

    ResetArticleForReplay(article=article).run()

    assert not EditorRevisionRequest.objects.filter(article=article).exists(), "revision requests deleted"
    assert not EditorDecision.objects.filter(workflow=article.articleworkflow).exists(), "decisions deleted"
    assert not ReviewRound.objects.filter(article=article).exists(), "review rounds deleted"
    assert not WjsEditorAssignment.objects.filter(article=article).exists(), "editor assignments deleted"


@pytest.mark.django_db
def test_reset_refuses_typeset_articles(assigned_to_typesetter_article):
    """Articles that reached typesetting are refused and left untouched."""
    state_before = assigned_to_typesetter_article.articleworkflow.state

    with pytest.raises(ValidationError):
        ResetArticleForReplay(article=assigned_to_typesetter_article).run()

    assigned_to_typesetter_article.articleworkflow.refresh_from_db()
    assert assigned_to_typesetter_article.articleworkflow.state == state_before, "state untouched"
    assert WjsEditorAssignment.objects.filter(article=assigned_to_typesetter_article).exists(), "not deleted"


@pytest.mark.django_db
def test_replay_accumulates_workload_in_submission_order(
    review_settings, journal, editors, create_submitted_articles, fake_request
):
    """
    Articles are replayed by date_submitted and each replayed assignment counts for the next one.

    All three articles were historically assigned to editors[2]. The batch is reset up front, so those
    historical assignments must not count against editors[2] when the first article is replayed. With
    equal workload of 1 each and the fallback (no keyword) path, each editor must get exactly one article,
    and the earliest-submitted article goes to the lowest-id parameter (tie-break).
    """
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=1, brake_on=0)
    first_param = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).order_by("id").first()
    articles = create_submitted_articles(journal, 3)
    # The highest pk gets the earliest submission date, to prove ordering is by date_submitted, not pk.
    earliest, middle, latest = _submitted(list(reversed(articles)), timezone.now() - timedelta(days=10))
    for article in articles:
        BaseAssignToEditor(editor=editors[2], article=article, request=fake_request, first_assignment=True).run()

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        rows = ReplayAutomaticAssignment(articles=Article.objects.filter(pk__in=[a.pk for a in articles])).run()

    assert [row.article for row in rows] == [earliest, middle, latest], "processed in date_submitted order"
    assert {row.new_editor for row in rows} == set(editors), "workload accumulates: one article per editor"
    assert rows[0].new_editor == first_param.user, "tie on available workload is broken by parameter id"
    assert all(row.original_editor == editors[2] for row in rows), "original editor snapshotted before reset"
    for row in rows:
        assignment = WjsEditorAssignment.objects.get(article=row.article)
        assert assignment.assigned == row.article.date_submitted, "assignment dated at the simulated submission"
        assert row.audit == AutomaticAssignmentAudit.objects.latest_for_article(row.article), "audit linked"
        assert row.audit.created_at == row.article.date_submitted, "audit dated at the simulated submission"
        row.article.articleworkflow.refresh_from_db()
        assert row.article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED, "like live"


@pytest.mark.django_db
def test_replay_links_its_own_audit_not_a_later_live_one(
    review_settings, journal, editors, create_submitted_articles, fake_request
):
    """A live audit row dated after the submission must not be reported in place of the replay's own row."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    BaseAssignToEditor(editor=editors[0], article=article, request=fake_request, first_assignment=True).run()
    live_audit = AutomaticAssignmentAudit.objects.create(
        article=article, editor=editors[0], algorithm=AutomaticAssignmentAudit.Algorithms.DEFAULT
    )
    AutomaticAssignmentAudit.objects.filter(pk=live_audit.pk).update(
        created_at=article.date_submitted + timedelta(hours=1)
    )

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()

    assert row.audit is not None, "precondition: the replay assigned an editor"
    assert row.audit.pk != live_audit.pk, "the live audit row must not be reported"
    assert row.audit.pk > live_audit.pk, "the replay's audit row is the one created by the replay"
    assert row.audit.created_at == article.date_submitted, "the replay's audit is dated at the submission"


@pytest.mark.django_db
def test_replay_refuses_already_replayed_article(
    review_settings, journal, editors, create_submitted_articles, fake_request
):
    """A second replay of the same article is skipped: its original editor can no longer be recovered."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    BaseAssignToEditor(editor=editors[0], article=article, request=fake_request, first_assignment=True).run()
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (first,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()
    assignment = WjsEditorAssignment.objects.get(article=article)
    assert first.new_editor is not None, "precondition: the first replay assigned an editor"

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (second,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()

    assert "already replayed" in second.skipped_reason, "second replay is skipped with the re-import reason"
    assert second.new_editor is None, "skipped articles are not replayed"
    current = WjsEditorAssignment.objects.get(article=article)
    assert (current.pk, current.editor) == (assignment.pk, assignment.editor), "assignment untouched"


@pytest.mark.django_db
def test_replay_reports_unassigned_article(review_settings, journal, editors, create_submitted_articles, fake_request):
    """When no editor is available, the row has no new editor, no audit, and the workflow awaits a director."""
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    # The article must have a historical editor, or the replay would skip it outright (see
    # test_replay_skips_article_with_no_historical_editor) instead of exercising "no editor found on replay".
    BaseAssignToEditor(editor=editors[0], article=article, request=fake_request, first_assignment=True).run()
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(enabled=False)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()

    assert row.original_editor == editors[0], "precondition: the article had a historical editor"
    assert row.new_editor is None, "nobody available"
    assert row.audit is None, "no audit without assignment"
    assert row.match is False, "no match without a new editor"
    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, "like live"


@pytest.mark.django_db
def test_replay_skips_article_with_no_historical_editor(review_settings, journal, editors, create_submitted_articles):
    """An article that never had an editor assigned historically is skipped, not reset and replayed."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    state_before = article.articleworkflow.state
    stage_before = article.stage

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()

    assert row.skipped_reason == (
        "Article never had an editor assigned historically: nothing to compare against."
    ), "the skip reason explains why there is nothing to replay against"
    assert row.new_editor is None, "skipped articles are not replayed"
    assert not WjsEditorAssignment.objects.filter(article=article).exists(), "no assignment is created"
    article.articleworkflow.refresh_from_db()
    article.refresh_from_db()
    assert article.articleworkflow.state == state_before, "workflow state left untouched"
    assert article.stage == stage_before, "article stage left untouched"


@pytest.mark.django_db
def test_replay_skips_typeset_articles(review_settings, assigned_to_typesetter_article):
    """Typeset articles are reported as skipped and left untouched; the batch does not abort."""
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=assigned_to_typesetter_article.pk)).run()

    assert row.skipped_reason, "a skip reason must be reported"
    assert row.new_editor is None, "skipped articles are not replayed"
    assert WjsEditorAssignment.objects.filter(article=assigned_to_typesetter_article).exists(), "untouched"


@pytest.mark.django_db
def test_replay_sends_no_real_email(
    review_settings, journal, editors, create_submitted_articles, fake_request, settings, mocker
):
    """Replay forces the locmem email backend, whatever the instance is configured with."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    BaseAssignToEditor(editor=editors[0], article=article, request=fake_request, first_assignment=True).run()
    # Patched only now: the historical assignment above must not count against the "no real email" assertion.
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    smtp_send = mocker.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=0)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()

    assert row.new_editor is not None, "precondition: an assignment (and its notification) happened"
    smtp_send.assert_not_called()


@pytest.mark.django_db
def test_get_replayable_articles_filters(journal, create_submitted_articles):
    """Only submitted articles of the journal in the date window (or explicit ids) are selected."""
    inside, outside, incomplete = create_submitted_articles(journal, 3)
    now = timezone.now()
    _submitted([inside], now - timedelta(days=5))
    _submitted([outside], now - timedelta(days=50))
    incomplete.date_submitted = None
    incomplete.save()

    by_window = get_replayable_articles(journal, date_from=(now - timedelta(days=10)).date(), date_to=now.date())
    by_ids = get_replayable_articles(journal, article_ids=[outside.pk, incomplete.pk])

    assert list(by_window) == [inside], "date window on date_submitted"
    assert list(by_ids) == [outside], "explicit ids, still excluding never-submitted articles"
    assert not get_replayable_articles(journal, article_ids=[]).exists(), "empty id list selects nothing"


@pytest.mark.django_db
def test_command_refuses_without_confirmation(journal):
    """Without --confirm-copy-instance the command refuses to touch anything."""
    with pytest.raises(CommandError, match="confirm-copy-instance"):
        call_command("replay_automatic_assignment", "--journal", journal.code, "--article-ids", "1")


@pytest.mark.django_db
def test_command_requires_selection(journal):
    """Either a date window or explicit article ids must be given."""
    with pytest.raises(CommandError, match="--article-ids"):
        call_command("replay_automatic_assignment", "--journal", journal.code, "--confirm-copy-instance")


MAIN_CSV_FIELDS = [
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
CANDIDATES_CSV_FIELDS = [
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


def _run_command(article_ids, output, *extra):
    """Run the replay command on the default algorithm for the given article ids."""
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        call_command(
            "replay_automatic_assignment",
            "--journal",
            Article.objects.get(pk=article_ids[0]).journal.code,
            "--article-ids",
            *[str(pk) for pk in article_ids],
            "--output-file",
            str(output),
            "--confirm-copy-instance",
            *extra,
        )


def _read_csv(path):
    """Return the header and the rows of a CSV file."""
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, list(reader)


@pytest.mark.django_db
def test_command_writes_csv_report(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords, tmp_path, capsys
):
    """
    The command writes one CSV row per replayed article, lists skipped ones on stdout, and writes candidates.

    The skipped article (no historical editor) is not in either CSV: it is printed after the summary instead.
    """
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5, brake_on=0)
    # With equal workloads, the fallback path always picks the lowest-id parameter (see
    # test_replay_accumulates_workload_in_submission_order): use it as the historical editor too, so the
    # article has one to compare against, and the replay picks it again.
    first_param = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).order_by("id").first()
    article, skipped = _submitted(create_submitted_articles(journal, 2), timezone.now() - timedelta(days=3))
    _set_keywords(article, {keywords[0]: 60})
    StaffKeyword.objects.create(parameters=first_param, keyword=keywords[0], weight=40)
    BaseAssignToEditor(editor=first_param.user, article=article, request=fake_request, first_assignment=True).run()
    output = tmp_path / "report.csv"

    _run_command([article.pk, skipped.pk], output)

    header, rows = _read_csv(output)
    assert header == MAIN_CSV_FIELDS, "main CSV columns must follow the spec exactly"
    assert "algorithm" not in header and "skipped_reason" not in header, "dropped columns must be gone"
    assert [row["article_id"] for row in rows] == [str(article.pk)], "only replayed articles are written"
    assert rows[0]["match"] == "True", "with equal workloads the lowest-id parameter is picked again"
    assert rows[0]["matching_keywords"] == "0-keyword (article 60, editor 40)", "winner's shared keywords"
    assert rows[0]["monthly_workload"] == "5", "renamed monthly workload column holds the monthly availability"
    assert rows[0]["workload_30d"] == "5", "renamed 30-day column holds the 30-day availability"
    stdout = capsys.readouterr().out
    assert "Processed: 2" in stdout, "summary printed to stdout"
    assert (
        f"Skipped {skipped.pk}: Article never had an editor assigned historically: nothing to compare against."
        in stdout
    ), "skipped articles are listed on stdout with their reason"
    assert stdout.index("Skipped: 1") < stdout.index(f"Skipped {skipped.pk}:"), "skip list follows the summary"

    candidates_file = tmp_path / "report_candidates.csv"
    assert candidates_file.exists(), "candidates CSV defaults to the main path with a _candidates suffix"
    header, candidates = _read_csv(candidates_file)
    assert header == CANDIDATES_CSV_FIELDS, "candidates CSV columns must follow the spec exactly"
    assert candidates == [
        {
            "article_id": str(article.pk),
            "editor": f"{first_param.user.full_name()} <{first_param.user.email}>",
            "rank": "1",
            "selected": "True",
            "reason": "selected",
            "keyword_score": "0.400",
            "matching_keywords_count": "1",
            "matching_keywords": "0-keyword (article 60, editor 40)",
            "monthly_workload": "5",
            "workload_30d": "5",
            "workload_cap": "5",
        }
    ], "only the editor sharing a keyword gets a candidate row"


@pytest.mark.django_db
def test_command_candidates_file_override(
    review_settings, journal, editors, create_submitted_articles, fake_request, tmp_path
):
    """--candidates-file overrides the default candidates path; a main path without .csv gets the suffix."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    BaseAssignToEditor(editor=editors[0], article=article, request=fake_request, first_assignment=True).run()
    override = tmp_path / "explained.csv"

    _run_command([article.pk], tmp_path / "report.csv", "--candidates-file", str(override))

    assert override.exists(), "the candidates CSV is written where --candidates-file says"
    assert not (tmp_path / "report_candidates.csv").exists(), "no default candidates file when overridden"
    assert _read_csv(override)[0] == CANDIDATES_CSV_FIELDS, "overridden file has the candidates header"


@pytest.mark.django_db
def test_command_candidates_file_default_without_csv_extension(
    review_settings, journal, editors, create_submitted_articles, fake_request, tmp_path
):
    """A main output path not ending in .csv gets ``_candidates.csv`` appended."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    BaseAssignToEditor(editor=editors[0], article=article, request=fake_request, first_assignment=True).run()

    _run_command([article.pk], tmp_path / "report")

    assert (tmp_path / "report_candidates.csv").exists(), "suffix appended to a path without .csv"


def _set_keywords(article, weights_by_keyword):
    """Replace the article's keywords with the given ones and KeywordArticle weights (None allowed)."""
    KeywordArticle.objects.filter(article=article).delete()
    for keyword, weight in weights_by_keyword.items():
        article.keywords.add(keyword)
        KeywordArticle.objects.filter(article=article, keyword=keyword).update(weight=weight)


def _parameters(editor, journal):
    """Return the editor's StaffWorkloadParameters for the journal."""
    return StaffWorkloadParameters.objects.get(user=editor, journal=journal)


def _prepare_article(journal, editors, create_submitted_articles, fake_request, weights_by_keyword):
    """
    Create a submitted article with the given keywords, historically assigned to editors[2].

    Every editor gets workload 5 and no brake: tests tune the parameters they care about. editors[2] never
    shares a keyword in these tests, so it never gets a candidate row.
    """
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5, brake_on=0)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    _set_keywords(article, weights_by_keyword)
    BaseAssignToEditor(editor=editors[2], article=article, request=fake_request, first_assignment=True).run()
    return article


def _replay_one(article, assignment_functions=DEFAULT_ASSIGNMENT):
    """Replay one article and return its report row."""
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=assignment_functions):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()
    return row


def _candidates_by_editor(row):
    """Index a row's candidates by editor."""
    return {candidate.editor: candidate for candidate in row.candidates}


def _assert_rank_one_is_the_winner(row):
    """The candidate ranked first before dispatch must be the editor dispatch actually picked."""
    (rank_one,) = (candidate for candidate in row.candidates if candidate.rank == 1)
    assert rank_one.editor == row.new_editor, "explanation's rank 1 must agree with the algorithm's pick"
    assert rank_one.selected is True, "rank 1 is flagged as selected"
    assert rank_one.reason == "selected", "rank 1 carries the selected reason"
    assert [c.editor for c in row.candidates if c.selected] == [row.new_editor], "only the winner is selected"


@pytest.mark.django_db
def test_candidates_lower_keyword_score(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords
):
    """A ranked editor with a lower weighted score than the winner is explained as such."""
    article = _prepare_article(journal, editors, create_submitted_articles, fake_request, {keywords[0]: None})
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[0], weight=100)
    StaffKeyword.objects.create(parameters=_parameters(editors[1], journal), keyword=keywords[0], weight=50)

    row = _replay_one(article)

    candidates = _candidates_by_editor(row)
    assert set(candidates) == {editors[0], editors[1]}, "only editors sharing a keyword are candidates"
    assert row.new_editor == editors[0], "the higher score wins"
    _assert_rank_one_is_the_winner(row)
    loser = candidates[editors[1]]
    assert (loser.rank, loser.selected, loser.reason) == (2, False, "lower keyword score"), "lower score explained"
    assert loser.keyword_score == pytest.approx(0.5), "loser's score is reported"
    assert [c.editor for c in row.candidates] == [editors[0], editors[1]], "selected first, then by rank"


@pytest.mark.django_db
def test_candidates_same_score_fewer_shared_keywords(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords
):
    """With equal scores (all editor weights 0), the editor sharing fewer keywords loses on that."""
    article = _prepare_article(
        journal, editors, create_submitted_articles, fake_request, {keywords[0]: 50, keywords[1]: 50}
    )
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[0], weight=0)
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[1], weight=0)
    StaffKeyword.objects.create(parameters=_parameters(editors[1], journal), keyword=keywords[0], weight=0)

    row = _replay_one(article)

    _assert_rank_one_is_the_winner(row)
    assert row.new_editor == editors[0], "more shared keywords wins the score tie"
    loser = _candidates_by_editor(row)[editors[1]]
    assert (loser.rank, loser.reason) == (2, "same score, fewer shared keywords"), "count tie-break explained"


@pytest.mark.django_db
def test_candidates_same_score_less_30d_availability(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords
):
    """With equal score and shared keywords, the editor with less 30-day availability loses on that."""
    article = _prepare_article(journal, editors, create_submitted_articles, fake_request, {keywords[0]: 50})
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[0], weight=100)
    StaffKeyword.objects.create(parameters=_parameters(editors[1], journal), keyword=keywords[0], weight=100)
    StaffWorkloadParameters.objects.filter(user=editors[1], journal=journal).update(workload=3)

    row = _replay_one(article)

    _assert_rank_one_is_the_winner(row)
    assert row.new_editor == editors[0], "more 30-day availability wins the full keyword tie"
    loser = _candidates_by_editor(row)[editors[1]]
    assert (loser.rank, loser.reason) == (
        2,
        "same score and shared keywords, less 30-day availability",
    ), "availability tie-break explained"
    assert (loser.monthly_workload, loser.workload_30d, loser.workload_cap) == (3, 3, 3), "loser's workload"


@pytest.mark.django_db
def test_candidates_full_tie_lost_on_id(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords
):
    """With every ranking value equal, the higher parameter id loses."""
    article = _prepare_article(journal, editors, create_submitted_articles, fake_request, {keywords[0]: 50})
    first, second = sorted([_parameters(editors[0], journal), _parameters(editors[1], journal)], key=lambda p: p.pk)
    StaffKeyword.objects.create(parameters=first, keyword=keywords[0], weight=100)
    StaffKeyword.objects.create(parameters=second, keyword=keywords[0], weight=100)

    row = _replay_one(article)

    _assert_rank_one_is_the_winner(row)
    assert row.new_editor == first.user, "the lowest parameter id wins a full tie"
    loser = _candidates_by_editor(row)[second.user]
    assert (loser.rank, loser.reason) == (2, "full tie, lost on id"), "id tie-break explained"


def _make_author(article, editor, journal):
    """Make the editor an author of the article."""
    FrozenAuthor.objects.create(article=article, author=editor, order=99)


def _make_unavailable(article, editor, journal):
    """Disable the editor's parameters."""
    StaffWorkloadParameters.objects.filter(user=editor, journal=journal).update(enabled=False)


def _make_over_monthly_limit(article, editor, journal):
    """Force the editor's monthly cap to 0 via the EO-disabled brake."""
    StaffWorkloadParameters.objects.filter(user=editor, journal=journal).update(brake_on=-1)


@pytest.mark.parametrize(
    "exclude, reason",
    (
        (_make_author, "author of the article"),
        (_make_unavailable, "not available (disabled, on leave or no workload)"),
        (_make_over_monthly_limit, "monthly limit reached"),
    ),
)
@pytest.mark.django_db
def test_candidates_excluded_editor(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords, exclude, reason
):
    """An editor sharing the best keyword but excluded by the algorithm is listed, unranked, with the reason."""
    article = _prepare_article(journal, editors, create_submitted_articles, fake_request, {keywords[0]: 50})
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[0], weight=100)
    StaffKeyword.objects.create(parameters=_parameters(editors[1], journal), keyword=keywords[0], weight=10)
    exclude(article, editors[0], journal)

    row = _replay_one(article)

    _assert_rank_one_is_the_winner(row)
    assert row.new_editor == editors[1], "the excluded editor cannot win"
    excluded = _candidates_by_editor(row)[editors[0]]
    assert (excluded.rank, excluded.selected, excluded.reason) == (None, False, reason), "exclusion explained"
    assert excluded.keyword_score == pytest.approx(1.0), "excluded editors still report their score"
    assert [c.editor for c in row.candidates] == [editors[1], editors[0]], "excluded candidates come last"


@pytest.mark.django_db
def test_candidates_author_reason_wins_over_unavailable(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords
):
    """Exclusion reasons are checked author first."""
    article = _prepare_article(journal, editors, create_submitted_articles, fake_request, {keywords[0]: 50})
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[0], weight=100)
    _make_author(article, editors[0], journal)
    _make_unavailable(article, editors[0], journal)

    row = _replay_one(article)

    assert _candidates_by_editor(row)[editors[0]].reason == "author of the article", "author is checked first"


@pytest.mark.django_db
def test_candidates_none_for_special_issue(
    review_settings, journal, editors, fake_request, keywords, special_issue, article
):
    """Special-issue articles follow the pure-workload path: no candidate rows."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    article.date_submitted = timezone.now() - timedelta(days=3)
    article.save()
    ArticleWorkflow.objects.filter(article=article).update(state=ArticleWorkflow.ReviewStates.SUBMITTED)
    _set_keywords(article, {keywords[0]: 50})
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[0], weight=100)
    BaseAssignToEditor(editor=editors[2], article=article, request=fake_request, first_assignment=True).run()
    article.refresh_from_db()
    assert article.primary_issue == special_issue, "precondition: the article is in the special issue"

    row = _replay_one(article)

    assert row.audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT_SPECIAL_ISSUE, "special-issue path"
    assert row.candidates == [], "no candidates on the special-issue path"
    assert report_row_to_dict(row)["matching_keywords"] == "", "no matching keywords outside the keyword path"


@pytest.mark.django_db
def test_candidates_none_for_jcom_algorithm(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords
):
    """Journals using the JCOM algorithm get no candidate rows."""
    article = _prepare_article(journal, editors, create_submitted_articles, fake_request, {keywords[0]: 50})
    StaffKeyword.objects.create(parameters=_parameters(editors[0], journal), keyword=keywords[0], weight=100)

    row = _replay_one(article, assignment_functions=JCOM_ASSIGNMENT)

    assert row.candidates == [], "no candidates on the JCOM path"


@pytest.mark.django_db
def test_report_row_matching_keywords_formatting(
    review_settings, journal, editors, create_submitted_articles, fake_request, keywords
):
    """Shared keywords are ordered by article weight (NULL counts as 100) then word; NULL shows as a dash."""
    article = _prepare_article(
        journal,
        editors,
        create_submitted_articles,
        fake_request,
        {keywords[3]: 30, keywords[2]: 80, keywords[1]: 80, keywords[0]: None, keywords[4]: 90},
    )
    parameters = _parameters(editors[0], journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[3], weight=10)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[2], weight=20)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[1], weight=25)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=5)
    # keywords[4] is shared by editors[1] only: it must not appear in editors[0]'s list.
    StaffKeyword.objects.create(parameters=_parameters(editors[1], journal), keyword=keywords[4], weight=1)

    row = _replay_one(article)

    assert row.new_editor == editors[0], "precondition: editors[0] wins on score"
    expected = (
        "0-keyword (article —, editor 5); 1-keyword (article 80, editor 25); "
        "2-keyword (article 80, editor 20); 3-keyword (article 30, editor 10)"
    )
    assert report_row_to_dict(row)["matching_keywords"] == expected, "format, order and NULL dash"
    assert candidates_to_dicts(row)[0]["matching_keywords"] == expected, "the candidates CSV uses the same format"


@pytest.mark.django_db
def test_report_row_keyword_score_formatting(article, editors):
    """The CSV keyword score has three decimals, and is empty for legacy or missing audits."""
    article.date_submitted = timezone.now()
    scored = AutomaticAssignmentAudit.objects.create(
        article=article,
        editor=editors[0],
        algorithm=AutomaticAssignmentAudit.Algorithms.DEFAULT,
        matched_by_keyword=True,
        workload_available=3,
        workload_cap=5,
        keyword_score=0.52,
    )
    legacy = AutomaticAssignmentAudit.objects.create(
        article=article,
        editor=editors[0],
        algorithm=AutomaticAssignmentAudit.Algorithms.DEFAULT,
        matched_by_keyword=False,
        workload_available=3,
        workload_cap=5,
        keyword_score=None,
    )

    def _score(audit):
        return report_row_to_dict(
            ReplayReportRow(article=article, original_editor=None, new_editor=editors[0], audit=audit)
        )["keyword_score"]

    assert _score(scored) == "0.520", "score is formatted with three decimals"
    assert _score(legacy) == "", "a NULL score gives an empty cell"
    assert _score(None) == "", "a row without audit gives an empty cell"
