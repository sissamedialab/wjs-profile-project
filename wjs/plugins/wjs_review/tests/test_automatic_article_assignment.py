"""Tests related to the automatic assignment of articles after submission."""

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from wjs.jcom_profile.models import EditorAssignmentParameters

from ..models import WjsEditorAssignment

DEFAULT_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.default_assign_editors_to_articles"
JCOM_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.jcom_assign_editors_to_articles"
RANDOM_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.assign_editor_random"

WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    None: DEFAULT_ASSIGN_EDITORS_TO_ARTICLES,
}

JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    "JCOM": JCOM_ASSIGN_EDITORS_TO_ARTICLES,
}

RANDOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    "JCOM": RANDOM_ASSIGN_EDITORS_TO_ARTICLES,
}


def get_expected_editor(editors, article):
    """
    Get the editor with the lowest workload, regardless the algorithm.
    :param editors: The editors among which we select the expected editor.
    :param article: the submitted article
    :return: The expected editor
    """
    if not editors:
        return None
    parameters = (
        EditorAssignmentParameters.objects.filter(
            editor__in=editors,
            journal=article.journal,
        )
        .order_by("workload", "id")
        .first()
    )
    return parameters.editor


@pytest.mark.parametrize(
    "has_editors",
    (
        False,
        True,
    ),
)
@pytest.mark.django_db
def test_default_normal_issue_articles_automatic_assignment(
    review_settings,
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
    has_editors,
):
    article_editors = None

    if has_editors:
        article_editors = editors

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        expected_editor = get_expected_editor(article_editors, article)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get(article=article)
            assert editor_assignment.editor == expected_editor


@pytest.mark.parametrize(
    "has_editors",
    (
        False,
        True,
    ),
)
@pytest.mark.django_db
def test_default_special_issue_articles_automatic_assignment(
    review_settings,
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
    special_issue,
    has_editors,
):
    article_editors = None

    if has_editors:
        article_editors = special_issue.editors.all()

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        expected_editor = get_expected_editor(article_editors, article)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get_current(article=article)
            assert editor_assignment.editor == expected_editor


@pytest.mark.parametrize(
    "has_editors",
    (
        False,
        True,
    ),
)
@pytest.mark.django_db
def test_jcom_normal_issue_articles_automatic_assignment(
    review_settings,
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
    has_editors,
):
    article_editors = None

    if has_editors:
        article_editors = directors

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        expected_editor = get_expected_editor(article_editors, article)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get(article=article)
            assert editor_assignment.editor == expected_editor


@pytest.mark.parametrize(
    "has_editors",
    (
        False,
        True,
    ),
)
@pytest.mark.django_db
def test_jcom_special_issue_articles_automatic_assignment(
    review_settings,
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
    special_issue,
    has_editors,
):
    article_editors = None

    if has_editors:
        article_editors = special_issue.editors.all()

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        expected_editor = get_expected_editor(article_editors, article)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get(article=article)
            assert editor_assignment.editor == expected_editor


@pytest.mark.django_db
def test_random_automatic_assignment(
    review_settings,
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
    special_issue,
):
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=RANDOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        editor_assignment = WjsEditorAssignment.objects.get(article=article)
        assert editor_assignment.editor
