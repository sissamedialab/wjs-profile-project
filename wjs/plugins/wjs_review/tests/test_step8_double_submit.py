"""
Overlapping step-8 submissions of the same revision (specs#3233).

Two POSTs to the real step-8 view run in two threads, hence in two DB connections. Before the step-8 view locked the
article, the second request either crashed (``RevisionStorage.DoesNotExist``, ``TransitionNotAllowed``) after saving
stale article data, or silently re-ran the first-submission flow on an article already under review.
"""

import threading
from unittest import mock

import pytest
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.db import connection
from plugins.wjs_submission.models import RevisionStorage
from plugins.wjs_submission.revision.views import RevisionStartConfirmView
from plugins.wjs_submission.step8.views import SubmissionStep8View
from review.models import ReviewRound
from submission.models import STAGE_ASSIGNED, STAGE_UNDER_REVISION, Article
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import create_rich_fake_request

from ..models import EditorRevisionRequest, Message, WjsEditorAssignment

WAIT = 60


def _prepare_revision(article: Article):
    """Start a confirm-previous-version revision flow and fill the data required to submit it."""
    RevisionStartConfirmView()._init_revision_flow(article_id=article.pk)
    revision_storage = RevisionStorage.objects.get(article=article)
    revision_storage.data.update({"comments_editor": "author note", "submission_requirements": True})
    revision_storage.save()
    if article.submission_data.access_mode_id is None:
        article.submission_data.access_mode = article.journal.access_mode_parameters.first().access_mode
        article.submission_data.save()
    article.refresh_from_db()
    assert article.stage == STAGE_UNDER_REVISION


def _snapshot(article: Article) -> dict:
    """Collect the article data a double submission used to corrupt."""
    article = Article.objects.get(pk=article.pk)
    return {
        "stage": article.stage,
        "workflow_state": article.articleworkflow.state,
        "date_submitted": article.date_submitted,
        "review_rounds": ReviewRound.objects.filter(article=article).count(),
        "editor_assignments": WjsEditorAssignment.objects.filter(article=article).count(),
        "messages": list(
            Message.objects.filter(
                content_type=ContentType.objects.get_for_model(article),
                object_id=article.pk,
            )
            .order_by("pk")
            .values_list("subject", flat=True),
        ),
    }


def _post_step8(article: Article, settings, results: dict, key: str):
    """Run the step-8 view as a POST from the correspondence author, in its own DB connection."""
    try:
        request = create_rich_fake_request(article.journal, settings, user=article.correspondence_author)
        request.method = "POST"
        results[key] = SubmissionStep8View.as_view()(request, article_id=article.pk)
        results[f"{key}_messages"] = [str(message) for message in get_messages(request)]
    except Exception as e:  # noqa: BLE001
        results[key] = e
    finally:
        connection.close()


def _run_race(article: Article, settings, pause_where: str) -> dict:
    """
    Run request B, pause it at ``pause_where``, start request A, then resume B.

    ``pause_where`` is "after_form_kwargs" (B already knows it is submitting a revision) or "before_form_kwargs" (B
    loaded the article but did not check the revision yet): the two windows that led to different failures.
    """
    b_paused = threading.Event()
    release_b = threading.Event()
    original_get_form_kwargs = SubmissionStep8View.get_form_kwargs

    def get_form_kwargs(view):
        is_b = threading.current_thread().name == "request-B"
        if is_b and pause_where == "before_form_kwargs":
            b_paused.set()
            assert release_b.wait(WAIT)
        kwargs = original_get_form_kwargs(view)
        if is_b and pause_where == "after_form_kwargs":
            b_paused.set()
            assert release_b.wait(WAIT)
        return kwargs

    results = {}
    with mock.patch.object(SubmissionStep8View, "get_form_kwargs", get_form_kwargs):
        thread_b = threading.Thread(target=_post_step8, args=(article, settings, results, "B"), name="request-B")
        thread_b.start()
        assert b_paused.wait(WAIT), f"request B never reached the pause point: {results.get('B')!r}"
        thread_a = threading.Thread(target=_post_step8, args=(article, settings, results, "A"), name="request-A")
        thread_a.start()
        thread_a.join(3)
        results["a_was_blocked"] = thread_a.is_alive()
        release_b.set()
        thread_b.join(WAIT)
        thread_a.join(WAIT)
    return results


