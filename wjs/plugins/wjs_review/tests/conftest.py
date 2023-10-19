import glob
import os

import pytest  # noqa
from core.models import Workflow, WorkflowElement
from django.http import HttpRequest
from events import logic as events_logic
from review import models as review_models
from review.models import ReviewAssignment
from utils import setting_handler  # noqa

from wjs.jcom_profile.tests.conftest import *  # noqa

from ..events import ReviewEvent
from ..logic import AssignToEditor
from ..models import ArticleWorkflow
from ..plugin_settings import HANDSHAKE_URL, SHORT_NAME, set_default_plugin_settings
from .test_helpers import _create_review_assignment

TEST_FILES_EXTENSION = ".santaveronica"


@pytest.fixture
def review_settings(journal):
    """
    Initialize plugin settings and install wjs_review as part of the workflow.

    It must be declared as first fixture in the test function to ensure it's called before the other fixtures.
    """
    set_default_plugin_settings()
    workflow = Workflow.objects.get(journal=journal)
    workflow.elements.filter(element_name="review").delete()
    workflow.elements.add(
        WorkflowElement.objects.create(
            element_name=SHORT_NAME,
            journal=journal,
            order=0,
            stage="wjs_review",
            handshake_url=HANDSHAKE_URL,
        ),
    )


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
def review_form(journal) -> review_models.ReviewForm:
    current_setting = setting_handler.get_setting(
        "general",
        "default_review_form",
        journal,
    ).value
    if current_setting:
        return review_models.ReviewForm.objects.get(pk=current_setting)
    else:
        review_form = review_models.ReviewForm.objects.create(
            name="A Form",
            slug="A Slug",
            intro="i",
            thanks="t",
            journal=journal,
        )

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
        return review_form


@pytest.fixture
def review_assignment(
    fake_request: HttpRequest,
    invited_user: JCOMProfile,  # noqa: F405
    assigned_article: submission_models.Article,  # noqa: F405
    review_form: review_models.ReviewForm,
    review_settings,
) -> ReviewAssignment:
    return _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=invited_user,
        assigned_article=assigned_article,
    )


@pytest.fixture
def with_no_hooks_for_on_article_workflow_submitted():
    """Disable ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED hook to skip chained events."""
    old_setting = events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED]
    events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED] = []
    yield
    events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED] = old_setting


@pytest.fixture
def cleanup_test_files_from_folder_files(settings):
    """Remove all files with extension .santaveronica from src/files."""
    yield
    for f in glob.glob(f"{settings.BASE_DIR}/files/**/*{TEST_FILES_EXTENSION}", recursive=True):
        os.unlink(f)
