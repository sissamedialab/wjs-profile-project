"""Tests related to the permissions module."""

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.utils import timezone
from plugins.wjs_review.forms import MessageForm
from plugins.wjs_review.logic import AssignToReviewer
from plugins.wjs_review.models import (
    ArticleWorkflow,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from plugins.wjs_review.permissions import (
    can_edit_note,
    can_see_other_user_name,
    has_director_role_by_article,
    is_one_of_the_authors,
    main_role_by_article,
)
from review.models import ReviewForm
from submission.models import Article

from wjs.jcom_profile import constants
from wjs.jcom_profile.models import JCOMProfile

from .test_helpers import _create_review_assignment

Account = get_user_model()


@pytest.mark.django_db
def test_is_one_of_the_authors(assigned_article: Article):
    """Nomen omen."""
    user: Account = assigned_article.correspondence_author
    instance: ArticleWorkflow = assigned_article.articleworkflow
    assert is_one_of_the_authors(instance, user)


@pytest.mark.parametrize("is_author", [True, False])
@pytest.mark.django_db
def test_is_director(assigned_article: Article, director: JCOMProfile, is_author: bool):
    """Director has director permission on an article only if it's not in the authors."""
    if is_author:
        assigned_article.authors.add(director.janeway_account)
        assert is_one_of_the_authors(assigned_article.articleworkflow, director)
        assert not has_director_role_by_article(assigned_article.articleworkflow, director)
    else:
        assert not is_one_of_the_authors(assigned_article.articleworkflow, director)
        assert has_director_role_by_article(assigned_article.articleworkflow, director)


@pytest.mark.parametrize("actor_is_eo", [True, False])
@pytest.mark.django_db
def test_can_edit_note(
    eo_user: JCOMProfile,
    eo_group: JCOMProfile,
    article: Article,
    normal_user: JCOMProfile,
    jcom_user: JCOMProfile,
    actor_is_eo: bool,
):
    """
    Check that the user can edit a note if they are the actor or if hey are in the eo group.
    """
    if actor_is_eo:
        normal_user.groups.add(eo_group)
    form = MessageForm(
        actor=normal_user.janeway_account,
        target=article,
        initial={"recipients": [normal_user]},
        note=True,
        data={
            "actor": normal_user.janeway_account,
            "subject": "subject2",
            "body": "body2",
        },
    )
    msg = form.save()
    # This is true only if the both aree part of the EO
    assert can_edit_note(eo_user, msg) is actor_is_eo
    # Actor can always edit note
    assert can_edit_note(normal_user.janeway_account, msg)
    # Other non-eo user can never edit note
    assert not can_edit_note(jcom_user.janeway_account, msg)


@pytest.mark.django_db
def test_can_see_other_user_name(
    assigned_article: Article,
    eo_user: JCOMProfile,
    reviewer: JCOMProfile,
    fake_request: HttpRequest,
    review_form: ReviewForm,  # noqa: ARG001
):
    """Test who can see whose name."""
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    author = assigned_article.correspondence_author
    _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=reviewer,
        assigned_article=assigned_article,
    )
    wf = assigned_article.articleworkflow
    # EO can see everyone
    assert can_see_other_user_name(instance=wf, actor=eo_user, target=author)
    assert can_see_other_user_name(instance=wf, actor=eo_user, target=editor)
    assert can_see_other_user_name(instance=wf, actor=eo_user, target=reviewer)
    # Editor can see everyone
    assert can_see_other_user_name(instance=wf, actor=editor, target=author)
    assert can_see_other_user_name(instance=wf, actor=editor, target=reviewer)
    # Author cannot see the editor and the reviewer
    assert not can_see_other_user_name(instance=wf, actor=author, target=editor)
    assert not can_see_other_user_name(instance=wf, actor=author, target=reviewer)
    # TODO: typesetter

    # The system should work also if editor did I-will-review
    AssignToReviewer(
        workflow=wf,
        reviewer=editor,
        editor=editor,
        form_data={
            "acceptance_due_date": timezone.now().strftime("%Y-%m-%d"),
            "message": "random message",
        },
        request=fake_request,
    ).run()
    assert can_see_other_user_name(instance=wf, actor=editor, target=author)
    assert can_see_other_user_name(instance=wf, actor=editor, target=reviewer)


@pytest.mark.django_db
def test_main_role_by_article(
    assigned_article_with_reviewer: Article,
    fake_request: HttpRequest,
    review_form: ReviewForm,  # noqa: ARG001
    eo_user: JCOMProfile,
):
    """Test what we mean by main-role."""
    article = assigned_article_with_reviewer
    aw = article.articleworkflow
    editor = WjsEditorAssignment.objects.get_current(article).editor
    reviewer = WorkflowReviewAssignment.objects.get(article=article, editor=editor).reviewer
    author = article.correspondence_author

    # Simple case: all actors have only one role
    assert main_role_by_article(article=aw, user=eo_user) == constants.EO_GROUP
    # NB: technically, the editor has the "section-editor" role, but we report "editor"
    assert main_role_by_article(article=aw, user=editor) == constants.EDITOR_ROLE
    assert main_role_by_article(article=aw, user=reviewer) == constants.REVIEWER_ROLE
    assert main_role_by_article(article=aw, user=author) == constants.AUTHOR_ROLE

    # The system should work also if editor did I-will-review
    AssignToReviewer(
        workflow=article.articleworkflow,
        reviewer=editor,
        editor=editor,
        form_data={
            "acceptance_due_date": timezone.now().strftime("%Y-%m-%d"),
            "message": "random message",
        },
        request=fake_request,
    ).run()
    assert main_role_by_article(article=aw, user=editor) == constants.EDITOR_ROLE

    # If the editor is also a director, the editor role is more important
    editor.add_account_role(constants.DIRECTOR_ROLE, article.journal)
    assert main_role_by_article(article=aw, user=editor) == constants.EDITOR_ROLE

    # same reasoning if the editor is the main director
    editor.add_account_role(constants.DIRECTOR_MAIN_ROLE, article.journal)
    assert main_role_by_article(article=aw, user=editor) == constants.EDITOR_ROLE