def _acknowledgement_subject(article: Article) -> str:
    """Subject of the acknowledgement sent on a first submission (it must not be sent for a revision)."""
    return get_setting("email_subject", "subject_submission_acknowledgement", article.journal).processed_value


def _assert_submitted_once(article: Article, revision: EditorRevisionRequest, date_submitted, results: dict):
    """Both requests answered with the status page, and the revision was submitted exactly once."""
    for key in ("A", "B"):
        assert not isinstance(results[key], Exception), f"{key}: {results[key]!r}"
        assert results[key].status_code == 302
        # The journal URL prefix is thread-local: the worker threads build URLs without it
        assert results[key].url.endswith(f"/submission/{article.pk}/0/")
    revision.refresh_from_db()
    assert revision.date_completed is not None
    assert not RevisionStorage.objects.filter(article=article).exists()
    final = _snapshot(article)
    assert final["stage"] == STAGE_ASSIGNED
    assert final["date_submitted"] == date_submitted
    assert _acknowledgement_subject(article) not in final["messages"]
    return final


@pytest.mark.django_db(transaction=True)
def test_single_submit_state(editor_revision: EditorRevisionRequest, access_mode_oa, settings):
    """A single submission of a revision: the reference state for the concurrent cases."""
    article = editor_revision.article
    _prepare_revision(article)
    date_submitted = article.date_submitted
    results = {}

    _post_step8(article, settings, results, "A")
    results["B"] = results["A"]

    final = _assert_submitted_once(article, editor_revision, date_submitted, results)
    assert final["workflow_state"] == "EditorSelected"


@pytest.mark.parametrize("pause_where", ["after_form_kwargs", "before_form_kwargs"])
@pytest.mark.django_db(transaction=True)
def test_race_is_serialized(editor_revision: EditorRevisionRequest, access_mode_oa, settings, pause_where):
    """The second request waits for the article lock, then is sent to the status page without side effects."""
    article = editor_revision.article
    _prepare_revision(article)
    date_submitted = article.date_submitted

    results = _run_race(article, settings, pause_where)

    assert results["a_was_blocked"], "request A must wait for the article lock held by request B"
    final = _assert_submitted_once(article, editor_revision, date_submitted, results)
    assert "This submission has already been completed." in results["A_messages"]
    assert final["review_rounds"] == 2
    assert final["editor_assignments"] == 1


@pytest.mark.parametrize("attempt", range(3))
@pytest.mark.django_db(transaction=True)
def test_free_race_double_submit(editor_revision: EditorRevisionRequest, access_mode_oa, settings, attempt):
    """Two POSTs released together, with no forced interleaving."""
    article = editor_revision.article
    _prepare_revision(article)
    date_submitted = article.date_submitted
    barrier = threading.Barrier(2)
    results = {}

    def submit(key):
        barrier.wait()
        _post_step8(article, settings, results, key)

    threads = [threading.Thread(target=submit, args=(key,), name=f"request-{key}") for key in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)

    _assert_submitted_once(article, editor_revision, date_submitted, results)


@pytest.mark.django_db(transaction=True)
def test_sequential_resubmit_redirects(editor_revision: EditorRevisionRequest, access_mode_oa, settings):
    """A late resubmission (e.g. from the browser history) is sent to the status page with a message."""
    article = editor_revision.article
    _prepare_revision(article)
    date_submitted = article.date_submitted
    results = {}

    _post_step8(article, settings, results, "A")
    _post_step8(article, settings, results, "B")

    _assert_submitted_once(article, editor_revision, date_submitted, results)
    assert "This submission has already been completed." in results["B_messages"]
