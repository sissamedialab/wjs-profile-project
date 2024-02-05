import datetime
import logging
from typing import Callable, Optional
from unittest import mock

import freezegun
import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.http import HttpRequest
from django.utils import timezone
from journal import models as journal_models
from review import models as review_models
from review.models import ReviewAssignment
from submission import models as submission_models

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import render_template

from ..communication_utils import get_eo_user, update_date_send_reminders
from ..logic import (
    AssignToEditor,
    AssignToReviewer,
    EvaluateReview,
    HandleDecision,
    create_reminder,
)
from ..models import ArticleWorkflow, Reminder, WorkflowReviewAssignment
from . import test_helpers


def test_render_template():
    """Test the simple render_template() function."""
    result = render_template("-{{ aaa }}-", {"aaa": "AAA"})
    assert result == "-AAA-"


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
    # processing (e.g. a ReviewAssignment). However, the `run()` method will call create_reminder itself.
    service._ensure_reviewer()
    service.assignment = service._assign_reviewer()

    reminder_obj = create_reminder(
        assigned_article.journal,
        service.assignment,
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
    )

    # Remember that the fixture `assigned_article` creates the EDITOR_SHOULD_SELECT_REVIEWER reminders
    reminders = Reminder.objects.filter(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1)
    assert reminders.count() == 1
    assert reminders.first() == reminder_obj

    # Somewhat weak test that the subject has been rendered
    assert assigned_article.journal.code in reminder_obj.message_subject

    assert reminder_obj.recipient == service.reviewer
    assert reminder_obj.actor == get_eo_user(assigned_article.journal)


