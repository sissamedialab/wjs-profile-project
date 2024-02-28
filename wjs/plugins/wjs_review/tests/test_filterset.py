from unittest.mock import patch

import pytest
from core.models import Account
from django.http import HttpRequest
from journal import models as journal_models

from ..filters import ArticleWorkflowFilter
from ..models import ArticleWorkflow


@pytest.mark.django_db
def test_articleworkflowfilter(journal: journal_models.Journal, create_set_of_articles_with_assignments):
    """
    ArticleWorkflowFilter queries.

    Different queries are tested in a single test case because setup is expensive.
    """
    workflows = ArticleWorkflow.objects.all()

    # filter by article title
    article_filterer = ArticleWorkflowFilter(data={"article": "reviewer"}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__title__icontains="reviewer"))

    # filter by article id
    existing_id = workflows.first().article.id
    article_filterer = ArticleWorkflowFilter(data={"article": existing_id}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__id=existing_id))

    # filter by editor email
    article_filterer = ArticleWorkflowFilter(data={"editor": "eee@a.it"}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__editorassignment__editor__email="eee@a.it"))

    # filter by author email
    article_filterer = ArticleWorkflowFilter(data={"author": "aaa@a.it"}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__authors__email="aaa@a.it"))

    # filter by reviewer email
    article_filterer = ArticleWorkflowFilter(data={"reviewer": "rrr@a.it"}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__reviewassignment__reviewer__email="rrr@a.it"))


@pytest.mark.django_db
def test_articleworkflowfilter_filter_status(
    fake_request: HttpRequest,
    eo_user: Account,  # noqa
    journal: journal_models.Journal,  # noqa
    director: Account,  # noqa
    review_settings,
):
    """
    ArticleWorkflowFilter query by status.

    Different queries are tested in a single test case because setup is expensive.
    """
    workflows = ArticleWorkflow.objects.all()

    filters = {
        "eo_unread_messages": "with_unread_messages",
        "my_unread_messages": "with_unread_messages",
        "with_unread_messages": "with_unread_messages",
        "with_reviews": "with_reviews",
        "with_pending_reviews": "with_pending_reviews",
        "with_all_completed_reviews": "with_all_completed_reviews",
    }

    for status_filter, qs_method in filters.items():
        with patch(f"plugins.wjs_review.models.ArticleWorkflowQuerySet.{qs_method}") as mock_queryset:
            article_filterer = ArticleWorkflowFilter(
                data={"status": status_filter},
                queryset=workflows,
                request=fake_request,
                journal=journal,
            )
            # call the filter method, it must be called low level because filter_queryset asserts that return value
            # is a queryset which is not in this case because we mocked it
            article_filterer.filters["status"].filter(workflows, status_filter)
            mock_queryset.assert_called_once()
