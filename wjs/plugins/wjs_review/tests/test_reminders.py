import datetime
import logging
from typing import Callable, Iterable, Optional, Type
from unittest import mock

import freezegun
import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import models
from django.http import HttpRequest
from django.utils import timezone
from journal import models as journal_models
from review import models as review_models
from submission import models as submission_models

from wjs.jcom_profile.constants import DIRECTOR_MAIN_ROLE, DIRECTOR_ROLE
from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import render_template

from ..communication_utils import get_eo_user, update_date_send_reminders
from ..conditions import any_reviewer_is_late_after_reminder
from ..logic import (
    AssignToEditor,
    AssignToReviewer,
    EvaluateReview,
    HandleDecision,
    HandleEditorDeclinesAssignment,
    SubmitReview,
)
from ..models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from ..reminders.settings import (
    AuthorShouldSubmitMajorRevisionReminderManager,
    AuthorShouldSubmitMinorRevisionReminderManager,
    AuthorShouldSubmitTechnicalRevisionReminderManager,
    DirectorShouldAssignEditorReminderManager,
    EditorShouldSelectReviewerReminderManager,
    ReminderManager,
    ReviewerShouldEvaluateAssignmentReminderManager,
    ReviewerShouldWriteReviewReminderManager,
)
from ..utils import get_report_form
from . import test_helpers
from .test_helpers import jcom_report_form_data


def test_render_template():
    """Test the simple render_template() function."""
    result = render_template("-{{ aaa }}-", {"aaa": "AAA"})
    assert result == "-AAA-"


def check_reminder_date(
    target_object: models.Model,
    manager: Type[ReminderManager],
    codes: Iterable[str],
    base_date: datetime.date,
    journal: journal_models.Journal,
    overall_count_matches: bool = True,
):
    """
    Generic function to test the date of reminders.

    Asserts that the expected reminders are created and that their due date is correct.

    :param target_object: the object for which the reminders are created
    :param manager: the review settings manager to test
    :param codes: the list of codes of the reminders to check
    :param base_date: the date from which to calculate the due date
    :param journal: current journal
    :param overall_count_matches: if True, the total number of reminders must match the length of `codes`
    """
    reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(target_object),
        object_id=target_object.pk,
    )
    if overall_count_matches:
        assert reminders.count() == len(codes)
        assert reminders.filter(code__in=codes).count() == len(codes)
    for code in codes:
        offset_date = manager.reminders[code].days_after
        date_by_settings = manager.reminders[code]._get_date_base_setting(journal)
        if date_by_settings:
            offset_date += date_by_settings
        if reminder := reminders.get(code=code):
            assert reminder.date_due == base_date + datetime.timedelta(days=offset_date)


@pytest.mark.django_db
def test_create_a_reminder(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test the auxiliary function that creates reminders."""
    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )
    # Ugly hack: create_reminder needs a service already "half-run", because the target is one of the results of the
    # processing (e.g. a WorkflowReviewAssignment). However, the `run()` method will call create_reminder itself.
    service._ensure_reviewer()
    service.assignment = service._assign_reviewer()

    ReviewerShouldEvaluateAssignmentReminderManager(
        assignment=service.assignment,
    ).create()

    reminder_obj = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1)

    # Remember that the fixture `assigned_article` creates the EDITOR_SHOULD_SELECT_REVIEWER reminders
    reminders = Reminder.objects.filter(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1)
    assert reminders.count() == 1
    assert reminders.first() == reminder_obj

    # Somewhat weak test that the subject has been rendered
    reminder_setting = ReviewerShouldEvaluateAssignmentReminderManager.get_settings(reminder_obj)
    # Needs coercion to string because the subject is a lazy translation
    assert str(reminder_setting.subject) in reminder_obj.message_subject

    assert reminder_obj.recipient == service.reviewer
    assert reminder_obj.actor == section_editor.janeway_account


@pytest.mark.parametrize("set_main_director", (True, False))
@pytest.mark.django_db
def test_reminder_to_single_director(
    fake_request: HttpRequest,
    director: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    create_jcom_user: Callable,
    set_main_director,
):
    """Assignment to director only assign to the main director even if other are available."""
    new_director = create_jcom_user("New Director")
    new_director.add_account_role(DIRECTOR_ROLE, assigned_article.journal)
    if set_main_director:
        new_director.add_account_role(DIRECTOR_MAIN_ROLE, assigned_article.journal)
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    fake_request.user = assignment.editor
    HandleEditorDeclinesAssignment(
        assignment=assignment,
        editor=assignment.editor,
        request=fake_request,
    ).run()
    assert Reminder.objects.all().count() == 2
    assert Reminder.objects.filter(code=Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_1).count() == 1
    assert Reminder.objects.filter(code=Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2).count() == 1
    for reminder in Reminder.objects.all():
        if set_main_director:
            assert reminder.recipient == new_director.janeway_account
        else:
            assert reminder.recipient == director.janeway_account


@pytest.mark.django_db
def test_assign_reviewer_creates_reminders(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test that when a reviewer is assigned, reviwer reminders are created and editor reminders are deleted."""
    fake_request.user = section_editor.janeway_account

    acceptance_due_date = timezone.now().date() + datetime.timedelta(days=7)

    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": acceptance_due_date,
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )

    assert Reminder.objects.count() == 3
    editor_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(WjsEditorAssignment.objects.get_current(assigned_article)),
        object_id=WjsEditorAssignment.objects.get_current(assigned_article).id,
    )
    assert editor_reminders.count() == 3
    assert all("EDSR" in code for code in editor_reminders.values_list("code", flat=True))

    reviewer_assignment = service.run()
    assert Reminder.objects.count() == 3
    reviewer_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(reviewer_assignment),
        object_id=reviewer_assignment.id,
    )
    assert reviewer_reminders.count() == 3
    assert all("REEA" in code for code in reviewer_reminders.values_list("code", flat=True))

    r_1_date = ReviewerShouldEvaluateAssignmentReminderManager.reminders[
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1
    ].days_after
    r_2_date = ReviewerShouldEvaluateAssignmentReminderManager.reminders[
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2
    ].days_after
    r_3_date = ReviewerShouldEvaluateAssignmentReminderManager.reminders[
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3
    ].days_after
    r_1 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1)
    assert r_1.actor == service.editor
    assert r_1.recipient == service.reviewer
    assert r_1_date == 0
    assert r_1.date_due == acceptance_due_date

    r_2 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2)
    assert r_2.actor == service.editor
    assert r_2.recipient == service.reviewer
    assert r_2.date_due == acceptance_due_date + datetime.timedelta(days=r_2_date)

    r_3 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3)
    assert r_3.actor == get_eo_user(assigned_article.journal)
    assert r_3.recipient == service.editor
    assert r_3.date_due == acceptance_due_date + datetime.timedelta(days=r_3_date)


