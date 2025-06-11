"""Tests related to the automatic assignment of articles after submission."""

import random
from typing import Callable

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, F
from django.test import Client, override_settings
from django.urls import reverse
from plugins.wjs_review.models import Message
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile, StaffWorkloadParameters

from ..communication_utils import get_system_user
from ..models import Reminder, WjsEditorAssignment

Account = get_user_model()

DEFAULT_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.default_assign_editors_to_articles"
JCOM_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.jcom_assign_editors_to_articles"
RANDOM_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.assign_editor_random"
DEFAULT_ASSIGN_EO_TO_ARTICLES = "plugins.wjs_review.events.assignment.assign_eo_to_articles"

EO_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    None: DEFAULT_ASSIGN_EO_TO_ARTICLES,
}
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
    Get the editor with the highest workload remaining, regardless the algorithm.
    :param editors: The editors among which we select the expected editor.
    :param article: the submitted article
    :return: The expected editor
    """
    if not editors:
        return None
    parameters = (
        StaffWorkloadParameters.objects.filter(
            user__in=editors,
            journal=article.journal,
        )
        .annotate(
            assignment_count=Count("user__editorassignment"),
            available_workload=F("workload") - F("assignment_count"),
        )
        .order_by("-available_workload", "id")
        .first()
    )
    return parameters.user


def get_expected_eo(editors, article):
    """
    Get the EO with the highest workload remaining, regardless the algorithm.
    :param editors: The "editors" (EO) among which we select the expected editor.
    :param article: the submitted article
    :return: The expected editor
    """
    if not editors:
        return None
    parameters = (
        StaffWorkloadParameters.objects.filter(
            user__in=editors,
            journal=article.journal,
        )
        .annotate(
            assignment_count=Count("user__articleworkflow__eo_in_charge"),
            available_workload=F("workload") - F("assignment_count"),
        )
        .order_by("-available_workload", "id")
        .first()
    )

    return parameters.user


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
        article_editors = special_issue.managing_editors.all()

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
            codes = (
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            )
            reminders = Reminder.objects.filter(code__in=codes)
            assert reminders.count() == 3
            # 2 Reminder to the SI editor and one to the director
            assert reminders.filter(recipient=editor_assignment.editor).count() == 2
            assert reminders.filter(recipient__in=directors).count() == 1


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
    main_director,
    editors,
    coauthors_setting,
    has_editors,
):
    article_editors = None

    if has_editors:
        article_editors = [main_director]

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin.janeway_account)
        expected_editor = get_expected_editor(article_editors, article)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get(article=article)
            assert editor_assignment.editor == expected_editor
            codes = (
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_1,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_2,
                Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
            )
            reminders = Reminder.objects.filter(code__in=codes)
            assert reminders.count() == 3
            # As in JCOM the director is the default editor, all reminders are sent to the director-as-editor
            assert reminders.filter(recipient=editor_assignment.editor).count() == 3
            # Any reminder is anyway set to a director
            assert reminders.filter(recipient=main_director).count() == 3


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
        article_editors = special_issue.managing_editors.all()

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
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        editor_assignment = WjsEditorAssignment.objects.get(article=article)
        assert editor_assignment.editor


@pytest.mark.parametrize(
    "assignment_function", [JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS, WJS_ARTICLE_ASSIGNMENT_FUNCTIONS]
)
@pytest.mark.parametrize("is_special_issue", [True, False])
@pytest.mark.django_db
def test_workload_decrease_editor(
    review_settings,
    admin,
    article,
    main_director,
    editors,
    coauthors_setting,
    special_issue,
    sections,
    special_issue_without_articles,
    assignment_function,
    is_special_issue,
):
    """
    Assign 3 different articles to editors starting with the same workload.
    Check that every of the 3 article gets assigned to a different editor.

    Test is repeated for every assignment function and for special issue/not special issue scenario.
    """

    if is_special_issue:
        article_editors = article.primary_issue.managing_editors.all()
    else:
        if assignment_function == WJS_ARTICLE_ASSIGNMENT_FUNCTIONS:
            article_editors = editors
        else:
            article_editors = [main_director]

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=assignment_function):
        if not is_special_issue:
            article.primary_issue = None
            article.save()
            article.refresh_from_db()

        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        for editor in article_editors:
            parameter = StaffWorkloadParameters.objects.get(
                user=editor,
                journal=article.journal,
            )
            parameter.workload = 100
            parameter.save()

        first_editor = get_expected_editor(article_editors, article)
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302
        article.refresh_from_db()
        StaffWorkloadParameters.objects.get(user=first_editor, journal=article.journal).refresh_from_db()

        editor_assignment = WjsEditorAssignment.objects.get(article=article)
        assert editor_assignment.editor == first_editor

        second_article = Article.objects.create(
            journal=article.journal,
            title="A title",
            current_step=4,
            owner=admin.janeway_account,
            correspondence_author=admin.janeway_account,
            section=random.choice(sections),
        )
        if is_special_issue:
            special_issue_without_articles.articles.add(second_article)
            second_article.refresh_from_db()

        url = reverse("submit_review", args=(second_article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302
        second_article.refresh_from_db()

        second_editor_assignment = WjsEditorAssignment.objects.get(article=second_article)
        if is_special_issue or assignment_function == WJS_ARTICLE_ASSIGNMENT_FUNCTIONS:
            assert second_editor_assignment.editor != first_editor
        else:
            assert second_editor_assignment.editor == main_director.janeway_account

        third_article = Article.objects.create(
            journal=article.journal,
            title="A title",
            current_step=4,
            owner=admin.janeway_account,
            correspondence_author=admin.janeway_account,
            section=random.choice(sections),
        )
        if is_special_issue:
            special_issue_without_articles.articles.add(third_article)
            third_article.refresh_from_db()

        if is_special_issue or assignment_function == WJS_ARTICLE_ASSIGNMENT_FUNCTIONS:
            assert get_expected_editor(article_editors, third_article) != first_editor
            assert get_expected_editor(article_editors, third_article) != second_editor_assignment.editor
        else:
            assert get_expected_editor(article_editors, third_article) == main_director.janeway_account


@pytest.mark.parametrize("is_special_issue", [True, False])
@pytest.mark.django_db
def test_workload_decrease_eo(
    review_settings,
    admin,
    article,
    editors,
    coauthors_setting,
    special_issue,
    sections,
    special_issue_without_articles,
    is_special_issue,
    eo_group: Group,
    create_jcom_user: Callable,
):
    """
    Assign 3 different articles to editors starting with the same workload.
    Check that every of the 3 article gets assigned to a different editor.

    Test is repeated for every assignment function and for special issue/not special issue scenario.
    """

    eo_1 = create_jcom_user("eo_1")
    eo_1.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_1, journal=article.journal, workload=10)
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_2, journal=article.journal, workload=10)
    article_editors = Account.objects.filter(groups__name="EO")

    with override_settings(WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS=EO_ARTICLE_ASSIGNMENT_FUNCTIONS):
        if not is_special_issue:
            article.primary_issue = None
            article.save()
            article.refresh_from_db()

        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        for editor in article_editors:
            parameter = StaffWorkloadParameters.objects.get(
                user=editor,
                journal=article.journal,
            )
            parameter.workload = 100
            parameter.save()

        first_editor = get_expected_eo(article_editors, article)

        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302
        article.refresh_from_db()

        assert article.articleworkflow.eo_in_charge == first_editor

        second_article = Article.objects.create(
            journal=article.journal,
            title="A title",
            current_step=4,
            owner=admin.janeway_account,
            correspondence_author=admin.janeway_account,
            section=random.choice(sections),
        )
        if is_special_issue:
            special_issue_without_articles.articles.add(second_article)
            second_article.refresh_from_db()

        url = reverse("submit_review", args=(second_article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302
        second_article.refresh_from_db()

        assert second_article.articleworkflow.eo_in_charge != first_editor

        third_article = Article.objects.create(
            journal=article.journal,
            title="A title",
            current_step=4,
            owner=admin.janeway_account,
            correspondence_author=admin.janeway_account,
            section=random.choice(sections),
        )
        if is_special_issue:
            third_article.primary_issue = special_issue_without_articles
            third_article.save()
            third_article.refresh_from_db()

        assert get_expected_eo(article_editors, third_article) != first_editor
        assert get_expected_eo(article_editors, third_article) != second_article.articleworkflow.eo_in_charge


@pytest.mark.django_db
def test_automatic_assignment_no_author_msg(
    review_settings: Callable,  # noqa: ARG001
    article: Article,
    main_director: JCOMProfile,
):
    """When an author submits an article, he should not receive the notification of the editor assignment."""
    author = article.correspondence_author.janeway_account
    article.authors.set([author])
    article.current_step = 5
    article.save()
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(author)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302
        article.refresh_from_db()
        assert article.stage == "Assigned"
        assert article.articleworkflow.state == "EditorSelected"
        editor_assignment = WjsEditorAssignment.objects.get(article=article)
        editor = editor_assignment.editor
        assert editor == main_director.janeway_account

        messages_to_editor = Message.objects.filter(
            object_id=article.id,
            content_type=ContentType.objects.get_for_model(Article),
            recipients__in=[editor],
        )
        assert messages_to_editor.count() == 1
        actor = messages_to_editor.first().actor
        assert actor != author
        assert actor == get_system_user()
