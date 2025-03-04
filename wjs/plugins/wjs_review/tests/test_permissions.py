"""Tests related to the permissons module."""

import pytest
from django.contrib.auth import get_user_model
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from ..forms import MessageForm
from ..models import ArticleWorkflow
from ..permissions import (
    can_edit_note,
    has_director_role_by_article,
    is_one_of_the_authors,
)

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
