import pytest
from core.models import AccountRole
from django.http import HttpRequest
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from ..logic import SupervisorChangeEditorAssignment
from ..models import ArticleWorkflow, EditorDecision, WjsEditorAssignment
from ..permissions import (
    EditorType,
    get_editor_type,
    is_article_editor,
    is_past_article_editor,
)
from ..templatetags.wjs_review import is_user_former_article_editor


@pytest.mark.django_db
def test_assigned_editor(assigned_article: Article, section_editor: JCOMProfile):
    """The current editor of an article without appeal is an assigned editor."""
    result = get_editor_type(section_editor.janeway_account, assigned_article)
    assert result == EditorType.ASSIGNED, "Current editor without appeal must be ASSIGNED"


@pytest.mark.django_db
def test_appeal_editor(under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile):
    """The new editor assigned on appeal is an appeal editor."""
    result = get_editor_type(appeal_editor.janeway_account, under_appeal_article_new_editor)
    assert result == EditorType.APPEAL, "Editor assigned on appeal must be APPEAL"


@pytest.mark.django_db
def test_removed_for_appeal_editor(under_appeal_article_new_editor: Article, section_editor: JCOMProfile):
    """The editor removed when the appeal was opened is a removed-for-appeal editor."""
    result = get_editor_type(section_editor.janeway_account, under_appeal_article_new_editor)
    assert result == EditorType.REMOVED_FOR_APPEAL, "Editor removed on appeal must be REMOVED_FOR_APPEAL"


@pytest.mark.django_db
def test_same_editor_on_appeal_is_assigned(under_appeal_article: Article, section_editor: JCOMProfile):
    """The editor who rejected and is re-assigned to the appeal is treated as assigned editor (spec D2)."""
    result = get_editor_type(section_editor.janeway_account, under_appeal_article)
    assert result == EditorType.ASSIGNED, "Same editor on appeal must be ASSIGNED"


@pytest.mark.django_db
def test_past_editor(
    assigned_article: Article,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """An editor replaced outside an appeal is a past editor."""
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
    result = get_editor_type(section_editor.janeway_account, assigned_article)
    assert result == EditorType.PAST, "Replaced editor must be PAST"


@pytest.mark.django_db
def test_past_editor_reassigned_is_current(
    assigned_article: Article,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """A current assignment wins over past ones: a replaced-then-reassigned editor is ASSIGNED."""
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
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        new_editor=section_editor.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    result = get_editor_type(section_editor.janeway_account, assigned_article)
    assert result == EditorType.ASSIGNED, "Re-assigned editor must be treated by the current role"


@pytest.mark.django_db
def test_non_editor(assigned_article: Article, normal_user: JCOMProfile):
    """A user with no editor assignment, present or past, has no editor type."""
    assert get_editor_type(normal_user.janeway_account, assigned_article) is None, "Non editor must be None"


def _reassign_current_editor(article, new_editor, eo_user, fake_request):
    """Replace the current editor with ``new_editor`` through a plain supervisor reassignment."""
    new_editor.add_account_role("section-editor", article.journal)
    fake_request.user = eo_user.janeway_account
    SupervisorChangeEditorAssignment(
        article=article,
        assignment=WjsEditorAssignment.objects.get_current(article),
        new_editor=new_editor.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()


@pytest.mark.django_db
def test_editor_swapped_mid_appeal_is_appeal_editor(
    appeal_submitted_article: Article,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """An editor assigned while the appeal is still unresolved takes over as appeal editor."""
    _reassign_current_editor(appeal_submitted_article, normal_user, eo_user, fake_request)
    result = get_editor_type(normal_user.janeway_account, appeal_submitted_article)
    assert result == EditorType.APPEAL, "Editor swapped in during an unresolved appeal must be APPEAL"


@pytest.mark.django_db
def test_editor_assigned_after_appeal_resolved_is_assigned(
    appeal_submitted_article: Article,
    appeal_editor: JCOMProfile,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """An editor assigned by plain reassignment after the appeal was resolved is not an appeal editor."""
    workflow = appeal_submitted_article.articleworkflow
    EditorDecision.objects.create(
        workflow=workflow,
        editor=appeal_editor.janeway_account,
        review_round=workflow.article.reviewround_set.order_by("round_number").last(),
        decision=ArticleWorkflow.Decisions.ACCEPT,
    )
    _reassign_current_editor(appeal_submitted_article, normal_user, eo_user, fake_request)
    result = get_editor_type(normal_user.janeway_account, appeal_submitted_article)
    assert result == EditorType.ASSIGNED, "Editor assigned after the appeal was resolved must be ASSIGNED"
    result = get_editor_type(appeal_editor.janeway_account, appeal_submitted_article)
    assert result != EditorType.APPEAL, "The former appeal editor is no longer the current editor"


@pytest.mark.django_db
def test_past_editor_without_editor_role_is_not_an_editor(
    under_appeal_article_new_editor: Article, section_editor: JCOMProfile
):
    """A past assignment only counts while the user still has an editor role: all definitions agree."""
    article = under_appeal_article_new_editor
    user = section_editor.janeway_account
    workflow = article.articleworkflow
    assert get_editor_type(user, article) == EditorType.REMOVED_FOR_APPEAL, "Test premise: removed for appeal"
    assert is_user_former_article_editor(workflow, user), "Test premise: former editor"

    AccountRole.objects.filter(user=user, journal=article.journal).delete()

    assert get_editor_type(user, article) is None, "Past assignment without editor role must not give an editor type"
    assert not is_user_former_article_editor(workflow, user), "Without the editor role the user is not a former editor"
    assert not is_past_article_editor(workflow, user), "is_past_article_editor must agree"


@pytest.mark.django_db
def test_current_editor_without_editor_role_is_not_an_editor(
    under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile
):
    """A current assignment only counts while the user still has an editor role, like is_article_editor."""
    article = under_appeal_article_new_editor
    user = appeal_editor.janeway_account
    assert get_editor_type(user, article) == EditorType.APPEAL, "Test premise: current appeal editor"

    AccountRole.objects.filter(user=user, journal=article.journal).delete()

    assert (
        get_editor_type(user, article) is None
    ), "Current assignment without editor role must not give an editor type"
    assert not is_article_editor(article.articleworkflow, user), "is_article_editor must agree"