@pytest.mark.django_db
def test_assign_reviewer_creates_reminders(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test that when a reviewers is assigned, reminders are created."""
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

    r_1 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1)
    assert r_1.actor == get_eo_user(assigned_article.journal)
    assert r_1.recipient == service.reviewer
    # TODO: do we really want to test the date computation? This promises a lot ot test-maintenance
    # assert r1.date_due == ???

    r_2 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2)
    assert r_2.actor == service.editor
    assert r_2.recipient == service.reviewer

    r_3 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3)
    assert r_3.actor == get_eo_user(assigned_article.journal)
    assert r_3.recipient == service.editor


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

    service__evaluate_review = EvaluateReview(
        assignment=service__assign_reviewer.assignment,
        reviewer=service__assign_reviewer.reviewer,
        editor=service__assign_reviewer.editor,
        form_data={"reviewer_decision": "1"},
        request=fake_request,
        token="",
    )
    service__evaluate_review.run()
    assert Reminder.objects.count() == 2
    r_1 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1)
    assert r_1.actor == service__evaluate_review.editor
    assert r_1.recipient == service__evaluate_review.reviewer
    r_2 = Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2)
    assert r_2.actor == get_eo_user(assigned_article.journal)
    assert r_2.recipient == service__evaluate_review.editor


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
        editor_assignment: review_models.EditorAssignment = review_models.EditorAssignment.objects.get(
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
        review_assignment: review_models.ReviewAssignment = (
            AssignToReviewer(
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
            .run()
            .reviewassignment_ptr
        )

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
        editor_assignment: review_models.EditorAssignment = review_models.EditorAssignment.objects.get(
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
        review_assignment: review_models.ReviewAssignment = (
            AssignToReviewer(
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
            .run()
            .reviewassignment_ptr
        )

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
        editor_assignment: review_models.EditorAssignment = review_models.EditorAssignment.objects.get(
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
        review_assignment: review_models.ReviewAssignment = (
            AssignToReviewer(
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
            .run()
            .reviewassignment_ptr
        )

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
        review_assignment: ReviewAssignment,
        review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    ):
        """Test that reminders for the reviewer are deleted and reminders for the editor are created."""
        assigned_article = review_assignment.article
        # Sanity check:
        editor_assignment: review_models.EditorAssignment = review_models.EditorAssignment.objects.get(
            article=assigned_article,
            editor=section_editor,
        )
        editor_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(editor_assignment),
            object_id=editor_assignment.id,
        )
        assert not editor_reminders.exists()

        # NB: review_assignment is really a WorkflowReviewAssignment!
        review_assignment = review_assignment.reviewassignment_ptr
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
        editor_assignment: review_models.EditorAssignment = review_models.EditorAssignment.objects.get(
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
        editor_assignment: review_models.EditorAssignment = review_models.EditorAssignment.objects.get(
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
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """Test that when a paper is assigned to an editor, reminders are created for the editor to select reviewers."""
    # The `assigned_article` fixture already performed the assignment, so we just check the reminders
    editor_assignment = review_models.EditorAssignment.objects.get(article=assigned_article, editor=section_editor)
    reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(editor_assignment),
        object_id=editor_assignment.id,
    )
    assert reminders.count() == 3
    # TODO: expand me!


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
    if isinstance(service__assign_reviewer.assignment, WorkflowReviewAssignment):
        assignment = service__assign_reviewer.assignment.reviewassignment_ptr
    else:
        # if we are here, self.assigment is a ReviewAssigment
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
def test_two_papers_three_reviewers(
    review_settings,
    known_reminders_configuration,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
    journal: journal_models.Journal,
    create_submitted_articles: Callable,
    create_jcom_user: Callable,
    fake_request: HttpRequest,
    caplog,
):
    """Test somewhat complex scenario.

    Let's have two papers.
    Paper A has 2 reviewers.
    - A_r1 is late: ESR1 has been sent, we are going to send ESR2, and ESR3 is not yet due.
    - A_r2 has accepted the review request on time, but is late with the report, so we send RAA1
    Paper B has 1 reviewer.
    - B_r1 is late, so we send ESR1.

    To achieve this, let's say
    - The review assignment date_due is t0 (makes things easier)
    - A has been assigned to A_r1 on t0
    - A has been assigned to A_r2 on t0, and A_r2 accepted right away
    - B has been assigned to B_r1 on t1, 3 days after A

    The reminders due date are thus as follow:
    (eX stands for esr1, esr2,... and rX stands for raa1,...)

            t0    t1  t2  t3
            A     B
    (days)  . . . . ' . . . . | . . . . ' . . . . |
    A_r1          e1    e2  e3
    A_r2                r1      r2
    B_r1                e1

    This works only if esr2 and raa1 have the same `days_after` value (7)
    (might want to do `reminders.settings.reminders["DEFAULT"][ESR1].days_after = 7` etc.)

    So:
    - on t0
      - assign A to A_r1
      - assign A to A_r2
      - A_r2 accepts assignment
    - on t1 (t0 + 3) assign B to B_r1
    - on t2 (t0 + 5) call send_wjs_reminders (expect only esr1 for A_r1)
    - on t3 (t0 + 8) call send_wjs_reminders (expect esr2 for A_r1, raa1 for A_r2 and esr1 for B_r1)

    """
    caplog.set_level(logging.DEBUG)

    # Setup
    # =====
    t0 = timezone.now().date()
    t1 = t0 + timezone.timedelta(days=3)
    t2 = t0 + timezone.timedelta(days=5)
    t3 = t0 + timezone.timedelta(days=8)

    (a1, a2) = create_submitted_articles(journal, 2)
    e1 = create_jcom_user("Edone").janeway_account
    e1.add_account_role("section-editor", journal)
    e2 = create_jcom_user("Edtwo").janeway_account
    e2.add_account_role("section-editor", journal)
    ra1 = create_jcom_user("Rev Aone").janeway_account
    ra2 = create_jcom_user("Rev Atwo").janeway_account
    rb1 = create_jcom_user("Rev Bone").janeway_account

    a1.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    a1.articleworkflow.save()
    AssignToEditor(
        article=a1,
        editor=e1,
        request=fake_request,
    ).run()
    a1.refresh_from_db()
    assert a1.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    a2.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    a2.articleworkflow.save()
    AssignToEditor(
        article=a2,
        editor=e2,
        request=fake_request,
    ).run()
    a2.refresh_from_db()
    assert a2.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    # Do I want to `cleanup_notifications_side_effects()`?

    # t0: assign A to A_r1
    # --------------------
    fake_request.user = e1
    assignment_A_r1: WorkflowReviewAssignment = AssignToReviewer(  # noqa N806
        workflow=a1.articleworkflow,
        reviewer=ra1,
        editor=e1,
        form_data={
            "acceptance_due_date": timezone.now().date(),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()
    # t0: assign A to A_r2
    # --------------------
    assignment_A_r2: WorkflowReviewAssignment = AssignToReviewer(  # noqa N806
        workflow=a1.articleworkflow,
        reviewer=ra2,
        editor=e1,
        form_data={
            "acceptance_due_date": timezone.now().date(),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()
    # t0: A_r2 accepts assignment
    # ---------------------------
    EvaluateReview(
        assignment=assignment_A_r2,
        reviewer=ra2,
        editor=e1,
        form_data={"reviewer_decision": "1"},
        request=fake_request,
        token="",
    ).run()

    # t1: assign B to B_r1
    # --------------------
    with freezegun.freeze_time(t1):
        fake_request.user = e2
        assignment_B_r1: WorkflowReviewAssignment = AssignToReviewer(  # noqa N806
            workflow=a2.articleworkflow,
            reviewer=rb1,
            editor=e2,
            form_data={
                "acceptance_due_date": timezone.now().date(),
                "message": "random message",
                "author_note_visible": False,
            },
            request=fake_request,
        ).run()

    # t2: send_wjs_reminders (expect only esr1 for A_r1)
    # ----------------------
    with freezegun.freeze_time(t2):
        call_command("send_wjs_reminders")
        assert "Sent 1/1 reminders." in caplog.text
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 1

        esr1_for_ra1 = Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
            content_type=ContentType.objects.get_for_model(assignment_A_r1.reviewassignment_ptr),
            object_id=assignment_A_r1.id,
        )
        assert esr1_for_ra1.date_sent.date() == t2

    # t3: send_wjs_reminders (expect esr2 for A_r1, raa1 for A_r2 and esr1 for B_r1)
    # ----------------------
    with freezegun.freeze_time(t3):
        call_command("send_wjs_reminders")
        assert "Sent 1/1 reminders." in caplog.text
        # We should have sent 3 reminders now and one on t2: 4 total
        assert Reminder.objects.filter(date_sent__isnull=False).count() == 4

        esr2_for_ra1 = Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2,
            content_type=ContentType.objects.get_for_model(assignment_A_r1.reviewassignment_ptr),
            object_id=assignment_A_r1.id,
        )
        assert esr2_for_ra1.date_sent.date() == t3

        raa1_for_ra2 = Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_1,
            content_type=ContentType.objects.get_for_model(assignment_A_r2.reviewassignment_ptr),
            object_id=assignment_A_r2.id,
        )
        assert raa1_for_ra2.date_sent.date() == t3

        esr1_for_rb1 = Reminder.objects.get(
            code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
            content_type=ContentType.objects.get_for_model(assignment_B_r1.reviewassignment_ptr),
            object_id=assignment_B_r1.id,
        )
        assert esr1_for_rb1.date_sent.date() == t3


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

    Existing ReviewAssignments in any state (open, declined or completed) do not play a role here, so I'm not testing
    any combination of decision x ReviewAssignment-state.

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
        """Test that reminders for the editor are deleted."""
        editor_assignment = assigned_article.editorassignment_set.first()
        section_editor = editor_assignment.editor
        fake_request.user = section_editor
        form_data = {
            "decision": decision,
            "decision_editor_report": "random message",
            "decision_internal_note": "random internal message",
            "withdraw_notice": "notice",
        }
        if decision not in (
            ArticleWorkflow.Decisions.ACCEPT,
            ArticleWorkflow.Decisions.REJECT,
            ArticleWorkflow.Decisions.NOT_SUITABLE,
        ):
            form_data["date_due"] = timezone.now().date() + datetime.timedelta(days=7)
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


class TestResetDate:
    """Tests relative to the resetting of ReviewAssignments' date_due.

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
        review_assignment: ReviewAssignment = review_assignment.reviewassignment_ptr

        reminder = create_reminder(
            journal=review_assignment.article.journal,
            target=review_assignment,
            reminder_code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1,
        )

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
        update_date_send_reminders(review_assignment, new_assignment_date_due.date())

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
        with mock.patch("plugins.wjs_review.logic.update_date_send_reminders") as mocked_update_date_send_reminders:
            fake_request.user = review_assignment.reviewer
            new_date_due = review_assignment.date_due.date() + datetime.timedelta(days=1)
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
        review_assignment: ReviewAssignment = review_assignment.reviewassignment_ptr
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
        new_date_due = review_assignment.date_due.date() + datetime.timedelta(days=1)
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

        # REEA reminders have clemeny_days = 0, and no reminder has been sent, so here I simply test that the due date
        # has been bumped.
        updated_dates = reea_reminders.order_by("id").values_list("date_due", flat=True)
        assert all(initial < updated for initial, updated in zip(initial_dates, updated_dates))
