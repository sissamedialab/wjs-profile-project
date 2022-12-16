"""Tests related to the automatic assignment of articles after submission."""
import pytest
from django.test import Client, override_settings
from django.urls import reverse
from review.models import EditorAssignment

from wjs.jcom_profile.models import EditorAssignmentParameters

WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    None: "wjs.jcom_profile.events.assignment.default_assign_editors_to_articles",
}


def get_expected_editor(editors, article):
    """
    Get the editor with the lowest workload, regardless the algorithm.
    :param editors: The editors among which we select the expected editor.
    :param article: the submitted article
    :return: The expected editor
    """
    lowest_workload = 1000
    expected_editor = None
    if not editors:
        return expected_editor
    for editor in editors:
        params = EditorAssignmentParameters.objects.get(
            editor=editor,
            journal=article.journal,
        )
        if params.workload < lowest_workload:
            expected_editor = params.editor
            lowest_workload = params.workload
    return expected_editor


@pytest.mark.parametrize(
    "has_editors",
    (
        False,
        True,
    ),
)
@pytest.mark.django_db
def test_default_normal_issue_articles_automatic_assignment(
    admin,
    article,
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
            editor_assignment = EditorAssignment.objects.get(article=article)
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
    admin,
    article,
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
            editor_assignment = EditorAssignment.objects.get(article=article)

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
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
    has_editors,
):
    article_editors = None

    jcom_assignment_settings = WJS_ARTICLE_ASSIGNMENT_FUNCTIONS
    jcom_assignment_settings[
        article.journal.code
    ] = "wjs.jcom_profile.events.assignment.jcom_assign_editors_to_articles"

    if has_editors:
        article_editors = directors

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=jcom_assignment_settings):
        client = Client()
        client.force_login(admin)
        expected_editor = get_expected_editor(article_editors, article)

        url = reverse("submit_review", args=(article.pk,))

        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = EditorAssignment.objects.get(article=article)

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
    admin,
    article,
    coauthors_setting,
    special_issue,
    has_editors,
):
    article_editors = None
    jcom_assignment_settings = WJS_ARTICLE_ASSIGNMENT_FUNCTIONS
    jcom_assignment_settings[
        article.journal.code
    ] = "wjs.jcom_profile.events.assignment.jcom_assign_editors_to_articles"

    if has_editors:
        article_editors = special_issue.editors.all()

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=jcom_assignment_settings):
        client = Client()
        client.force_login(admin)
        expected_editor = get_expected_editor(article_editors, article)

        url = reverse("submit_review", args=(article.pk,))

        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = EditorAssignment.objects.get(article=article)

            assert editor_assignment.editor == expected_editor
