import datetime
import logging
from typing import Callable

import freezegun
import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.http import HttpRequest
from django.utils import timezone
from journal import models as journal_models
from review import models as review_models
from submission import models as submission_models

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import render_template

from ..communication_utils import get_eo_user
from ..logic import AssignToEditor, AssignToReviewer, EvaluateReview, create_reminder
from ..models import ArticleWorkflow, Reminder, WorkflowReviewAssignment
from . import test_helpers


def test_render_template():
    """Test the simple render_template() function."""
    result = render_template("-{{ aaa }}-", {"aaa": "AAA"})
    assert result == "-AAA-"


@pytest.mark.django_db
def test_create_a_reminder(
    review_settings,
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

    reminders = Reminder.objects.all()
    assert reminders.count() == 1
    assert reminders.first() == reminder_obj

    # Somewhat weak test that the subject has been rendered
    assert assigned_article.journal.code in reminder_obj.message_subject

    assert reminder_obj.recipient == service.reviewer
    assert reminder_obj.actor == get_eo_user(assigned_article.journal)


@pytest.mark.django_db
def test_assign_reviewer_creates_reminders(
    review_settings,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test that when a reviewers is assigned, reminders are created."""
    fake_request.user = section_editor.janeway_account

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
    review_settings,
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
    review_settings,
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
    review_settings,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test that when a reviewer accepts an assignments the ESR reminders for that assignment are deleted."""
    fake_request.user = section_editor.janeway_account

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


@pytest.mark.django_db
def test_reviewer_declines__deletes_reminders(
    review_settings,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test that when a reviewer declines an assignments all reminders for that assignment are deleted."""
    fake_request.user = section_editor.janeway_account

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
        form_data={"reviewer_decision": "0"},
        request=fake_request,
        token="",
    )
    service__evaluate_review.run()
    assert Reminder.objects.count() == 0


@pytest.mark.django_db
def test_reminders_handling_for_reviewer_cycle(
    review_settings,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,  # Without this, quick_assign() fails!
):
    """Test the creation/deletion of reminders on a full cycle or reviewer assigned-accept-report."""
    fake_request.user = section_editor.janeway_account

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
    assert Reminder.objects.count() == 0


@pytest.mark.django_db
def test_two_papers_three_reviewers(
    review_settings,
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
