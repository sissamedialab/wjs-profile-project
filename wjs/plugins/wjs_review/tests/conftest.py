import glob
import os
from io import BytesIO

import pytest
from core import files
from core import models as core_models
from core.models import Account, File, SupplementaryFile, Workflow, WorkflowElement
from django.core import mail
from django.core.files import File as DjangoFile
from django.core.management import call_command
from django.http import HttpRequest
from events import logic as events_logic
from plugins.typesetting.models import GalleyProofing
from plugins.wjs_review.reminders.settings import reminders_configuration
from review import models as review_models
from review.models import ReviewAssignment
from submission.models import Article
from utils import setting_handler

from wjs.jcom_profile.tests.conftest import *  # noqa

from ..events import ReviewEvent
from ..logic import (
    AssignToEditor,
    AssignTypesetter,
    HandleDecision,
    VerifyProductionRequirements,
)
from ..models import ArticleWorkflow, EditorRevisionRequest, Message, Reminder
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
    set_default_plugin_settings(force=True)
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


def _assign_article(fake_request, article, section_editor):
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
    return _assign_article(fake_request, article, section_editor)


def _accept_article(
    fake_request: HttpRequest,
    article: Article,
) -> ArticleWorkflow:
    form_data = {
        "decision": ArticleWorkflow.Decisions.ACCEPT,
        "decision_editor_report": "Some editor report",
        "decision_internal_note": "Some internal note",
        "withdraw_notice": "Some withdraw notice",
    }
    assert fake_request.user is not None
    editor_decision = HandleDecision(
        workflow=article.articleworkflow,
        form_data=form_data,
        user=fake_request.user,
        request=fake_request,
    ).run()
    workflow = editor_decision.workflow
    # An accepted article can be moved to READY_FOR_TYPESETTER (most common case) or be left in ACCEPTED state if there
    # are issues that must be resolved before the paper is ready for tyepsetters.
    assert workflow.state in (
        ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
        ArticleWorkflow.ReviewStates.ACCEPTED,
    )
    cleanup_notifications_side_effects()
    return workflow.article


@pytest.fixture
def accepted_article(fake_request, assigned_article) -> ArticleWorkflow:
    """Create and return an accepted article.

    See notes about notifications in `assigned_article`.

    Remember that accepted != ready-for-typesetter
    """
    if fake_request.user is None:
        # This can happen when this fixture is called by other fixtures
        # In this case it should be safe to assume that the editor assigned to the article is performing the acceptance
        # (which is the most common case)
        fake_request.user = assigned_article.editorassignment_set.last().editor
    return _accept_article(fake_request, assigned_article)


def _ready_for_typesetter_article(article) -> ArticleWorkflow:
    workflow = article.articleworkflow
    if workflow.state == ArticleWorkflow.ReviewStates.ACCEPTED:
        workflow = VerifyProductionRequirements(articleworkflow=workflow).run()
    assert workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    cleanup_notifications_side_effects()
    return workflow.article


@pytest.fixture
def ready_for_typesetter_article(accepted_article) -> ArticleWorkflow:
    """Create and return an ready_for_typed article.

    See notes about notifications in `assigned_article`.
    """
    return _ready_for_typesetter_article(accepted_article)


def _assigned_to_typesetter_article(
    article: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> Article:
    typesetting_assignment = AssignTypesetter(article, typesetter, fake_request).run()
    workflow = typesetting_assignment.round.article.articleworkflow
    assert workflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
    cleanup_notifications_side_effects()
    return workflow.article


@pytest.fixture
def assigned_to_typesetter_article(
    ready_for_typesetter_article: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> ArticleWorkflow:
    """Create and return an article assigned to a typesetter.

    See notes about notifications in `assigned_article`.
    """
    return _assigned_to_typesetter_article(ready_for_typesetter_article, typesetter, fake_request)


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


@pytest.fixture
def create_set_of_articles_with_assignments(
    fake_request: HttpRequest,
    eo_user: Account,
    journal: journal_models.Journal,  # noqa
    director: Account,
    review_settings,
):
    """
    Create a set of articles with assignments using scenario_review command.

    It's a bit heavy in terms of time of execution but it's the most reliable way to have a significant data set
    for testing the managers and the queries.
    """
    # TODO: Using scenario_review has two drawbacks:
    #  - it's slow
    #  - it ties the tests to a command meant more for local develoment than test purposes
    #  In the future we must evaluate if it's possible to replace this fixture with a more targeted one.
    #  For now it's too much work for little benefit and we must handle other tasks.
    call_command("scenario_review")


@pytest.fixture
def editor_revision(assigned_article: Article, fake_request: HttpRequest) -> EditorRevisionRequest:
    """Return the revision of the article that is in the editor's hands."""
    decision = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data={
            "decision": ArticleWorkflow.Decisions.MAJOR_REVISION,
            "decision_editor_report": "skip",
            "decision_internal_note": "skip",
            "date_due": "2024-01-01",
            "withdraw_notice": "automatic",
        },
        user=assigned_article.editorassignment_set.first().editor,
        request=fake_request,
    ).run()
    revision_request = decision.review_round.editorrevisionrequest_set.first()
    file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{assigned_article.pk}_new.pdf")
    assigned_article.manuscript_files.set([file_obj])
    file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{assigned_article.pk}_new.png")
    assigned_article.data_figure_files.set([file_obj])
    file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{assigned_article.pk}_new.txt")
    assigned_article.supplementary_files.set([SupplementaryFile.objects.create(file=file_obj)])
    return revision_request


def _create_supplementary_files(
    article: Article,
    author: Account,
    n: int = 1,
):
    """Nomen Omen."""
    for i in range(n):
        # TODO: conftest fixture
        supplementary_dj = DjangoFile(BytesIO(b"ciao"), f"ESM_file_{i}.txt")
        supplementary_file = files.save_file_to_article(
            supplementary_dj,
            article,
            author,
        )
        supplementary_file.label = "ESM LABEL"
        supplementary_file.description = "Supplementary file description"
        supplementary_file.save()
        supp_file = core_models.SupplementaryFile.objects.create(file=supplementary_file)
        article.supplementary_files.add(supp_file)
    return supp_file


# Could have added this in the method above but supplementary files are handled slightly different. Could RFC this.
def _create_article_files(
    article: Article,
    author: Account,
    n: int = 1,
):
    """Nomen Omen."""
    file_types = {
        "manuscript_files": b"manuscript content",
        "data_figure_files": b"data figure content",
        "source_files": b"source content",
    }

    for file_category, file_content_bytes in file_types.items():
        for i in range(n):
            file_name = f"{file_category[:-1]}_{i}.txt"
            file_data = BytesIO(file_content_bytes)
            django_file = DjangoFile(file_data, file_name)

            file_instance = files.save_file_to_article(
                django_file,
                article,
                author,
            )
            getattr(article, file_category).add(file_instance)


def _create_galleyproofing_proofed_files(
    article: Article,
    author: Account,
    proofing_assignment: GalleyProofing,
    n: int = 1,
):
    """Nomen Omen."""

    for i in range(n):
        for file_type in ["PDF", "epub", "html"]:
            galley_dj = DjangoFile(BytesIO(b"ciao"), f"Galley_{i}.{file_type}")
            galley_file = files.save_file_to_article(
                galley_dj,
                article,
                author,
            )
            galley_file.label = f"{file_type}"
            galley_file.description = f"{file_type} galley description"
            galley_file.save()
            galley = core_models.Galley.objects.create(
                file=galley_file,
                article=article,
            )
            proofing_assignment.proofed_files.add(galley)

            if file_type == "html":
                article.render_galley = galley
                article.save()
