import pytest
from django.http import HttpRequest
from django.utils import timezone
from events import logic as events_logic
from identifiers import models as identifiers_models
from submission import models as submission_models
from utils import setting_handler

from wjs.jcom_profile.models import JCOMProfile

from ..events import ReviewEvent
from ..models import ArticleWorkflow, WjsEditorAssignment
from ..plugin_settings import STAGE
from .conftest import _accept_article


@pytest.mark.django_db
def test_unsubmitted_article(article: submission_models.Article):
    """Article in unsubmitted state should have a workflow in INCOMPLETE_SUBMISSION state."""
    assert article.stage == submission_models.STAGE_UNSUBMITTED
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION


@pytest.mark.django_db
def test_submitted_article(
    review_settings,
    article: submission_models.Article,
    fake_request: HttpRequest,
    coauthors_setting,
    director: JCOMProfile,
    with_no_hooks_for_on_article_workflow_submitted,
):
    """When an article is submitted, the workflow is moved to submitted state."""
    assert article.stage == submission_models.STAGE_UNSUBMITTED
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION

    # mimics submission.views.submit_review to complete the submission of an article
    kwargs = {"article": article, "request": fake_request}
    article.date_submitted = timezone.now()
    article.stage = STAGE
    article.current_step = 5
    article.save()
    events_logic.Events.raise_event(events_logic.Events.ON_ARTICLE_SUBMITTED, task_object=article, **kwargs)

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.SUBMITTED


@pytest.mark.django_db
def test_submitted_workflow(
    review_settings,
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    coauthors_setting,
    director: JCOMProfile,
):
    """When an article is submitted, the workflow is moved to submitted state."""
    assert submitted_workflow.article.stage == submission_models.STAGE_UNASSIGNED
    assert submitted_workflow.state == ArticleWorkflow.ReviewStates.SUBMITTED
    events_logic.Events.raise_event(
        ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED,
        task_object=submitted_workflow,
        **{"workflow": submitted_workflow},
    )
    submitted_workflow.refresh_from_db()
    assert submitted_workflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED


@pytest.mark.django_db
def test_submitted_workflow_issues(
    review_settings,
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    coauthors_setting,
    director: JCOMProfile,
    settings,
):
    """When an article is submitted and it does not pass checks, the workflow is moved to paper might have issues."""
    settings.WJS_REVIEW_CHECK_FUNCTIONS = {
        submitted_workflow.article.journal.code: ["wjs_review.events.checks.always_reject"],
    }
    events_logic.Events.raise_event(
        ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED,
        task_object=submitted_workflow,
        **{"workflow": submitted_workflow},
    )
    submitted_workflow.refresh_from_db()
    assert submitted_workflow.state == ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES


@pytest.mark.parametrize(
    "function_name, expected_state",
    (
        ("wjs_review.events.checks.always_reject", ArticleWorkflow.ReviewStates.ACCEPTED),
        ("wjs_review.events.checks_after_acceptance.always_pass", ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER),
    ),
)
@pytest.mark.django_db
def test_accepted_workflow_issues(
    assigned_article: submission_models.Article,
    fake_request: HttpRequest,
    director: JCOMProfile,
    settings,
    function_name,
    expected_state,
):
    """When an article is accepted, checks are run.

    - if they pass, article state is bumped from accepted to ready-for-typ
    - if they don't pass, article state is not changed
    """
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {
        assigned_article.journal.code: [function_name],
    }
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    # Signal is emitted on acceptance and check functions should run.
    # Even if the checks fail, the logic class should not raise any exception.
    _accept_article(fake_request, assigned_article)
    # TODO: check for a message to the EO after approach is validated

    assigned_article.refresh_from_db()
    assert assigned_article.articleworkflow.state == expected_state


@pytest.mark.django_db
def test_always_accept(submitted_workflow: ArticleWorkflow):
    """Always accept function should always return True."""
    from ..events.checks import always_accept

    assert always_accept(submitted_workflow.article) is True


@pytest.mark.django_db
def test_always_decline(submitted_workflow: ArticleWorkflow):
    """Always decline function should always return False."""
    from ..events.checks import always_reject

    assert always_reject(submitted_workflow.article) is False


@pytest.mark.django_db
def test_one_author_or_more_reject(submitted_workflow: ArticleWorkflow):
    """at_least_one_author return False if no author is set."""
    from ..events.checks import at_least_one_author

    submitted_workflow.article.authors.clear()
    assert at_least_one_author(submitted_workflow.article) is False


@pytest.mark.django_db
def test_one_author_or_more_accept(submitted_workflow: ArticleWorkflow, create_jcom_user):
    """at_least_one_author return True if at least one author is set."""
    from ..events.checks import at_least_one_author

    another_author = create_jcom_user()
    submitted_workflow.article.authors.add(another_author)
    assert submitted_workflow.article.authors.exists()
    assert at_least_one_author(submitted_workflow.article) is True


@pytest.mark.skipif("not config.getoption('--run-academic')", reason="See wjs-profile-projects#197")
@pytest.mark.django_db
def test_identifiers_at_acceptance(
    assigned_article: submission_models.Article,
    fake_request: HttpRequest,
    director: JCOMProfile,
):
    """When an article is accepted with production settings, check if identifiers:

    - preprintid: not changed
    - doi: created
    - pubid: not created
    """

    test_preprintid = "TEST_PREPRINTID"

    # production settings and crossref_test True
    setting_handler.save_setting(
        "Identifiers",
        "use_crossref",
        journal=assigned_article.journal,
        value=True,
    )
    setting_handler.save_setting(
        "Identifiers",
        "crossref_test",
        journal=assigned_article.journal,
        value=True,
    )
    setting_handler.save_setting(
        "Identifiers",
        "register_doi_at_acceptance",
        journal=assigned_article.journal,
        value=False,
    )

    assert assigned_article.journal.use_crossref
    assert setting_handler.get_setting(
        "Identifiers",
        "crossref_test",
        journal=assigned_article.journal,
    ).processed_value
    assert not assigned_article.journal.register_doi_at_acceptance

    identifiers_models.Identifier.objects.create(
        identifier=test_preprintid,
        article=assigned_article,
        id_type="preprintid",  # NOT a member of the set identifiers_models.IDENTIFIER_TYPES
        enabled=True,
    )

    assert assigned_article.get_pubid() is None
    assert assigned_article.get_doi() is None
    assert assigned_article.get_identifier("preprintid") == test_preprintid

    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    _accept_article(fake_request, assigned_article)
    assigned_article.refresh_from_db()

    assert assigned_article.get_pubid() is None
    assert isinstance(assigned_article.get_doi(), str) and assigned_article.get_doi()
    assert assigned_article.get_identifier("preprintid") == test_preprintid
    assert identifiers_models.Identifier.objects.filter(article=assigned_article).count() == 2
