import random
from typing import Type
from unittest.mock import patch

import pytest
from core.models import Account
from django.contrib.auth.models import Group
from django.http import HttpRequest
from django.utils import timezone
from django.views.generic.list import MultipleObjectMixin
from journal import models as journal_models

from wjs.jcom_profile.models import JCOMProfile

from .. import views
from ..filters import EOArticleWorkflowFilter, status_choices
from ..logic import (
    states_when_article_is_considered_archived_for_review,
    states_when_article_is_considered_in_review,
)
from ..models import ArticleWorkflow, WjsEditorAssignment, WorkflowReviewAssignment


@pytest.mark.django_db
def test_articleworkflowfilter(journal: journal_models.Journal, create_set_of_articles_with_assignments):
    """
    ArticleWorkflowFilter queries.

    Different queries are tested in a single test case because setup is expensive.
    """
    workflows = ArticleWorkflow.objects.all()

    # filter by article title
    article_filterer = EOArticleWorkflowFilter(data={"article": "reviewer"}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__title__icontains="reviewer"))

    # filter by article id
    existing_id = workflows.first().article.id
    article_filterer = EOArticleWorkflowFilter(data={"article": existing_id}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__id=existing_id))

    # filter by editor email
    article_filterer = EOArticleWorkflowFilter(data={"editor": "eee@a.it"}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__editorassignment__editor__email="eee@a.it"))

    # filter by author email
    article_filterer = EOArticleWorkflowFilter(data={"author": "aaa@a.it"}, queryset=workflows, journal=journal)
    assert article_filterer.qs.exists()
    assert set(article_filterer.qs) == set(workflows.filter(article__authors__email="aaa@a.it"))

    # filter by reviewer email
    article_filterer = EOArticleWorkflowFilter(data={"reviewer": "rrr@a.it"}, queryset=workflows, journal=journal)
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
            article_filterer = EOArticleWorkflowFilter(
                data={"status": status_filter},
                queryset=workflows,
                request=fake_request,
                journal=journal,
            )
            # call the filter method, it must be called low level because filter_queryset asserts that return value
            # is a queryset which is not in this case because we mocked it
            article_filterer.filters["status"].filter(workflows, status_filter)
            mock_queryset.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    ("none", "Accepted", "EditorSelected"),
)
def test_articleworkflowfilter_status_choices(
    fake_request: HttpRequest,
    eo_user: Account,  # noqa
    journal: journal_models.Journal,  # noqa
    director: Account,  # noqa
    review_settings,
    create_set_of_articles_with_assignments,
    state: str,
):
    """
    EOArticleWorkflowFilter.status choices are set depending on actual queryset.
    """
    if state != "none":
        ArticleWorkflow.objects.update(state=state)
    workflows = ArticleWorkflow.objects.all()

    filterset = EOArticleWorkflowFilter(
        queryset=workflows,
        request=fake_request,
        journal=journal,
    )
    if state != "none":
        expected = [""] + [item[0] for item in status_choices()] + [state]
        assert {item[0] for item in filterset.filters["status"].field.choices} == set(expected)
    else:
        states = workflows.values_list("state", flat=True).distinct()
        expected = [""] + [item[0] for item in status_choices()] + list(states)
        assert {item[0] for item in filterset.filters["status"].field.choices} == set(expected)


