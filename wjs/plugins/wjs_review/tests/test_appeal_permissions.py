import datetime
import importlib
from unittest import mock

import pytest
from core.models import AccountRole
from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from journal.models import Issue, IssueType
from submission.models import Article
from utils import setting_handler

from wjs.jcom_profile.models import JCOMProfile

from .. import communication_utils
from ..communication_utils import get_messages_related_to_me, get_system_user
from ..logic import (
    HandleEditorDeclinesAssignment,
    SupervisorChangeEditorAssignment,
    WithdrawPreprint,
)
from ..logic__visibility import PermissionChecker
from ..models import (
    ArticleWorkflow,
    EditorDecision,
    Message,
    PastEditorAssignment,
    PermissionAssignment,
    WjsEditorAssignment,
)
from ..permissions import EditorType, get_editor_type
from ..templatetags.wjs_review import hide_last_submitted, is_user_former_article_editor
from .conftest import _under_appeal_article
from .test_helpers import _create_review_assignment, _submit_review


@pytest.mark.django_db
def test_open_appeal_with_new_editor_flags_past_assignment(
    under_appeal_article_new_editor: Article, section_editor: JCOMProfile
):
    """The editor removed when the EO opens an appeal gets a PastEditorAssignment flagged on_appeal."""
    past = PastEditorAssignment.objects.get(
        article=under_appeal_article_new_editor, editor=section_editor.janeway_account
    )
    assert past.on_appeal is True, "Editor removed by OpenAppeal must be flagged on_appeal"


@pytest.mark.django_db
def test_supervisor_change_editor_does_not_flag_on_appeal(
    assigned_article: Article, normal_user: JCOMProfile, eo_user: JCOMProfile, fake_request: HttpRequest
):
    """A plain editor change by the EO does not flag the past assignment."""
    normal_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = eo_user.janeway_account
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    old_editor = assignment.editor
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=assignment,
        new_editor=normal_user.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    past = PastEditorAssignment.objects.get(article=assigned_article, editor=old_editor)
    assert past.on_appeal is False, "Plain editor change must not flag on_appeal"


@pytest.mark.django_db
def test_editor_decline_does_not_flag_on_appeal(
    assigned_article: Article, section_editor: JCOMProfile, fake_request: HttpRequest
):
    """An editor declining the assignment does not flag the past assignment."""
    fake_request.user = section_editor.janeway_account
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    HandleEditorDeclinesAssignment(
        assignment=assignment,
        editor=section_editor.janeway_account,
        request=fake_request,
        form_data={"decline_reason": PastEditorAssignment.DeclineReasons.BUSY, "decline_text": ""},
    ).run()
    past = PastEditorAssignment.objects.get(article=assigned_article, editor=section_editor.janeway_account)
    assert past.on_appeal is False, "Editor decline must not flag on_appeal"


@pytest.mark.django_db
def test_appeal_submitted_article_fixture(appeal_submitted_article: Article, appeal_editor: JCOMProfile):
    """The appeal_submitted_article fixture yields an article back with the appeal editor after resubmission."""
    assert appeal_submitted_article.reviewround_set.count() == 2, "Resubmission must open a new review round"
    current = WjsEditorAssignment.objects.get_current(appeal_submitted_article)
    assert current.editor == appeal_editor.janeway_account, "Appeal editor must be the current editor"


@pytest.mark.django_db
def test_appeal_editor_does_not_see_appealed_round(
    under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile
):
    """The appeal editor does not get the rejected round that holds the OPEN_APPEAL decision."""
    workflow = under_appeal_article_new_editor.articleworkflow
    versions = workflow.get_review_versions(appeal_editor.janeway_account)
    assert [v.number for v in versions] == [-1], "Appeal editor must only see the initial fake version"


@pytest.mark.django_db
def test_appeal_editor_sees_rounds_after_resubmission(appeal_submitted_article: Article, appeal_editor: JCOMProfile):
    """After the author resubmits, the appeal editor sees the new round but still not the appealed one."""
    workflow = appeal_submitted_article.articleworkflow
    numbers = [v.number for v in workflow.get_review_versions(appeal_editor.janeway_account)]
    assert 2 in numbers, "Appeal editor must see the round opened by the resubmission"
    assert 1 not in numbers, "Appeal editor must not see the appealed round"