@pytest.mark.django_db
def test_reminders_know_their_article(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test reminders can refer to their own article."""
    # TODO: this test is weak: it tries only one batch of reminders for the same article.
    # We should
    # - test another batch for a different article (same action)
    # - test a batch for a every action
    fake_request.user = section_editor.janeway_account

    # Let's delete all reminders (e.g. those for the editor created by the `assigned_article` fixture), so that we can
    # test what comes next easily.
    Reminder.objects.all().delete()

    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )

    assert not Reminder.objects.exists()
    service.run()
    assert Reminder.objects.count() == 3
    for reminder in Reminder.objects.all():
        assert reminder.get_related_article() == assigned_article


@pytest.mark.django_db
def test_send_reminders__no_reminders(
    review_settings,
    caplog,
):
    """Test sending overdue reminders.

    No reminders are sent if there is no reminder to send.
    """
    caplog.set_level(logging.DEBUG)
    call_command("send_wjs_reminders")
    assert "Sent 0/0 reminders." in caplog.text


@pytest.mark.django_db
def test_send_reminders__simple_case(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    caplog,
):
    """Test reminders can refer to their own article."""
    # TODO: this test is weak: it tries only one batch of reminders for the same article.
    # We should
    # - test another batch for a different article (same action)
    # - test a batch for every action
    # - specifically test a date (a day before, the same day, a day after)
    fake_request.user = section_editor.janeway_account

    # Let's delete all reminders (e.g. those for the editor created by the `assigned_article` fixture), so that we can
    # test what comes next easily.
    Reminder.objects.all().delete()

    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )

    assert not Reminder.objects.exists()
    service.run()
    assert Reminder.objects.count() == 3
    last_reminder = Reminder.objects.all().order_by("date_due").last()

    the_day_after = last_reminder.date_due + timezone.timedelta(days=1)

    with caplog.at_level(logging.DEBUG):
        with freezegun.freeze_time(the_day_after):
            call_command("send_wjs_reminders")
            assert "Sent 3/3 reminders." in caplog.text


@pytest.mark.django_db
def test_reviewer_accepts__deletes_some_reminders(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test that when a reviewer accepts an assignments the ESR reminders for that assignment are deleted."""
    fake_request.user = section_editor.janeway_account

    # Let's delete all reminders (e.g. those for the editor created by the `assigned_article` fixture), so that we can
    # test what comes next easily.
    Reminder.objects.all().delete()

    acceptance_due_date = timezone.now().date() + datetime.timedelta(days=7)

    service__assign_reviewer = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": acceptance_due_date,
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )

    assert not Reminder.objects.exists()
    service__assign_reviewer.run()
    assert Reminder.objects.count() == 3

    service__evaluate_review = EvaluateReview(
        assignment=service__assign_reviewer.assignment,
        reviewer=service__assign_reviewer.reviewer,
        editor=service__assign_reviewer.editor,
        form_data={"reviewer_decision": "1"},
        request=fake_request,
        token="",
    )

    r_1_date = ReviewerShouldWriteReviewReminderManager.reminders[
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1
    ].days_after
    r_2_date = ReviewerShouldWriteReviewReminderManager.reminders[
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2
    ].days_after

    service__evaluate_review.run()
    assert Reminder.objects.count() == 2
    r_1 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1)
    assert r_1.actor == service__evaluate_review.editor
    assert r_1.recipient == service__evaluate_review.reviewer
    assert r_1.date_due == acceptance_due_date + datetime.timedelta(days=r_1_date)
    r_2 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2)
    assert r_2.actor == get_eo_user(assigned_article.journal)
    assert r_2.recipient == service__evaluate_review.editor
    assert r_2.date_due == acceptance_due_date + datetime.timedelta(days=r_2_date)


