import pytest  # noqa
from django.http import HttpRequest
from events import logic as events_logic
from review import models as review_models
from review.models import ReviewAssignment
from utils import setting_handler  # noqa

from wjs.jcom_profile.tests.conftest import *  # noqa

from ..events import ReviewEvent
from ..logic import AssignToEditor, AssignToReviewer
from ..models import ArticleWorkflow
from ..plugin_settings import set_default_plugin_settings


@pytest.fixture
def review_settings():
    set_default_plugin_settings()


@pytest.fixture
def assigned_article(fake_request, article, section_editor):
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()
    workflow = AssignToEditor(
        article=article,
        editor=section_editor,
        request=fake_request,
    ).run()
    assert workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    return workflow.article


@pytest.fixture
def submitted_workflow(
    journal: journal_models.Journal,  # noqa
    create_submitted_articles: Callable,  # noqa
) -> ArticleWorkflow:
    article = create_submitted_articles(journal, count=1)[0]
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
    article.articleworkflow.save()
    return article.articleworkflow


@pytest.fixture
def review_form(journal):
    review_form = review_models.ReviewForm(name="A Form", slug="A Slug", intro="i", thanks="t", journal=journal)
    review_form.save()

    review_form_element, __ = review_models.ReviewFormElement.objects.get_or_create(
        name="Review",
        kind="text",
        order=1,
        width="full",
        required=True,
    )
    review_form.elements.add(review_form_element)
    setting_handler.save_setting(
        "general",
        "default_review_form",
        journal,
        review_form_element.pk,
    )


@pytest.fixture
def review_assignment(
    fake_request: HttpRequest,
    invited_user: JCOMProfile,  # noqa: F405
    assigned_article: submission_models.Article,  # noqa: F405
    review_form: review_models.ReviewForm,
    review_settings,
) -> ReviewAssignment:
    editor = assigned_article.editorassignment_set.first().editor
    fake_request.user = editor
    assign_service = AssignToReviewer(
        reviewer=invited_user.janeway_account,
        workflow=assigned_article.articleworkflow,
        editor=editor,
        form_data={"message": "Message from fixture"},
        request=fake_request,
    )
    return assign_service.run()


@pytest.fixture
def with_no_hooks_for_on_article_workflow_submitted():
    """Disable ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED hook to skip chained events."""
    old_setting = events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED]
    events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED] = []
    yield
    events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED] = old_setting
