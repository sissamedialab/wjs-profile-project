import datetime
from typing import Literal

import pytest
from core.models import Account
from django.http import HttpRequest
from django.utils.timezone import now
from submission.models import Article

from .. import conditions
from ..logic import HandleDecision
from ..models import ArticleWorkflow, EditorRevisionRequest, WjsEditorAssignment
from ..states import ToBeRevised


@pytest.mark.django_db
@pytest.mark.parametrize(
    "decision,user_type,completed,success",
    (
        (ArticleWorkflow.Decisions.ACCEPT, "author", False, False),
        (ArticleWorkflow.Decisions.ACCEPT, "editor", False, False),
        (ArticleWorkflow.Decisions.ACCEPT, "other", False, False),
        (ArticleWorkflow.Decisions.ACCEPT, "author", True, False),
        (ArticleWorkflow.Decisions.ACCEPT, "editor", True, False),
        (ArticleWorkflow.Decisions.ACCEPT, "other", True, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "editor", False, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "author", False, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "other", False, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "editor", True, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "author", True, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "other", True, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "editor", False, True),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "author", False, True),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "other", False, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "editor", True, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "author", True, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "other", True, False),
    ),
)
def test_pending_revision_request(
    assigned_article: Article,
    jcom_user: Account,
    fake_request: HttpRequest,
    decision: ArticleWorkflow.Decisions,
    user_type: Literal["author", "editor", "other"],
    completed: bool,
    success: bool,
):
    """Pending revision request are returned for the Corresponding author / editor and not for normal users."""

    section_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    if user_type == "author":
        user = assigned_article.correspondence_author
    elif user_type == "editor":
        user = section_editor
    else:
        user = jcom_user

    assert not conditions.pending_revision_request(assigned_article.articleworkflow, user)

    fake_request.user = user
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
        "withdraw_notice": "notice",
        "date_due": now().date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()

    if completed:
        EditorRevisionRequest.objects.filter(article=assigned_article).update(date_completed=now())

    revision_request = conditions.pending_revision_request(assigned_article.articleworkflow, user)
    if success:
        assert revision_request
    else:
        assert not revision_request


@pytest.mark.django_db
@pytest.mark.parametrize(
    "decision,user_type,completed,success",
    (
        (ArticleWorkflow.Decisions.ACCEPT, "author", False, False),
        (ArticleWorkflow.Decisions.ACCEPT, "editor", False, False),
        (ArticleWorkflow.Decisions.ACCEPT, "other", False, False),
        (ArticleWorkflow.Decisions.ACCEPT, "author", True, False),
        (ArticleWorkflow.Decisions.ACCEPT, "editor", True, False),
        (ArticleWorkflow.Decisions.ACCEPT, "other", True, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "editor", False, True),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "author", False, True),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "other", False, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "editor", True, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "author", True, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, "other", True, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "editor", False, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "author", False, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "other", False, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "editor", True, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "author", True, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "other", True, False),
    ),
)
def test_pending_edit_metadata_request(
    assigned_article: Article,
    jcom_user: Account,
    fake_request: HttpRequest,
    decision: ArticleWorkflow.Decisions,
    user_type: Literal["author", "non_author"],
    completed: bool,
    success: bool,
):
    """
    Pending edit metadata (technical revision) request are returned depending on user role.

    They are returned for the Corresponding author / editor and not for normal users.
    """

    section_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    if user_type == "author":
        user = assigned_article.correspondence_author
    elif user_type == "editor":
        user = section_editor
    else:
        user = jcom_user

    assert not conditions.pending_revision_request(assigned_article.articleworkflow, user)

    fake_request.user = user
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
        "withdraw_notice": "notice",
        "date_due": now().date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()

    if completed:
        EditorRevisionRequest.objects.filter(article=assigned_article).update(date_completed=now())

    revision_request = conditions.pending_edit_metadata_request(assigned_article.articleworkflow, user)
    if success:
        assert revision_request
    else:
        assert not revision_request


@pytest.mark.django_db
@pytest.mark.parametrize(
    "decision,user_type,actions",
    (
        (ArticleWorkflow.Decisions.ACCEPT, "author", []),
        (ArticleWorkflow.Decisions.ACCEPT, "editor", []),
        (ArticleWorkflow.Decisions.ACCEPT, "other", []),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "author", ["submits new version", "confirms previous manuscript"]),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "editor", ["postpone author revision deadline"]),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "other", []),
        (
            ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            "author",
            ["edit metadata"],
        ),
        (
            ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            "editor",
            ["postpone author edit metadata deadline"],
        ),
        (
            ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            "other",
            [],
        ),
    ),
)
def test_revision_actions(
    assigned_article: Article,
    jcom_user: Account,
    fake_request: HttpRequest,
    decision: ArticleWorkflow.Decisions,
    user_type: Literal["author", "non_author"],
    actions: list[str],
):
    """
    ToBeRevised state actions are triggered for proper user / conditions.

    - Paper in ACCEPTED state: no actions available for any user
    - Paper in MINOR_REVISION state:
        - author can submit revision / confirm revision
        - editor can postpone deadline
        - other users have no actions
    - Paper in TECHNICAL_REVISION state:
        - author can edit metadata
        - editor can postpone deadline
        - other users have no actions

    """
    section_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    if user_type == "author":
        user = assigned_article.correspondence_author
    elif user_type == "editor":
        user = section_editor
    else:
        user = jcom_user

    fake_request.user = user
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
        "withdraw_notice": "notice",
        "date_due": now().date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()

    for action in ToBeRevised.article_actions:
        expected = action.name in actions
        if action.is_available(assigned_article.articleworkflow, user):
            assert expected
            configuration = action.as_dict(assigned_article.articleworkflow, user)
            if configuration["name"] in actions:
                assert bool(configuration["url"])
            else:
                raise AssertionError(f"Unexpected action {configuration['name']} for user {user_type}")
        else:
            assert not expected