class TestReviewerDeclines:
    """What happens when a reviewer declines an assignment.

    See https://gitlab.sissamedialab.it/wjs/specs/-/issues/619#implementation-details

    Different situations that require different behavior:
    - at least one assigment completed (i.e. there is a review)
    - no other assignement completed (i.e. there are no review)
      - at least one assignment pending (i.e. the paper is assigned to at least one reviewer)
      - no other assignement pending
    """

    @pytest.mark.django_db
    def test__one_other_assignment_completed(
        self,
        fake_request: HttpRequest,
        section_editor: JCOMProfile,
        director: JCOMProfile,
        normal_user: JCOMProfile,
        create_jcom_user: Callable[[Optional[str]], JCOMProfile],
        assigned_article: submission_models.Article,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    ):
        """Test that reminders for the reviewer are deleted and reminders for the editor are created."""
        # TODO: remove me! Sanity check: (twin with test_paper_assignment_create_reminders_for_editor)
        editor_assignment: WjsEditorAssignment = WjsEditorAssignment.objects.get(
            article=assigned_article,
            editor=section_editor,
        )
        editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
            code__in=[
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            ],
        )
        assert editor_reminders.count() == 3

        fake_request.user = section_editor.janeway_account
        review_assignment = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=normal_user.janeway_account,
            editor=section_editor.janeway_account,
            form_data={
                "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()

        # Sanity check: we should now have no reminders for the editor and three for the reviewer
        assert (
            Reminder.objects.filter(
                content_type=ContentType.objects.get_for_model(review_assignment),
                object_id=review_assignment.id,
                code__in=[
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
                ],
            ).count()
            == 3
        )
        assert Reminder.objects.all().count() == 3

        # Let's assign to another reviewer and let this reviewer complete the review
        bernardos_review_assignment = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=create_jcom_user("Bernardo Da Corleone").janeway_account,
            editor=section_editor.janeway_account,
            form_data={
                "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()
        test_helpers._submit_review(
            review_assignment=bernardos_review_assignment,
            review_form=review_form,
            fake_request=fake_request,
        )

        # Now we go back to the first reviewer, who is a bad person and declines our kind request 😠
        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            form_data={"reviewer_decision": "0"},
            request=fake_request,
            token="",
        ).run()

        # This one reviewer declined the assignement _but_ Bernardo completed his review.
        # There should be _no_ reminders for the editor yet.
        new_editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
            code__in=[
                Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
            ],
        )
        assert new_editor_reminders.count() == 3

    @pytest.mark.django_db
    def test__no_other_assignment_completed__one_pending(
        self,
        fake_request: HttpRequest,
        section_editor: JCOMProfile,
        director: JCOMProfile,
        normal_user: JCOMProfile,
        create_jcom_user: Callable[[Optional[str]], JCOMProfile],
        assigned_article: submission_models.Article,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    ):
        """Test that reminders for the reviewer are deleted and reminders for the editor are _not_ created."""
        # TODO: remove me! Sanity check: (twin with test_paper_assignment_create_reminders_for_editor)
        editor_assignment: WjsEditorAssignment = WjsEditorAssignment.objects.get(
            article=assigned_article,
            editor=section_editor,
        )
        editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
            code__in=[
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            ],
        )
        assert editor_reminders.count() == 3

        fake_request.user = section_editor.janeway_account
        review_assignment = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=normal_user.janeway_account,
            editor=section_editor.janeway_account,
            form_data={
                "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()

        # Sanity check: we should now have no reminders for the editor and three for the reviewer
        assert (
            Reminder.objects.filter(
                content_type=ContentType.objects.get_for_model(review_assignment),
                object_id=review_assignment.id,
                code__in=[
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
                ],
            ).count()
            == 3
        )
        assert Reminder.objects.all().count() == 3

        # Let's assign to another reviewer and let this reviewer do nothing (i.e. we make another pending assignment)
        AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=create_jcom_user("Bernardo Da Corleone").janeway_account,
            editor=section_editor.janeway_account,
            form_data={
                "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()

        # Now we go back to the first reviewer, who is a bad person and declines our kind request 😠
        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            form_data={"reviewer_decision": "0"},
            request=fake_request,
            token="",
        ).run()

        # This one reviewer declined the assignement _but_ there still is Bernardo's assignment pending.
        # There should be no reminders for the editor.
        new_editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
            code__in=[
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            ],
        )
        assert new_editor_reminders.count() == 0

    @pytest.mark.django_db
    def test__no_other_assignment_completed__none_pending(
        self,
        fake_request: HttpRequest,
        section_editor: JCOMProfile,
        director: JCOMProfile,
        normal_user: JCOMProfile,
        assigned_article: submission_models.Article,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    ):
        """Test that reminders for the reviewer are deleted and reminders for the editor are created."""
        # Sanity check: (twin with test_paper_assignment_create_reminders_for_editor)
        editor_assignment: WjsEditorAssignment = WjsEditorAssignment.objects.get(
            article=assigned_article,
            editor=section_editor,
        )
        editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
            code__in=[
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            ],
        )
        assert editor_reminders.count() == 3

        fake_request.user = section_editor.janeway_account
        review_assignment = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=normal_user.janeway_account,
            editor=section_editor.janeway_account,
            form_data={
                "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()

        # Sanity check: we should now have no reminders for the editor and three for the reviewer
        assert (
            Reminder.objects.filter(
                content_type=ContentType.objects.get_for_model(review_assignment),
                object_id=review_assignment.id,
                code__in=[
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
                    Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
                ],
            ).count()
            == 3
        )
        assert Reminder.objects.all().count() == 3

        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            form_data={"reviewer_decision": "0"},
            request=fake_request,
            token="",
        ).run()

        # The only reviewer declined the assignement and there is nothing else.
        # There should be three "new" reminders for the editor
        new_editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
            code__in=[
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            ],
        )
        assert new_editor_reminders.count() == 3
        assert new_editor_reminders.order_by(
            "id",
        ).values_list(
            "id",
            flat=True,
        ) != editor_reminders.order_by(
            "id",
        ).values_list(
            "id",
            flat=True,
        )
        assert Reminder.objects.count() == 3


