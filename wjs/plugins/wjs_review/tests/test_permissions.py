"""Tests related to the permissons module."""

import pytest
from django.contrib.auth import get_user_model
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from ..models import ArticleWorkflow
from ..permissions import has_director_role_by_article, is_one_of_the_authors

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