class TestListViews:
    @classmethod
    def _create_user(cls, create_jcom_user, journal: journal_models.Journal, role: str) -> JCOMProfile:
        created = create_jcom_user(role)
        if role == "eo":
            created.groups.add(Group.objects.get(name="EO"))
        else:
            created.add_account_role(role, journal)
        return created

    @classmethod
    @pytest.fixture
    def setup_data(cls, review_settings, create_jcom_user, create_submitted_articles, journal):
        """Create articles in random states."""
        articles = create_submitted_articles(journal, count=30)
        for article in articles:
            article.articleworkflow.state = random.choice(
                states_when_article_is_considered_in_review + states_when_article_is_considered_archived_for_review,
            )
            article.articleworkflow.save()

        roles = ("section-editor", "eo", "director", "author", "reviewer")
        users = {}
        for role in roles:
            users[role] = cls._create_user(create_jcom_user, journal, role)

        article_qs = ArticleWorkflow.objects.all()
        for state_list in (
            states_when_article_is_considered_in_review,
            states_when_article_is_considered_archived_for_review,
        ):
            for workflow in random.sample(list(article_qs.filter(state__in=state_list)), k=3):
                WjsEditorAssignment.objects.create(
                    article=workflow.article,
                    editor=users["section-editor"],
                    editor_type="section-editor",
                )
            for workflow in random.sample(list(article_qs.filter(state__in=state_list)), k=3):
                WorkflowReviewAssignment.objects.create(
                    reviewer=users["reviewer"],
                    article=workflow.article,
                    editor=users["section-editor"],
                    date_due=timezone.now(),
                    is_complete=False,
                )
            for workflow in random.sample(list(article_qs.filter(state__in=state_list)), k=3):
                WorkflowReviewAssignment.objects.create(
                    reviewer=users["reviewer"],
                    article=workflow.article,
                    editor=users["section-editor"],
                    date_due=timezone.now(),
                    is_complete=True,
                )
            for workflow in random.sample(list(article_qs.filter(state__in=state_list)), k=3):
                workflow.article.authors.add(users["author"])
                workflow.article.authors.add(users["director"])
        return users

    @pytest.mark.parametrize(
        "view_class,role",
        (
            (views.EditorPending, "section-editor"),
            (views.EOPending, "eo"),
            (views.DirectorPending, "director"),
            (views.AuthorPending, "author"),
            (views.ReviewerPending, "reviewer"),
        ),
    )
    @pytest.mark.django_db
    def test_pending_views(
        self,
        setup_data,
        fake_request: HttpRequest,
        view_class: Type[MultipleObjectMixin],
        role: str,
    ):
        """
        Pending views returns article only in pending states.
        """
        user = setup_data[role]
        # GET must be set to empty dict, otherwise it's interpreted by filterset and used as filter parameters
        # resulting in an empty queryset
        fake_request.GET = {}
        fake_request.user = user
        view_obj = view_class()
        view_obj.kwargs = {}
        view_obj.request = fake_request
        view_obj.setup(fake_request)
        qs = view_obj.get_queryset()
        assert qs.exists()
        if role == "reviewer":
            assert qs.filter(
                article__reviewassignment__reviewer=user,
                article__reviewassignment__is_complete=False,
            ).exists()
        else:
            assert set(qs.values_list("state", flat=True)).issubset(states_when_article_is_considered_in_review)
            if role == "author":
                assert qs.filter(article__authors=user).exists()
            elif role == "section-editor":
                assert qs.filter(article__editorassignment__editor=user).exists()
            elif role == "director":
                assert not qs.filter(article__authors=user).exists()

    @pytest.mark.parametrize(
        "view_class,role",
        (
            (views.EditorArchived, "section-editor"),
            (views.EOArchived, "eo"),
            (views.DirectorArchived, "director"),
            (views.AuthorArchived, "author"),
            (views.ReviewerArchived, "reviewer"),
        ),
    )
    @pytest.mark.django_db
    def test_archived_views(
        self,
        setup_data,
        fake_request: HttpRequest,
        view_class: Type[MultipleObjectMixin],
        role: str,
    ):
        """
        Archived views returns article only in archived states.
        """
        user = setup_data[role]
        # GET must be set to empty dict, otherwise it's interpreted by filterset and used as filter parameters
        # resulting in an empty queryset
        fake_request.GET = {}
        fake_request.user = user
        view_obj = view_class()
        view_obj.kwargs = {}
        view_obj.request = fake_request
        view_obj.setup(fake_request)
        qs = view_obj.get_queryset()
        assert qs.exists()
        if role == "reviewer":
            assert qs.filter(
                article__reviewassignment__reviewer=user,
                article__reviewassignment__is_complete=True,
            ).exists()
        else:
            assert set(qs.values_list("state", flat=True)).issubset(
                states_when_article_is_considered_archived_for_review
            )
            if role == "author":
                assert qs.filter(article__authors=user).exists()
            elif role == "section-editor":
                assert qs.filter(article__editorassignment__editor=user).exists()
            elif role == "director":
                assert not qs.filter(article__authors=user).exists()
