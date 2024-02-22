import glob
import os

import pytest  # noqa
from core.models import Workflow, WorkflowElement
from django.core import mail
from django.http import HttpRequest
from events import logic as events_logic
from plugins.wjs_review.reminders.settings import reminders_configuration
from review import models as review_models
from review.models import ReviewAssignment
from utils import setting_handler  # noqa

from wjs.jcom_profile.tests.conftest import *  # noqa

from ..events import ReviewEvent
from ..logic import AssignToEditor
from ..models import ArticleWorkflow, Message, Reminder
from ..plugin_settings import (
    HANDSHAKE_URL,
    SHORT_NAME,
    STAGE,
    set_default_plugin_settings,
)
from .test_helpers import _create_review_assignment

TEST_FILES_EXTENSION = ".santaveronica"


def cleanup_notifications_side_effects():
    """Clean up messages and notifications."""
    mail.outbox = []
    Message.objects.all().delete()


@pytest.fixture
def review_settings(journal, eo_user):
    """
    Initialize plugin settings and install wjs_review as part of the workflow.

    It must be declared as first fixture in the test function to ensure it's called before the other fixtures.
    """
    set_default_plugin_settings()
    # TODO: use plugin_settings.ensure_workflow_elements ?
    workflow = Workflow.objects.get(journal=journal)
    workflow.elements.filter(element_name="review").delete()
    workflow.elements.add(
        WorkflowElement.objects.create(
            element_name=SHORT_NAME,
            journal=journal,
            order=0,
            stage=STAGE,
            handshake_url=HANDSHAKE_URL,
        ),
    )


@pytest.fixture
def assigned_article(fake_request, article, section_editor, review_settings):
    """
    Assign an editor to an article.

    By default the assignment creates notifications (one mail and one message), and this can give problems
    in the tests using this fixture, because they have to distinguish between these notifications and the
    ones that are to be checked during the test itself.

    Calling the cleanup_notifications_side_effects() function here will remove the AssignToEditor() mail and
    message, so that the test using this fixture can check the notifications created *during* the test without
    interferences and without knowing the side effects of the fixture or of AssignToEditor().
    """
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()
    workflow = AssignToEditor(
        article=article,
        editor=section_editor,
        request=fake_request,
    ).run()
    assert workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    cleanup_notifications_side_effects()
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


@pytest.fixture
def known_reminders_configuration():
    """A fixture that sets the reminders configuration to known values."""
    # Using a fixture instead than mocking the dictionary for easier reuse
    # (and also because I'm not very confortable with mock ;)
    configuration = reminders_configuration["DEFAULT"]

    # Store old config in a dictionary using the reminder code as a key that points to a tuple of (old, mine) values
    tmp_config = {
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1: (
            configuration[Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1].days_after,
            4,
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2: (
            configuration[Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2].days_after,
            7,
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3: (
            configuration[Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3].days_after,
            9,
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1: (
            configuration[Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1].days_after,
            7,
        ),
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2: (
            configuration[Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2].days_after,
            14,
        ),
    }
    # At the time of writing I also had:
    # - configuration[Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1].days_after = 5
    # - configuration[Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2].days_after = 8
    # - configuration[Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3].days_after = 10
    # - configuration[Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1].days_after = 5
    # - configuration[Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2].days_after = 8
    # - configuration[Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3].days_after = 10

    for reminder_code, values in tmp_config.items():
        my_value = values[1]
        configuration[reminder_code].days_after = my_value
    yield
    for reminder_code, values in tmp_config.items():
        old_value = values[0]
        configuration[reminder_code].days_after = old_value