class TestReviewerSubmits:
    """What happens when a reviewer submits an assignment.

    We should create the editor-should-make-decision reminders only if there are not other pending assignments.

    """

    @pytest.mark.django_db
    def test__only_one_assignment(
        self,
        fake_request: HttpRequest,
        section_editor: JCOMProfile,
        director: JCOMProfile,
        normal_user: JCOMProfile,
        create_jcom_user: Callable[[Optional[str]], JCOMProfile],
        review_assignment: WorkflowReviewAssignment,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    ):
        """Test that reminders for the reviewer are deleted and reminders for the editor are created."""
        assigned_article = review_assignment.article
        # Sanity check:
        editor_assignment: WjsEditorAssignment = WjsEditorAssignment.objects.get(
            article=assigned_article,
            editor=section_editor,
        )
        editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
        )
        assert not editor_reminders.exists()

        reviewer_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.id,
        )
        # The reviewers has not yet accepted the assignment
        assert reviewer_reminders.count() == 3
        assert list(reviewer_reminders.order_by("code").values_list("code", flat=True)) == [
            Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1.value,
            Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2.value,
            Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3.value,
        ]

        # This is the interesting part: the reviewer submits his review and reminders magically change!
        fake_request.user = review_assignment.reviewer
        test_helpers._submit_review(review_assignment, review_form, fake_request, submit_final=True)

        # And these are the important tests:
        editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
        )
        assert editor_reminders.count() == 3
        assert list(editor_reminders.order_by("code").values_list("code", flat=True)) == [
            Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1.value,
            Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2.value,
            Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3.value,
        ]
        reviewer_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.id,
        )
        assert not reviewer_reminders.exists()

    @pytest.mark.django_db
    def test__two_assignment__first_declined(
        self,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
        director: JCOMProfile,
        create_jcom_user: Callable,
        assigned_article: submission_models.Article,
        fake_request: HttpRequest,
    ):
        """Test that editor-should-make-decision reminder are created only after the last review is submitted."""
        editor_assignment: WjsEditorAssignment = WjsEditorAssignment.objects.get(
            article=assigned_article,
        )
        editor = editor_assignment.editor
        fake_request.user = editor
        r1 = create_jcom_user("Rev Aone").janeway_account
        r2 = create_jcom_user("Rev Atwo").janeway_account
        review_assignment_r1 = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=r1,
            editor=editor,
            form_data={
                "acceptance_due_date": timezone.now().date(),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()
        review_assignment_r2 = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=r2,
            editor=editor,
            form_data={
                "acceptance_due_date": timezone.now().date(),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()

        # Now that we have two review assignments, let the first decline...
        EvaluateReview(
            assignment=review_assignment_r1,
            reviewer=r1,
            editor=editor,
            form_data={"reviewer_decision": "0"},
            request=fake_request,
            token="",
        ).run()
        assert not Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
        ).exists()

        # ... and not the interesting part: after the second reviewer submits his review, there is one submitted review
        # and no more pending reviews, so the editor-should-make-decision reminders are created.
        test_helpers._submit_review(review_assignment_r2, review_form, fake_request)
        assert (
            Reminder.objects.filter(
                content_type=ContentType.objects.get_for_model(editor_assignment),
                object_id=editor_assignment.id,
                code__in=[
                    Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1.value,
                    Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2.value,
                    Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3.value,
                ],
            ).count()
            == 3
        )

    @pytest.mark.django_db
    def test__two_assignment__first_submitted(
        self,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
        director: JCOMProfile,
        create_jcom_user: Callable,
        assigned_article: submission_models.Article,
        fake_request: HttpRequest,
    ):
        """Test editor-should-make-decision reminders are not created if there is a pending assignment."""
        editor_assignment: WjsEditorAssignment = WjsEditorAssignment.objects.get(
            article=assigned_article,
        )
        editor = editor_assignment.editor
        fake_request.user = editor
        r1 = create_jcom_user("Rev Aone").janeway_account
        r2 = create_jcom_user("Rev Atwo").janeway_account
        review_assignment_r1 = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=r1,
            editor=editor,
            form_data={
                "acceptance_due_date": timezone.now().date(),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()
        AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            reviewer=r2,
            editor=editor,
            form_data={
                "acceptance_due_date": timezone.now().date(),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()

        test_helpers._submit_review(review_assignment_r1, review_form, fake_request)
        assert (
            Reminder.objects.filter(
                content_type=ContentType.objects.get_for_model(editor_assignment),
                object_id=editor_assignment.id,
            ).count()
            == 0
        )


@pytest.mark.django_db
def test_paper_assignment_create_reminders_for_editor(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    director: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """Test that when a paper is assigned to an editor, reminders are created for the editor to select reviewers."""
    # The `assigned_article` fixture already performed the assignment, so we just check the reminders
    editor_assignment = WjsEditorAssignment.objects.get(article=assigned_article, editor=section_editor)
    reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(editor_assignment),
        object_id=editor_assignment.id,
    )
    create_date = timezone.localtime(timezone.now()).date()
    assert reminders.count() == 3
    reminder_1 = EditorShouldSelectReviewerReminderManager.reminders[
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1
    ]
    reminder_2 = EditorShouldSelectReviewerReminderManager.reminders[
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2
    ]
    reminder_3 = EditorShouldSelectReviewerReminderManager.reminders[
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3
    ]
    r_1_days_after = reminder_1.days_after
    r_1_base_days = reminder_1._get_date_base_setting(assigned_article.journal)
    r_2_days_after = reminder_2.days_after
    r_2_base_days = reminder_2._get_date_base_setting(assigned_article.journal)
    r_3_days_after = reminder_3.days_after
    r_3_base_days = reminder_3._get_date_base_setting(assigned_article.journal)

    r_1 = Reminder.objects.get(code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1)
    assert r_1.actor == get_eo_user(assigned_article.journal)
    assert r_1.recipient == section_editor.janeway_account
    assert r_1.date_due == create_date + datetime.timedelta(days=(r_1_days_after + r_1_base_days))

    r_2 = Reminder.objects.get(code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2)
    assert r_2.actor == get_eo_user(assigned_article.journal)
    assert r_2.recipient == section_editor.janeway_account
    assert r_2.date_due == create_date + datetime.timedelta(days=(r_2_days_after + r_2_base_days))

    r_3 = Reminder.objects.get(code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3)
    assert r_3.actor == get_eo_user(assigned_article.journal)
    assert r_3.recipient == director.janeway_account
    assert r_3.date_due == create_date + datetime.timedelta(days=(r_3_days_after + r_3_base_days))


@pytest.mark.django_db
def test_create_message_from_esr1_reminders(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    director: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """Message created from ESR reminder correctly creates message body."""
    # The `assigned_article` fixture already performed the assignment, so we just check the reminders
    editor_assignment = WjsEditorAssignment.objects.get(article=assigned_article, editor=section_editor)
    reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(editor_assignment),
        object_id=editor_assignment.id,
    )
    assert reminders.count() == 3
    reminder = reminders.get(code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1)
    message = reminder.create_message()
    assert section_editor.full_name() in message.body


@pytest.mark.django_db
def test_reminders_handling_for_reviewer_cycle(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test the creation/deletion of reminders on a full cycle or reviewer assigned-accept-report."""
    fake_request.user = section_editor.janeway_account

    # Let's delete all reminders (e.g. those for the editor created by the `assigned_article` fixture), so that we can
    # test what comes next easily.
    Reminder.objects.all().delete()

    service__assign_reviewer = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": timezone.now().date() + datetime.timedelta(days=7),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )

    assert not Reminder.objects.exists()
    service__assign_reviewer.run()
    assert Reminder.objects.count() == 3
    assignment = service__assign_reviewer.assignment
    assert Reminder.objects.get(
        code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
    )
    assert Reminder.objects.get(
        code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
    )
    assert Reminder.objects.get(
        code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
    )

    service__evaluate_review = EvaluateReview(
        assignment=assignment,
        reviewer=assignment.reviewer,
        editor=assignment.editor,
        form_data={"reviewer_decision": "1"},
        request=fake_request,
        token="",
    )
    service__evaluate_review.run()
    assert Reminder.objects.count() == 2
    assert Reminder.objects.get(
        code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1,
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
    )
    assert Reminder.objects.get(
        code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2,
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
    )

    test_helpers._submit_review(
        review_assignment=assignment,
        review_form=review_form,
        fake_request=fake_request,
    )
    # I'm checking only the reminders for the reviewer, because I should have some other rimenders for the editor.
    assert (
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(assignment),
            object_id=assignment.id,
        ).count()
        == 0
    )
    assert (
        Reminder.objects.filter(
            code__in=[
                Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
                Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
                Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
                Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1,
                Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2,
            ],
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_three_papers_three_reviewers(
    review_settings,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    journal: journal_models.Journal,
    create_submitted_articles: Callable,
    create_jcom_user: Callable,
    fake_request: HttpRequest,
    caplog,
):
    """
    Integration test that reminder works with each other

    Let's have four papers.
    Paper A has 2 reviewers.
    - A_r1 is late: REEA1 has been sent, we are going to send REEA2, and REEA3 is not yet due.
    - A_r2 has accepted the review request on time, but is late with the report, so we send REWR1
    Paper B has 1 reviewer.
    - B_r1 is late, so we send REEA1.
    Paper C has no reviewer.
    - Editor is late so we send EDSR1.
    Paper D has 1 reviewer.
    - D_r1 accepts and send report immediately
    - Editor is late 1 time for make a decision
    - Author is late 1 time for revision

    To achieve this, let's say
    - The review assignment date_due is t1 (makes things easier)
    - A has been assigned to A_r1 due date t1
    - A has been assigned to A_r2 due date t1, and A_r2 accepted right away, report due date is t2
    - B has been assigned to B_r1 due date t2, 3 days after A
    - C has been assigned to the editor on t0
    - D has been assigned to D_r1 accepted / report sent at t0, editor decision due at t3, author revision due at t3

    Due dates for reminders:

    - t0: now
    - t1: t0 + 1
    - t2: t0 + 3
    - t3: t0 + 5

    - A_r1
      - REEA1: t1 + 0 (t0 + 1)
      - REEA2: t1 + 3 (t0 + 4)
      - REEA3: t1 + 5 (t0 + 6)
    - A_r2
      - REWR1: t2 + 0 (t0 + 3)
      - REWR2: t2 + 5 (t0 + 8)
    - B_r1
      - REEA1: t2 + 0 (t0 + 3)
      - REEA2: t2 + 3 (t0 + 6)
      - REEA3: t2 + 5 (t0 + 8)
    - C_e1
      - EDSR1: t0 + 5
      - EDSR2: t0 + 8
      - EDSR3: t0 + 10

    The reminders due date are thus as follows:
    (eX stands for edsr1, edsr2,..., rX stands for reaa1,...,..., wX stands for rewr1,...)

            t0t1  t2  t3
    (days)  . . . . . ' . . . . |
    A_r1      r1    r2  r3
    A_r2          w1        w2
    B_r1          r1    r2  r3
    C_e1              e1    e2
    D_e1              m1    m2
    D_a1

    So:
    - on t0
      - assign A to A_r1
      - assign A to A_r2
      - A_r2 accepts assignment
      - C is created
    - on t2 (t0 + 3)
      - assign B to B_r1

    on each day tick send_wjs_reminders is sent and we check:
    - logs
    - actual messages
    """
    caplog.set_level(logging.DEBUG)

    # Setup
    # =====
    t0 = timezone.localtime(timezone.now()).date()
    t1 = t0 + timezone.timedelta(days=1)
    t2 = t0 + timezone.timedelta(days=3)
    t3 = t0 + timezone.timedelta(days=5)
    t4 = t0 + timezone.timedelta(days=9)

    (a, b, c, d) = create_submitted_articles(journal, 4)
    e1 = create_jcom_user("Edone").janeway_account
    e1.add_account_role("section-editor", journal)
    e2 = create_jcom_user("Edtwo").janeway_account
    e2.add_account_role("section-editor", journal)
    ra1 = create_jcom_user("Rev Aone").janeway_account
    ra2 = create_jcom_user("Rev Atwo").janeway_account
    rb1 = create_jcom_user("Rev Bone").janeway_account
    rd1 = create_jcom_user("Rev Done").janeway_account

    a.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    a.articleworkflow.save()
    AssignToEditor(article=a, editor=e1, request=fake_request).run()
    a.refresh_from_db()
    assert a.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    ea_a = WjsEditorAssignment.objects.get_current(a)

    b.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    b.articleworkflow.save()
    AssignToEditor(article=b, editor=e2, request=fake_request).run()
    b.refresh_from_db()
    assert b.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    ea_b = WjsEditorAssignment.objects.get_current(b)

    c.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    c.articleworkflow.save()
    AssignToEditor(article=c, editor=e2, request=fake_request).run()
    c.refresh_from_db()
    assert c.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    ea_c = WjsEditorAssignment.objects.get_current(c)

    d.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    d.articleworkflow.save()
    AssignToEditor(article=d, editor=e2, request=fake_request).run()
    d.refresh_from_db()
    assert d.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    ea_d = WjsEditorAssignment.objects.get_current(d)

    assert (
        Reminder.objects.filter(
            code__startswith="EDSR",
            content_type=ContentType.objects.get_for_model(ea_a),
            object_id=ea_a.pk,
        ).count()
        == 3
    )
    assert (
        Reminder.objects.filter(
            code__startswith="EDSR",
            content_type=ContentType.objects.get_for_model(ea_b),
            object_id=ea_b.pk,
        ).count()
        == 3
    )
    assert (
        Reminder.objects.filter(
            code__startswith="EDSR",
            content_type=ContentType.objects.get_for_model(ea_c),
            object_id=ea_c.pk,
        ).count()
        == 3
    )
    assert (
        Reminder.objects.filter(
            code__startswith="EDSR",
            content_type=ContentType.objects.get_for_model(ea_d),
            object_id=ea_d.pk,
        ).count()
        == 3
    )
    assert Reminder.objects.all().count() == 12
    assert Message.objects.all().count() == 4
    # Resetting messages, we are going to count new message each day
    Message.objects.all().delete()

    # assign A to A_r1 - due date t1
    # ------------------------------
    fake_request.user = e1
    assignment_A_r1 = AssignToReviewer(  # noqa N806
        workflow=a.articleworkflow,
        reviewer=ra1,
        editor=e1,
        form_data={
            "acceptance_due_date": t1,
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()
    # assign A to A_r2 - due date t1
    # ------------------------------
    assignment_A_r2 = AssignToReviewer(  # noqa N806
        workflow=a.articleworkflow,
        reviewer=ra2,
        editor=e1,
        form_data={
            "acceptance_due_date": t1,
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()
    # A_r2 accepts assignment - report due date t2
    # --------------------------------------------
    EvaluateReview(
        assignment=assignment_A_r2,
        reviewer=ra2,
        editor=e1,
        form_data={"reviewer_decision": "1", "date_due": t2},
        request=fake_request,
        token="",
    ).run()

    # assign B to B_r1 - due date t2
    # ------------------------------
    fake_request.user = e2
    assignment_B_r1 = AssignToReviewer(  # noqa N806
        workflow=b.articleworkflow,
        reviewer=rb1,
        editor=e2,
        form_data={
            "acceptance_due_date": t2,
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()

    # assign D to D_r1 - due date t0
    # ------------------------------
    assignment_D_r1 = AssignToReviewer(  # noqa N806
        workflow=d.articleworkflow,
        reviewer=rd1,
        editor=e2,
        form_data={
            "acceptance_due_date": t0,
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()
    # D_r1 accepts assignment - report due date t1
    # --------------------------------------------
    EvaluateReview(
        assignment=assignment_D_r1,
        reviewer=rd1,
        editor=e2,
        form_data={"reviewer_decision": "1", "date_due": t1},
        request=fake_request,
        token="",
    ).run()
    # D_r1 sends report
    # --------------------------------------------
    report_form = get_report_form(journal.code)
    rf = report_form(
        data=jcom_report_form_data, review_assignment=assignment_D_r1, request=fake_request, submit_final=True
    )
    assert rf.is_valid()
    SubmitReview(
        assignment=assignment_D_r1,
        form=rf,
        request=fake_request,
        submit_final=True,
    ).run()

    assert (
        Reminder.objects.filter(
            code__startswith="REE",
            content_type=ContentType.objects.get_for_model(assignment_A_r1),
            object_id=assignment_A_r1.pk,
        ).count()
        == 3
    )
    assert (
        Reminder.objects.filter(
            code__startswith="REW",
            content_type=ContentType.objects.get_for_model(assignment_A_r2),
            object_id=assignment_A_r2.pk,
        ).count()
        == 2
    )
    assert (
        Reminder.objects.filter(
            code__startswith="REE",
            content_type=ContentType.objects.get_for_model(assignment_B_r1),
            object_id=assignment_B_r1.pk,
        ).count()
        == 3
    )
    assert (
        Reminder.objects.filter(
            code__startswith="EDSR",
            content_type=ContentType.objects.get_for_model(ea_c),
            object_id=ea_c.pk,
        ).count()
        == 3
    )
    assert (
        Reminder.objects.filter(
            code__startswith="EDMD",
            content_type=ContentType.objects.get_for_model(ea_d),
            object_id=ea_d.pk,
        ).count()
        == 3
    )
    assert Reminder.objects.all().count() == 14
    # 4 assignments notifications
    # 2 acceptance notifications
    # 2 report notifications
    assert Message.objects.all().count() == 8
    # Resetting messages, we are going to count new message each day
    Message.objects.all().delete()
    caplog.clear()

    # t0 + 1 : send_wjs_reminders (expect no message)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=1)):
        call_command("send_wjs_reminders")
        assert "Sent 0/0 reminders." in caplog.text
        # No new reminder, same as above
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 0
        assert Message.objects.all().count() == 0
        Message.objects.all().delete()
        caplog.clear()

    # t1 - t0 + 2: send_wjs_reminders (expect REEA1 for A_r1)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=2)):
        call_command("send_wjs_reminders")
        assert "Sent 1/1 reminders." in caplog.text
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 1
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
            content_type=ContentType.objects.get_for_model(assignment_A_r1),
            object_id=assignment_A_r1.pk,
        ).date_sent.date() == t1 + datetime.timedelta(days=1)
        assert Message.objects.all().count() == 1
        Message.objects.all().delete()
        caplog.clear()

    # t0 + 3 : send_wjs_reminders (expect no message)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=3)):
        call_command("send_wjs_reminders")
        assert "Sent 0/0 reminders." in caplog.text
        # No new reminder, same as above
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 1
        assert Message.objects.all().count() == 0
        Message.objects.all().delete()
        caplog.clear()

    # t2 - t0 + 4 : send_wjs_reminders (expect REWR1 for A_r2, REEA1 for B_r1)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=4)):
        call_command("send_wjs_reminders")
        assert "Sent 2/2 reminders." in caplog.text
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 3
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1,
            content_type=ContentType.objects.get_for_model(assignment_A_r2),
            object_id=assignment_A_r2.pk,
        ).date_sent.date() == t2 + datetime.timedelta(days=1)
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
            content_type=ContentType.objects.get_for_model(assignment_B_r1),
            object_id=assignment_B_r1.pk,
        ).date_sent.date() == t2 + datetime.timedelta(days=1)
        assert Message.objects.all().count() == 2
        Message.objects.all().delete()
        caplog.clear()

    # t0 + 5 : send_wjs_reminders (expect REEA2 for A_r1)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=5)):
        call_command("send_wjs_reminders")
        assert "Sent 1/1 reminders." in caplog.text
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 4
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
            content_type=ContentType.objects.get_for_model(assignment_A_r1),
            object_id=assignment_A_r1.pk,
        ).date_sent.date() == t0 + datetime.timedelta(days=5)
        assert Message.objects.all().count() == 1
        Message.objects.all().delete()
        caplog.clear()

    # t3 - t0 + 6 : send_wjs_reminders (expect EDSR1 for ea_c, EDMD1 for ea_d)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=6)):
        call_command("send_wjs_reminders")
        assert "Sent 2/2 reminders." in caplog.text
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 6
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
            content_type=ContentType.objects.get_for_model(ea_c),
            object_id=ea_c.pk,
        ).date_sent.date() == t3 + datetime.timedelta(days=1)
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1,
            content_type=ContentType.objects.get_for_model(ea_d),
            object_id=ea_d.pk,
        ).date_sent.date() == t3 + datetime.timedelta(days=1)
        assert Message.objects.all().count() == 2
        Message.objects.all().delete()
        caplog.clear()

    # t0 + 7 : send_wjs_reminders (expect REEA3 for A_r1, REEA2 for B_r1)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=7)):
        call_command("send_wjs_reminders")
        assert "Sent 2/2 reminders." in caplog.text
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 8
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
            content_type=ContentType.objects.get_for_model(assignment_A_r1),
            object_id=assignment_A_r1.pk,
        ).date_sent.date() == t0 + datetime.timedelta(days=7)
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
            content_type=ContentType.objects.get_for_model(assignment_B_r1),
            object_id=assignment_B_r1.pk,
        ).date_sent.date() == t0 + datetime.timedelta(days=7)
        assert Message.objects.all().count() == 2
        Message.objects.all().delete()
        caplog.clear()

    # t0 + 8 : send_wjs_reminders (no remainder)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=8)):
        call_command("send_wjs_reminders")
        assert "Sent 0/0 reminders." in caplog.text
        assert Message.objects.all().count() == 0
        caplog.clear()

    fake_request.user = e2
    decision_d_a = HandleDecision(
        workflow=d.articleworkflow,
        form_data={
            "decision": ArticleWorkflow.Decisions.TECHNICAL_REVISION.value,
            "decision_editor_report": "report",
            "Notice": "",
            "date_due": t4,
        },
        user=e2,
        request=fake_request,
    ).run()
    rr_d_a = decision_d_a.get_revision_request()
    Message.objects.all().delete()
    caplog.clear()

    # t0 + 9 : send_wjs_reminders (expect REWR2 for A_r2, REEA3 for B_r1, EDSR2 for ea_c)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=9)):
        call_command("send_wjs_reminders")
        assert "Sent 3/3 reminders." in caplog.text
        # 11 because EDITOR_SHOULD_MAKE_DECISION_1 at t3 has been deleted because the
        # editor has made a decision
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 10
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2,
            content_type=ContentType.objects.get_for_model(assignment_A_r2),
            object_id=assignment_A_r2.pk,
        ).date_sent.date() == t0 + datetime.timedelta(days=9)
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
            content_type=ContentType.objects.get_for_model(assignment_B_r1),
            object_id=assignment_B_r1.pk,
        ).date_sent.date() == t0 + datetime.timedelta(days=9)
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
            content_type=ContentType.objects.get_for_model(ea_c),
            object_id=ea_c.pk,
        ).date_sent.date() == t0 + datetime.timedelta(days=9)
        assert Message.objects.all().count() == 3
        Message.objects.all().delete()
        caplog.clear()

    # t0 + 10 : send_wjs_reminders (expect AUTCR1 for rr_d_a)
    with freezegun.freeze_time(t0 + datetime.timedelta(days=10)):
        call_command("send_wjs_reminders")
        assert "Sent 1/1 reminders." in caplog.text
        # 11 because EDITOR_SHOULD_MAKE_DECISION_1 at t3 has been deleted because the
        # editor has made a decision
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 11
        assert Reminder.objects.get(
            code=Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1,
            content_type=ContentType.objects.get_for_model(rr_d_a),
            object_id=rr_d_a.pk,
        ).date_sent.date() == t0 + datetime.timedelta(days=10)
        assert Message.objects.all().count() == 1
        Message.objects.all().delete()
        caplog.clear()


@pytest.mark.django_db
def test_editor_declines(
    fake_request: HttpRequest,
    director: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """Reminders for the editor are deleted and reminders for the director are created."""
    t0 = timezone.localtime(timezone.now()).date()
    assert Reminder.objects.all().count() == 3
    assert Reminder.objects.filter(code__startswith="EDSR").count() == 3
    editor_assignment = WjsEditorAssignment.objects.get(article=assigned_article)
    HandleEditorDeclinesAssignment(
        assignment=editor_assignment,
        editor=editor_assignment.editor,
        request=fake_request,
        director=director,
    ).run()
    assert Reminder.objects.all().count() == 2
    assert Reminder.objects.filter(code__startswith="DIRAS").count() == 2
    check_reminder_date(
        assigned_article,
        DirectorShouldAssignEditorReminderManager,
        (
            Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_1,
            Reminder.ReminderCodes.DIRECTOR_SHOULD_ASSIGN_EDITOR_2,
        ),
        t0,
        journal=assigned_article.journal,
    )


@pytest.mark.django_db
def test_director_assigns(
    fake_request: HttpRequest,
    director: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """
    Reminders for the director are deleted and reminders for the editor are created if director selects an editor.
    """
    t0 = timezone.localtime(timezone.now()).date()
    editor_assignment = WjsEditorAssignment.objects.get(article=assigned_article)
    HandleEditorDeclinesAssignment(
        assignment=editor_assignment,
        editor=editor_assignment.editor,
        request=fake_request,
        director=director,
    ).run()
    assert not Reminder.objects.filter(recipient=editor_assignment.editor).exists()
    new_assignment = AssignToEditor(
        editor=editor_assignment.editor,
        article=assigned_article,
        request=fake_request,
        workflow=assigned_article.articleworkflow,
    ).run()
    assert Reminder.objects.all().count() == 3
    assert Reminder.objects.filter(code__startswith="EDSR").count() == 3
    check_reminder_date(
        new_assignment,
        EditorShouldSelectReviewerReminderManager,
        (
            Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
            Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
            Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
        ),
        t0,
        journal=assigned_article.journal,
    )


class TestEditorDecides:
    """What happens when an editor makes a decision.

    As per specs#619, in all cases, we should just delete the editor reminders:
    - accept article
    - decline (i.e. reject) article
    - deem paper not suitable
    - request revision
      - major
      - minor
      - technical  <-- even here we don't keep reminders for the editor, because the author should act!

    In case a revision is requested, author's reminders are created.

    Existing WorkflowReviewAssignments in any state (open, declined or completed) do not play a role here,
    so I'm not testing any combination of decision x WorkflowReviewAssignments-state.

    """

    @pytest.mark.parametrize(
        "decision",
        (
            ArticleWorkflow.Decisions.ACCEPT,
            ArticleWorkflow.Decisions.REJECT,
            ArticleWorkflow.Decisions.NOT_SUITABLE,
            ArticleWorkflow.Decisions.MINOR_REVISION,
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.TECHNICAL_REVISION,
        ),
    )
    @pytest.mark.django_db
    def test__all_decisions__no_reviews(
        self,
        fake_request: HttpRequest,
        director: JCOMProfile,
        normal_user: JCOMProfile,
        assigned_article: submission_models.Article,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
        decision: str,
    ):
        """Test that reminders for the editor are deleted and author's are created if revision is requested."""

        editor_assignment = WjsEditorAssignment.objects.get_current(assigned_article)
        section_editor = editor_assignment.editor
        fake_request.user = section_editor
        form_data = {
            "decision": decision,
            "decision_editor_report": "random message",
            "withdraw_notice": "notice",
        }
        date_due = timezone.localtime(timezone.now()).date() + datetime.timedelta(days=7)
        if decision not in (
            ArticleWorkflow.Decisions.ACCEPT,
            ArticleWorkflow.Decisions.REJECT,
            ArticleWorkflow.Decisions.NOT_SUITABLE,
        ):
            form_data["date_due"] = date_due
        handle = HandleDecision(
            workflow=assigned_article.articleworkflow,
            form_data=form_data,
            user=section_editor,
            request=fake_request,
        )
        handle.run()
        assigned_article.refresh_from_db()
        assert not Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
        ).exists()

        if decision == ArticleWorkflow.Decisions.MAJOR_REVISION:
            revision_request = EditorRevisionRequest.objects.get(article=assigned_article)
            check_reminder_date(
                revision_request,
                AuthorShouldSubmitMajorRevisionReminderManager,
                (
                    Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_1,
                    Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2,
                ),
                date_due,
                journal=assigned_article.journal,
            )
        if decision == ArticleWorkflow.Decisions.MINOR_REVISION:
            revision_request = EditorRevisionRequest.objects.get(article=assigned_article)
            check_reminder_date(
                revision_request,
                AuthorShouldSubmitMinorRevisionReminderManager,
                (
                    Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_1,
                    Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2,
                ),
                date_due,
                journal=assigned_article.journal,
            )
        if decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
            revision_request = EditorRevisionRequest.objects.get(article=assigned_article)
            check_reminder_date(
                revision_request,
                AuthorShouldSubmitTechnicalRevisionReminderManager,
                (
                    Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1,
                    Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2,
                ),
                date_due,
                journal=assigned_article.journal,
            )


class TestResetDate:
    """Tests relative to the resetting of WorkflowReviewAssignmentss' date_due.

    When a reviewer modifies an assignment's due date, all relative reminders should be checked.

    """

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "clemency_days,delta_days,reminder_is_sent",
        (
            (0, 0, True),
            (0, 0, False),
            (0, 1, True),
            (0, 1, False),
            (0, 3, True),
            (0, 3, False),
            (2, 0, True),
            (2, 0, False),
            (2, 1, True),
            (2, 1, False),
            (2, 3, True),
            (2, 3, False),
        ),
    )
    def test_reset_date_function(
        self,
        fake_request: HttpRequest,
        review_form: review_models.ReviewForm,
        review_assignment: WorkflowReviewAssignment,
        clemency_days: int,
        delta_days: int,
        reminder_is_sent,
    ):
        """Test the function `update_date_send_reminders`."""
        reminder = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1)

        # Force a known clemency_days for testing
        reminder.clemency_days = clemency_days

        # Simulate that a reminder has been sent by setting any date
        if reminder_is_sent:
            reminder.date_sent = timezone.now()

        reminder.save()
        reminder.refresh_from_db()  # ⬅ correct the date_due type from datetime to date

        # Store the reminder's due date for later comparison
        reminder_date_due_t0 = reminder.date_due

        # Simulate an update of the reminder date
        # NB: Remember tha the update of the reminder's date happens _before_ the assignment's date_due is updated!
        new_assignment_date_due = review_assignment.date_due + datetime.timedelta(days=delta_days)
        update_date_send_reminders(review_assignment, new_assignment_date_due)

        # Change the assigment due_date
        review_assignment.date_due = new_assignment_date_due
        review_assignment.save()

        reminder.refresh_from_db()

        reminder_date_due_t1 = reminder.date_due
        reminder_date_due_delta = (reminder_date_due_t1 - reminder_date_due_t0).days
        if delta_days > clemency_days:
            assert reminder.date_sent is None
            assert reminder_date_due_delta == delta_days
        else:
            if reminder_is_sent:
                assert reminder.date_sent is not None
                assert reminder.date_due == reminder_date_due_t0
            else:
                assert reminder.date_sent is None
                assert reminder_date_due_delta == delta_days

    @pytest.mark.django_db
    def test_reset_function_is_called(
        self,
        fake_request: HttpRequest,
        review_form: review_models.ReviewForm,
        review_assignment: WorkflowReviewAssignment,
    ):
        """Verify that update_date_send_reminders is called."""
        # Let the reviewer update the date
        with mock.patch(
            "plugins.wjs_review.logic.communication_utils.update_date_send_reminders",
        ) as mocked_update_date_send_reminders:
            fake_request.user = review_assignment.reviewer
            new_date_due = review_assignment.date_due + datetime.timedelta(days=1)
            EvaluateReview(
                assignment=review_assignment,
                reviewer=review_assignment.reviewer,
                editor=review_assignment.editor,
                form_data={
                    "reviewer_decision": "2",
                    "date_due": new_date_due,
                },
                request=fake_request,
                token="",
            ).run()

            # ⮶ 本番 ⮷
            mocked_update_date_send_reminders.assert_called_once()
            # NB: the function bumps the dates of _all_ reminders (of a certain target), so
            # `mocked_update_date_send_reminders.call_count == 3` is False!

    @pytest.mark.django_db
    def test_reset_REEA_reminders_send_date(  # noqa N802 lowercase
        self,
        fake_request: HttpRequest,
        review_form: review_models.ReviewForm,
        review_assignment: WorkflowReviewAssignment,
    ):
        """Verify that all REEA reminders are "touched"."""
        # Sanity check: we should have the three REEA reminders on this review assignment
        reea_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.id,
        )
        assert reea_reminders.count() == 3
        assert all("REEA" in code for code in reea_reminders.values_list("code", flat=True))

        # Save the initial dates for later comparison.
        # NB: remember that values_list() returns a queryset, which is lazy, so we need list() to fixate it!
        initial_dates = list(reea_reminders.order_by("id").values_list("date_due", flat=True))

        # Let the reviewer update the date
        fake_request.user = review_assignment.reviewer
        new_date_due = review_assignment.date_due + datetime.timedelta(days=1)
        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            form_data={
                "reviewer_decision": "2",
                "date_due": new_date_due,
            },
            request=fake_request,
            token="",
        ).run()
        review_assignment.refresh_from_db()
        reea_reminders.all()

        # no REEA reminder has been sent, so here I simply test that the due date
        # has been bumped.
        updated_dates = reea_reminders.order_by("id").values_list("date_due", flat=True)
        assert all(initial < updated for initial, updated in zip(initial_dates, updated_dates))

    @pytest.mark.django_db
    def test_reset_REWR1_reminders_send_date(  # noqa N802 lowercase
        self,
        fake_request: HttpRequest,
        review_form: review_models.ReviewForm,
        review_assignment: WorkflowReviewAssignment,
    ):
        """Verify that all REWR1 reminders are "touched"."""
        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            form_data={"reviewer_decision": "1", "accept_gdpr": 1},
            request=fake_request,
            token="",
        ).run()

        # Sanity check: we should have the two REWR reminders on this review assignment
        rewr_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.id,
        )
        assert rewr_reminders.count() == 2
        assert all("REWR" in code for code in rewr_reminders.values_list("code", flat=True))

        # Save the initial dates for later comparison.
        # NB: remember that values_list() returns a queryset, which is lazy, so we need list() to fixate it!
        initial_dates = list(rewr_reminders.order_by("id").values_list("date_due", flat=True))

        # Let the reviewer update the date
        fake_request.user = review_assignment.reviewer
        new_date_due = review_assignment.date_due + datetime.timedelta(days=3)
        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            form_data={
                "reviewer_decision": "1",
                "accept_gdpr": 1,
                "date_due": new_date_due,
            },
            request=fake_request,
            token="",
        ).run()
        review_assignment.refresh_from_db()
        rewr_reminders.all()

        # no REWR reminder has been sent, so here I simply test that the due date
        # has been bumped.
        updated_dates = rewr_reminders.order_by("id").values_list("date_due", flat=True)
        assert all(initial < updated for initial, updated in zip(initial_dates, updated_dates))

    @pytest.mark.django_db
    def test_any_reviewer_is_late_after_reminder(
        self,
        review_form: review_models.ReviewForm,
        review_assignment: WorkflowReviewAssignment,
    ):
        """Verify that reminders trigger the attention condition."""
        # Sanity check: we should have the three REEA reminders on this review assignment
        reea_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.id,
        )
        base_date = timezone.localtime(timezone.now())
        assert reea_reminders.count() == 3
        reea_reminders.update(date_sent=base_date)
        assert not any_reviewer_is_late_after_reminder(review_assignment.article)
        reea_reminders.update(date_sent=base_date - datetime.timedelta(days=1))
        assert not any_reviewer_is_late_after_reminder(review_assignment.article)
        reea_reminders.update(date_sent=base_date - datetime.timedelta(days=settings.WJS_REMINDER_LATE_AFTER + 1))
        assert (
            f"Reviewer's reminder sent past {settings.WJS_REMINDER_LATE_AFTER} days."
            == any_reviewer_is_late_after_reminder(review_assignment.article)
        )
