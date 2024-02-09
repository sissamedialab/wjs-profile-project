"""Tests related to the permissons module."""
import pytest
from django.contrib.auth import get_user_model
from submission.models import Article

from ..models import ArticleWorkflow
from ..permissions import is_one_of_the_authors

Account = get_user_model()


@pytest.mark.django_db
def test_is_one_of_the_authors(assigned_article: Article):
    """Nomen omen."""
    user: Account = assigned_article.correspondence_author
    instance: ArticleWorkflow = assigned_article.articleworkflow
    assert is_one_of_the_authors(instance, user)