@pytest.mark.django_db
def test_same_editor_on_appeal_sees_rejected_round(under_appeal_article: Article, section_editor: JCOMProfile):
    """The editor who rejected and is re-assigned on appeal keeps seeing the rejected round (spec D2)."""
    workflow = under_appeal_article.articleworkflow
    numbers = [v.number for v in workflow.get_review_versions(section_editor.janeway_account)]
    assert 1 in numbers, "Same editor on appeal must keep the rejected round"


@pytest.mark.django_db
def test_removed_for_appeal_editor_sees_their_round_and_report(
    appeal_submitted_article: Article, section_editor: JCOMProfile
):
    """The editor removed on appeal sees the round they handled with its editor report, not later rounds."""
    workflow = appeal_submitted_article.articleworkflow
    user = section_editor.janeway_account
    versions = {v.number: v for v in workflow.get_review_versions(user)}
    assert 1 in versions, "Removed editor must see the round they handled"
    assert 2 not in versions, "Removed editor must not see rounds after their removal"
    decision = versions[1].final_decision()
    assert decision is not None, "Round 1 must have a final (reject) decision"
    assert PermissionChecker()(
        workflow, user, decision, permission_type=PermissionAssignment.PermissionType.NO_NAMES, review_round=1
    ), "Removed editor must see the editor report of their round"


@pytest.mark.django_db
def test_past_editor_sees_decision_they_did_not_take(
    appeal_submitted_article: Article, appeal_editor: JCOMProfile, section_editor: JCOMProfile
):
    """A past editor sees editor decisions of rounds they handled even when another user took them."""
    workflow = appeal_submitted_article.articleworkflow
    open_appeal = EditorDecision.objects.get(workflow=workflow, decision=ArticleWorkflow.Decisions.OPEN_APPEAL)
    assert open_appeal.editor != section_editor.janeway_account, "Precondition: OPEN_APPEAL is taken by the EO"
    assert PermissionChecker()(
        workflow,
        section_editor.janeway_account,
        open_appeal,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        review_round=1,
    ), "Past editor must see decisions of the rounds they handled"


@pytest.mark.django_db
def test_past_editor_custom_deny_wins(appeal_submitted_article: Article, section_editor: JCOMProfile):
    """An explicit DENY custom permission hides an item even in a round the past editor handled."""
    workflow = appeal_submitted_article.articleworkflow
    user = section_editor.janeway_account
    decision = EditorDecision.objects.get(workflow=workflow, decision=ArticleWorkflow.Decisions.OPEN_APPEAL)
    PermissionAssignment.objects.create(
        content_type=ContentType.objects.get_for_model(decision),
        object_id=decision.pk,
        user=user,
        permission=PermissionAssignment.PermissionType.DENY,
        permission_secondary=PermissionAssignment.BinaryPermissionType.DENY,
    )
    assert not PermissionChecker()(
        workflow, user, decision, permission_type=PermissionAssignment.PermissionType.NO_NAMES, review_round=1
    ), "Custom DENY must win over the past-editor default"


@pytest.mark.django_db
def test_editor_without_past_rounds_unchanged(appeal_submitted_article: Article, normal_user: JCOMProfile):
    """Users who never edited the article gain nothing from the past-round rule."""
    normal_user.add_account_role("section-editor", appeal_submitted_article.journal)
    workflow = appeal_submitted_article.articleworkflow
    decision = EditorDecision.objects.get(workflow=workflow, decision=ArticleWorkflow.Decisions.OPEN_APPEAL)
    assert not PermissionChecker()(
        workflow,
        normal_user.janeway_account,
        decision,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        review_round=1,
    ), "An unrelated editor must not see the decision"


@pytest.mark.django_db
def test_past_editor_sees_completed_reviews_only(
    assigned_article: Article,
    reviewer: JCOMProfile,
    review_form,
    fake_request: HttpRequest,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    create_jcom_user,
):
    """A past editor sees a completed reviewer report of their round but not an incomplete one."""
    workflow = assigned_article.articleworkflow
    old_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    completed_assignment = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=reviewer,
        assigned_article=assigned_article,
    )
    completed_assignment = _submit_review(review_assignment=completed_assignment, fake_request=fake_request)
    review_round_number = completed_assignment.review_round.round_number

    second_reviewer = create_jcom_user("second_reviewer")
    second_reviewer.add_account_role("reviewer", assigned_article.journal)
    incomplete_assignment = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=second_reviewer,
        assigned_article=assigned_article,
    )

    normal_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = eo_user.janeway_account
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        new_editor=normal_user.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    # The editor change moves review assignments of the current round to the new editor (_migrate_review_assignments);
    # refresh in-memory objects so their `editor` attribute reflects that.
    completed_assignment.refresh_from_db()
    incomplete_assignment.refresh_from_db()

    # _migrate_review_assignments() also creates an explicit ALL PermissionAssignment for the completed
    # assignment; asserting with default_permissions=True bypasses that custom permission so the assertion
    # below exercises EditorPermissionChecker's default branch (date_complete is not None and
    # _in_past_review_rounds(...)) instead of the custom-permission shortcut.
    assert PermissionChecker()(
        workflow,
        old_editor,
        completed_assignment,
        permission_type=PermissionAssignment.PermissionType.ALL,
        review_round=review_round_number,
        default_permissions=True,
    ), "Past editor must see the completed reviewer report of the round they handled"
    assert not PermissionChecker()(
        workflow,
        old_editor,
        incomplete_assignment,
        permission_type=PermissionAssignment.PermissionType.ALL,
        review_round=review_round_number,
    ), "Past editor must not see an incomplete reviewer report of a round moved to the new editor"


@pytest.mark.django_db
def test_former_editor_filter(
    under_appeal_article_new_editor: Article, section_editor: JCOMProfile, appeal_editor: JCOMProfile
):
    """Removed-for-appeal editors are former editors, appeal editors are not."""
    workflow = under_appeal_article_new_editor.articleworkflow
    assert is_user_former_article_editor(workflow, section_editor.janeway_account), "Removed editor is former"
    assert not is_user_former_article_editor(workflow, appeal_editor.janeway_account), "Appeal editor is current"


@pytest.mark.django_db
def test_former_editor_filter_plain_past_and_non_editor(
    assigned_article: Article,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """Complements test_former_editor_filter: a plain past editor is former, a non-editor is not.

    Together the two tests cover all four ``is_user_former_article_editor`` cases (current editor -> False,
    past -> True, removed-for-appeal -> True, non-editor -> False), pinning the short-circuited implementation
    to the same behaviour as the ``get_editor_type``-based one it replaces.
    """
    workflow = assigned_article.articleworkflow
    old_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    non_editor = normal_user.janeway_account
    assert not is_user_former_article_editor(workflow, non_editor), "Non-editor is not a former editor"

    normal_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = eo_user.janeway_account
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        new_editor=non_editor,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    assert is_user_former_article_editor(workflow, old_editor), "Plain past editor is a former editor"


@pytest.mark.django_db
def test_hide_last_submitted_after_unassignment(appeal_submitted_article: Article, section_editor: JCOMProfile):
    """Last submitted is hidden to a former editor when the revision came after their removal."""
    workflow = appeal_submitted_article.articleworkflow
    assert hide_last_submitted(workflow, section_editor.janeway_account), "Newer submission must be hidden"


@pytest.mark.django_db
def test_hide_last_submitted_uses_latest_unassignment(appeal_submitted_article: Article, section_editor: JCOMProfile):
    """With several past assignments, the latest date_unassigned is the reference."""
    workflow = appeal_submitted_article.articleworkflow
    user = section_editor.janeway_account
    existing = PastEditorAssignment.objects.get(article=appeal_submitted_article, editor=user)
    PastEditorAssignment.objects.create(
        article=appeal_submitted_article,
        editor=user,
        date_assigned=existing.date_assigned,
        date_unassigned=timezone.now() + datetime.timedelta(days=1),
    )
    assert not hide_last_submitted(workflow, user), "Submission older than the latest unassignment is shown"


@pytest.mark.django_db
def test_hide_last_submitted_never_for_current_editors(appeal_submitted_article: Article, appeal_editor: JCOMProfile):
    """Current editors always see Last submitted."""
    workflow = appeal_submitted_article.articleworkflow
    assert not hide_last_submitted(workflow, appeal_editor.janeway_account), "Current editor always sees it"


@pytest.mark.django_db
def test_listing_row_for_former_editor(
    client: Client, under_appeal_article_new_editor: Article, section_editor: JCOMProfile
):
    """A former editor sees the article as Unassigned, without reviewers and issue, in the archived list."""
    article = under_appeal_article_new_editor
    issue = Issue.objects.create(
        journal=article.journal,
        issue_title="Former Editor Issue",
        short_name="former-editor-issue",
        issue_type=IssueType.objects.get(journal=article.journal, code="collection"),
    )
    article.primary_issue = issue
    article.save()

    client.force_login(section_editor.janeway_account)
    response = client.get(f"/{article.journal.code}/plugins/wjs-review-articles/editor/archived/")
    assert response.status_code == 200, "Archived list must load"
    content = response.content.decode()
    assert "Unassigned" in content, "Former editor must see the fixed Unassigned status"
    assert issue.short_name not in content, "Former editor must not see the issue"


@pytest.mark.django_db
def test_listing_row_for_appeal_editor_shows_real_state(
    client: Client, under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile
):
    """The appeal (current) editor sees the real state label and the issue, not the fixed Unassigned status."""
    article = under_appeal_article_new_editor
    workflow = article.articleworkflow
    issue = Issue.objects.create(
        journal=article.journal,
        issue_title="Appeal Editor Issue",
        short_name="appeal-editor-issue",
        issue_type=IssueType.objects.get(journal=article.journal, code="collection"),
    )
    article.primary_issue = issue
    article.save()

    client.force_login(appeal_editor.janeway_account)
    response = client.get(f"/{article.journal.code}/plugins/wjs-review-articles/editor/archived/")
    assert response.status_code == 200, "Archived list must load"
    content = response.content.decode()
    assert str(workflow.state_label) in content, "Appeal editor must see the real state label"
    assert "Unassigned" not in content, "Appeal editor must not see the fixed Unassigned status"
    assert issue.short_name in content, "Appeal editor must see the issue"


@pytest.mark.django_db
def test_listing_row_for_plain_past_editor(
    client: Client,
    assigned_article: Article,
    reviewer: JCOMProfile,
    review_form,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """A plain past editor (replaced outside an appeal) also sees Unassigned and no reviewer cell content.

    Complements ``test_listing_row_for_former_editor``, which only covers the removed-for-appeal case.
    """
    old_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    _create_review_assignment(fake_request=fake_request, reviewer_user=reviewer, assigned_article=assigned_article)

    normal_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = eo_user.janeway_account
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        new_editor=normal_user.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()

    client.force_login(old_editor)
    response = client.get(f"/{assigned_article.journal.code}/plugins/wjs-review-articles/editor/archived/")
    assert response.status_code == 200, "Archived list must load"
    content = response.content.decode()
    assert "Unassigned" in content, "Plain past editor must see the fixed Unassigned status"
    assert (
        reviewer.janeway_account.full_name() not in content
    ), "Plain past editor must not see the reviewer cell content"


@pytest.mark.django_db
def test_status_badge_hidden_for_former_editor_shown_for_appeal_editor(
    under_appeal_article_new_editor: Article,
    section_editor: JCOMProfile,
    appeal_editor: JCOMProfile,
    fake_request: HttpRequest,
):
    """Regression pin: the article page's state badge is hidden for a former editor, shown for the appeal editor."""
    workflow = under_appeal_article_new_editor.articleworkflow
    template = "wjs_review/details/sections/title.html"

    fake_request.user = section_editor.janeway_account
    former_editor_html = render_to_string(
        template,
        {
            "workflow": workflow,
            "article": workflow.article,
            "request": fake_request,
            "user": fake_request.user,
        },
    )
    assert str(workflow.state_label) not in former_editor_html, "Former editor must not see the state badge"

    fake_request.user = appeal_editor.janeway_account
    appeal_editor_html = render_to_string(
        template,
        {
            "workflow": workflow,
            "article": workflow.article,
            "request": fake_request,
            "user": fake_request.user,
        },
    )
    assert str(workflow.state_label) in appeal_editor_html, "Appeal editor must see the state badge"


@pytest.mark.django_db
def test_metadata_main_hides_last_submitted_for_removed_editor_shows_for_appeal_editor(
    appeal_submitted_article: Article,
    section_editor: JCOMProfile,
    appeal_editor: JCOMProfile,
    fake_request: HttpRequest,
):
    """Render test (not just the filter): "Last submitted" is absent for the editor removed for appeal after
    resubmission, and present for the (current) appeal editor, in the actual metadata_main.html template.
    """
    workflow = appeal_submitted_article.articleworkflow
    template = "wjs_review/details/elements/metadata_main.html"

    fake_request.user = section_editor.janeway_account
    removed_editor_html = render_to_string(
        template,
        {
            "workflow": workflow,
            "article": workflow.article,
            "request": fake_request,
            "user": fake_request.user,
        },
    )
    assert "Last submitted" not in removed_editor_html, "Editor removed for appeal must not see Last submitted"

    fake_request.user = appeal_editor.janeway_account
    appeal_editor_html = render_to_string(
        template,
        {
            "workflow": workflow,
            "article": workflow.article,
            "request": fake_request,
            "user": fake_request.user,
        },
    )
    assert "Last submitted" in appeal_editor_html, "Appeal editor must see Last submitted"


def _appeal_granted_messages(article: Article):
    return Message.objects.filter(object_id=article.pk, subject="Appeal granted")


@pytest.fixture
def appeal_article_with_messages(
    rejected_article: Article, fake_request: HttpRequest, eo_user: JCOMProfile, appeal_editor: JCOMProfile
) -> Article:
    """Return an article under appeal (new editor), keeping the messages the open-appeal flow created.

    Unlike ``under_appeal_article_new_editor``, this does not call ``cleanup_notifications_side_effects``,
    because these tests need the "Appeal granted" message it would otherwise delete.
    """
    return _under_appeal_article(rejected_article, fake_request, eo_user, appeal_editor, cleanup_side_effects=False)


@pytest.mark.django_db
def test_appeal_granted_sent_by_system_user(appeal_article_with_messages: Article):
    """The Appeal granted message is logged by the system user."""
    message = _appeal_granted_messages(appeal_article_with_messages).get()
    assert message.actor == get_system_user(appeal_article_with_messages.journal), "Actor must be system user"
    author = appeal_article_with_messages.correspondence_author
    assert author in message.recipients.all(), "Author is recipient"


@pytest.mark.django_db
def test_appeal_editor_timeline_excludes_appeal_granted(
    appeal_article_with_messages: Article, appeal_editor: JCOMProfile
):
    """The appeal editor does not see the Appeal granted message in the article timeline."""
    messages = get_messages_related_to_me(appeal_editor.janeway_account, appeal_article_with_messages)
    assert not messages.filter(subject="Appeal granted").exists(), "Appeal editor must not see Appeal granted"


@pytest.mark.django_db
def test_withdraw_after_appeal_notifies_appeal_editor_only(
    under_appeal_article_new_editor: Article,
    section_editor: JCOMProfile,
    appeal_editor: JCOMProfile,
    fake_request: HttpRequest,
    review_settings,
):
    """Withdrawing under appeal notifies the (current) appeal editor; the removed editor gets nothing.

    Driven through ``WithdrawPreprint`` directly (not the view), per spec: the article was assigned to a new
    editor (``appeal_editor``) on appeal, so ``section_editor`` (removed for appeal) must receive no message
    from the withdrawal.
    """
    article = under_appeal_article_new_editor
    workflow = article.articleworkflow
    fake_request.user = article.correspondence_author
    # conftest fixtures purge Message rows created by earlier steps; count only messages created from here on.
    cutoff = timezone.now()
    WithdrawPreprint(
        workflow=workflow,
        request=fake_request,
        form_data={"notification_subject": "Withdraw subject", "notification_body": "Withdraw body"},
    ).run()
    new_messages = Message.objects.filter(object_id=article.pk, created__gte=cutoff)
    assert new_messages.filter(
        recipients=appeal_editor.janeway_account,
    ).exists(), "Appeal editor must receive the withdraw notification"
    assert not new_messages.filter(
        recipients=section_editor.janeway_account,
    ).exists(), "Editor removed for appeal must receive no withdraw notification"


@pytest.mark.django_db
def test_removed_for_appeal_editor_gets_no_notifications(
    rejected_article: Article,
    fake_request: HttpRequest,
    eo_user: JCOMProfile,
    appeal_editor: JCOMProfile,
    section_editor: JCOMProfile,
):
    """The editor removed for appeal receives no message created by the open-appeal flow."""
    cutoff = timezone.now()
    article = _under_appeal_article(rejected_article, fake_request, eo_user, appeal_editor, cleanup_side_effects=False)
    assert not Message.objects.filter(
        object_id=article.pk,
        recipients=section_editor.janeway_account,
        created__gte=cutoff,
    ).exists(), "Editor removed for appeal must receive no message from the appeal flow"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "editor_type",
    [EditorType.ASSIGNED, EditorType.APPEAL, EditorType.PAST, EditorType.REMOVED_FOR_APPEAL],
)
def test_timeline_excludes_hijack_for_editors(
    editor_type: EditorType,
    request: pytest.FixtureRequest,
    section_editor: JCOMProfile,
    appeal_editor: JCOMProfile,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
    create_user_message,
):
    """Hijack-type messages never appear in an editor's timeline, whichever kind of editor they are.

    Fixtures are fetched lazily (``request.getfixturevalue``) because ``under_appeal_article`` and
    ``under_appeal_article_new_editor`` both open an appeal on the same underlying article: requesting
    both as plain parameters in the same test would run ``OpenAppeal`` twice on it.
    """
    if editor_type == EditorType.PAST:
        assigned_article = request.getfixturevalue("assigned_article")
        normal_user.add_account_role("section-editor", assigned_article.journal)
        fake_request.user = eo_user.janeway_account
        old_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
        SupervisorChangeEditorAssignment(
            article=assigned_article,
            assignment=WjsEditorAssignment.objects.get_current(assigned_article),
            new_editor=normal_user.janeway_account,
            request=fake_request,
            deassignment_message="bye",
            assignment_message="hello",
        ).run()
        article, user = assigned_article, old_editor
    elif editor_type == EditorType.ASSIGNED:
        article, user = request.getfixturevalue("under_appeal_article"), section_editor.janeway_account
    else:
        article = request.getfixturevalue("under_appeal_article_new_editor")
        user = appeal_editor.janeway_account if editor_type == EditorType.APPEAL else section_editor.janeway_account

    assert get_editor_type(user, article) == editor_type, "Precondition: user must have the expected editor type"
    message = create_user_message(user, article, "Hijack test", "body", [user], Message.MessageTypes.HIJACK)
    messages = get_messages_related_to_me(user, article)
    assert not messages.filter(pk=message.pk).exists(), f"{editor_type} editor must not see HIJACK messages"


MIGRATION = "plugins.wjs_review.migrations.0020_appeal_granted_system_actor"


@pytest.mark.django_db
def test_migration_moves_appeal_granted_to_system_user(
    appeal_article_with_messages: Article, appeal_editor: JCOMProfile
):
    """Existing Appeal granted messages sent by the appeal editor are reassigned to the system user."""
    article = appeal_article_with_messages
    message = _appeal_granted_messages(article).get()
    message.actor = appeal_editor.janeway_account  # simulate data from before this change
    message.save()
    importlib.import_module(MIGRATION).set_system_actor_on_appeal_granted(django_apps, None)
    message.refresh_from_db()
    assert message.actor == get_system_user(article.journal), "Old message must now be sent by the system user"


@pytest.mark.django_db
def test_migration_leaves_other_messages(
    appeal_article_with_messages: Article, article: Article, appeal_editor: JCOMProfile
):
    """Messages with other subjects, other recipients, or on articles without appeal are untouched.

    ``article`` is a plain, unrelated article (no ``OPEN_APPEAL`` decision at all) - not to be confused
    with ``appeal_article_with_messages``, which is the one under appeal.
    """
    appealed_article = appeal_article_with_messages
    message_other_article = communication_utils.log_operation(
        article=article,
        message_subject="Appeal granted",
        actor=appeal_editor.janeway_account,
        recipients=[article.correspondence_author],
    )
    message_other_subject = communication_utils.log_operation(
        article=appealed_article,
        message_subject="Something else",
        actor=appeal_editor.janeway_account,
        recipients=[appealed_article.correspondence_author],
    )
    message_other_recipient = communication_utils.log_operation(
        article=appealed_article,
        message_subject="Appeal granted",
        actor=appeal_editor.janeway_account,
        recipients=[appeal_editor.janeway_account],
    )
    importlib.import_module(MIGRATION).set_system_actor_on_appeal_granted(django_apps, None)
    for message in (message_other_article, message_other_subject, message_other_recipient):
        message.refresh_from_db()
        assert message.actor == appeal_editor.janeway_account, f"{message.subject} message must be untouched"


@pytest.mark.django_db
def test_migration_skips_journal_without_system_user(
    appeal_article_with_messages: Article, appeal_editor: JCOMProfile
):
    """The migration is a no-op when the journal has no account matching the system user's email."""
    article = appeal_article_with_messages
    message = _appeal_granted_messages(article).get()
    message.actor = appeal_editor.janeway_account
    message.save()
    setting_handler.save_setting("general", "support_email", article.journal, "nobody@example.com")
    importlib.import_module(MIGRATION).set_system_actor_on_appeal_granted(django_apps, None)
    message.refresh_from_db()
    assert message.actor == appeal_editor.janeway_account, "Message must be unchanged when there is no system user"


def _login_as(client: Client, user) -> None:
    """Log the test client in as `user`, unwrapping a JCOMProfile to its underlying Account if needed."""
    client.force_login(getattr(user, "janeway_account", user))


@pytest.mark.django_db
def test_withdraw_after_appeal_uses_dedicated_settings(
    client: Client, under_appeal_article_new_editor: Article, review_settings
):
    """Withdrawing during an appeal prefills the notification from the after-appeal settings."""
    article = under_appeal_article_new_editor
    setting_handler.save_setting(
        "wjs_review", "author_withdraws_preprint_after_appeal_subject", article.journal, "After appeal subject"
    )
    _login_as(client, article.correspondence_author)
    response = client.get(reverse("wjs_author_withdraw_preprint", args=(article.articleworkflow.pk,)))
    assert response.status_code == 200, "Withdraw page must load"
    assert (
        response.context["form"].initial["notification_subject"] == "After appeal subject"
    ), "Withdraw during appeal must use the after-appeal subject"


@pytest.mark.django_db
def test_withdraw_without_rejection_uses_standard_settings(client: Client, assigned_article: Article, review_settings):
    """Withdrawing an article never rejected keeps the standard settings."""
    setting_handler.save_setting(
        "wjs_review",
        "author_withdraws_preprint_after_appeal_subject",
        assigned_article.journal,
        "After appeal subject",
    )
    _login_as(client, assigned_article.correspondence_author)
    response = client.get(reverse("wjs_author_withdraw_preprint", args=(assigned_article.articleworkflow.pk,)))
    assert response.status_code == 200, "Withdraw page must load"
    assert (
        response.context["form"].initial["notification_subject"] != "After appeal subject"
    ), "Withdraw without rejection must use the standard subject"


@pytest.mark.django_db
def test_withdraw_after_appeal_default_text_matches_standard(
    client: Client, under_appeal_article_new_editor: Article, review_settings
):
    """Without a journal override, the after-appeal subject still equals the standard default (D6: copied text)."""
    article = under_appeal_article_new_editor
    _login_as(client, article.correspondence_author)
    response = client.get(reverse("wjs_author_withdraw_preprint", args=(article.articleworkflow.pk,)))
    assert response.status_code == 200, "Withdraw page must load"
    # The standard default ("Withdrawn") has no template syntax, so its rendered value is itself.
    assert (
        response.context["form"].initial["notification_subject"] == "Withdrawn"
    ), "Default after-appeal subject must be an unchanged copy of the standard default"


@pytest.mark.django_db
def test_latest_flag_counts_only_visible_versions(appeal_submitted_article: Article, section_editor: JCOMProfile):
    """Skipped rounds do not count: exactly one visible version is flagged ``latest``, and it is the first one."""
    workflow = appeal_submitted_article.articleworkflow
    versions = workflow.get_review_versions(section_editor.janeway_account)
    assert [v.number for v in versions][:1] != [2], "Test premise: the newest round must not be visible"
    assert [v.latest for v in versions] == [True] + [False] * (
        len(versions) - 1
    ), "Only the first visible version must be flagged latest"


@pytest.mark.django_db
def test_past_editor_without_editor_role_sees_no_past_rounds(
    appeal_submitted_article: Article, section_editor: JCOMProfile
):
    """A past assignment grants nothing once the user lost the editor role."""
    workflow = appeal_submitted_article.articleworkflow
    user = section_editor.janeway_account
    assert 1 in [v.number for v in workflow.get_review_versions(user)], "Test premise: round 1 visible as past editor"

    AccountRole.objects.filter(user=user, journal=appeal_submitted_article.journal).delete()

    numbers = [v.number for v in workflow.get_review_versions(user)]
    assert 1 not in numbers, "Without the editor role the past round must not be visible"


@pytest.mark.django_db
def test_review_versions_skip_editor_lookups_for_non_editors(appeal_submitted_article: Article):
    """Editor type and past rounds are only looked up for editors (authors, reviewers, ... never need them)."""
    workflow = appeal_submitted_article.articleworkflow
    author = appeal_submitted_article.correspondence_author

    with mock.patch("plugins.wjs_review.models.permissions.get_editor_type") as mock_editor_type:
        workflow.get_review_versions(author)

    mock_editor_type.assert_not_called()
