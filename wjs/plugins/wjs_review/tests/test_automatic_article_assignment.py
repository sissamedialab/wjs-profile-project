"""Tests related to the automatic assignment of articles after submission."""

import random
from typing import Callable

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.test import Client, override_settings
from django.urls import reverse
from plugins.wjs_review.models import Message
from submission.models import Article

from wjs.jcom_profile import constants
from wjs.jcom_profile.constants import EO_GROUP
from wjs.jcom_profile.models import JCOMProfile, StaffWorkloadParameters

from ..communication_utils import get_system_user
from ..events.assignment import (
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
)
from ..logic import (
    BaseAssignToEditor,
    states_where_article_needs_editor,
    states_where_article_needs_eo_in_charge,
)
from ..models import ArticleWorkflow, Reminder, WjsEditorAssignment
from ..plugin_settings import STAGE

Account = get_user_model()

DEFAULT_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.default_assign_editors_to_articles"
JCOM_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.jcom_assign_editors_to_articles"
RANDOM_ASSIGN_EDITORS_TO_ARTICLES = "plugins.wjs_review.events.assignment.assign_editor_random"
DEFAULT_ASSIGN_EO_TO_ARTICLES = "plugins.wjs_review.events.assignment.get_select_eo_by_workload"

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

        expected_editor = None
        if has_editors:
            parameters = StaffWorkloadParameters.objects.filter(user__in=article_editors)
            expected_editor = get_selected_editor_by_workload(parameters, journal=article.journal)

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

        expected_editor = None
        if has_editors:
            parameters = StaffWorkloadParameters.objects.filter(user__in=article_editors)
            expected_editor = get_selected_editor_by_workload(parameters, journal=article.journal)

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

        expected_editor = None
        if has_editors:
            parameters = StaffWorkloadParameters.objects.filter(user__in=article_editors)
            expected_editor = get_selected_editor_by_workload(parameters, journal=article.journal)

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

        expected_editor = None
        if has_editors:
            parameters = StaffWorkloadParameters.objects.filter(user__in=article_editors)
            expected_editor = get_selected_editor_by_workload(parameters, journal=article.journal)

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

    article.articleworkflow.state = ArticleWorkflow.ReviewStates.TO_BE_REVISED
    if is_special_issue:
        article_editors = article.primary_issue.managing_editors.all()
    else:
        if assignment_function == WJS_ARTICLE_ASSIGNMENT_FUNCTIONS:
            article_editors = editors
        else:
            article_editors = [main_director.janeway_account]

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

        first_editor = article_editors[0]
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
            parameters = StaffWorkloadParameters.objects.filter(user__in=article_editors)
            assert get_selected_editor_by_workload(parameters, journal=article.journal) != first_editor
            assert (
                get_selected_editor_by_workload(parameters, journal=article.journal) != second_editor_assignment.editor
            )
        else:
            parameters = StaffWorkloadParameters.objects.filter(user=main_director.janeway_account)
            assert (
                get_selected_editor_by_workload(parameters, journal=article.journal) == main_director.janeway_account
            )


@pytest.mark.parametrize("assign_published", (True, False))
@pytest.mark.django_db
def test_get_selected_eo_by_workload(
    review_settings,
    journal,
    eo_group: Group,
    create_jcom_user: Callable,
    submitted_articles,
    published_articles,
    eo_user,
    assign_published,
):
    """
    Select the EO with the highest available workload from the available users.

    The test simulates a scenario where EOs are associated with specific workloads and verifies that the user with
    the highest available workload is correctly selected.

    :param review_settings: Settings for the review process (fixture)
    :param journal: The journal object involved in the test (fixture)
    :param eo_group: Group representing editorial officers (fixture, type: Group)
    :param create_jcom_user: Callable fixture to create users with JCOM profile
    :param submitted_articles: A set of articles submitted to the journal (fixture)
    :param published_articles: A set of articles in published state to the journal (fixture)
    :param eo_user: The default editorial officer user in the database (fixture)
    :param assign_published: Parameter to indicate if the articles should be assigned to the asserted EO or not
    """
    for submitted in submitted_articles:
        submitted.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        submitted.articleworkflow.save()

    eo_1 = create_jcom_user("eo_1")
    eo_1.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_1, journal=journal, workload=12)
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_2, journal=journal, workload=1)
    # Assigning base eo user zero workload to exclude it
    StaffWorkloadParameters.objects.filter(user=eo_user, journal=journal).update(workload=0)

    for submitted in submitted_articles:
        submitted.articleworkflow.eo_in_charge = random.choice([eo_1, eo_2])
        submitted.articleworkflow.save()
    if assign_published:
        for submitted in published_articles:
            submitted.articleworkflow.eo_in_charge = eo_1
            submitted.articleworkflow.save()

    eo_users = Account.objects.filter(groups__name=EO_GROUP)
    eo_parameters = StaffWorkloadParameters.objects.filter(journal=journal, user__in=eo_users, workload__gt=0)
    eo_selected = get_select_eo_by_workload(eo_parameters)
    # eo_1 is always selected because published articles are ignored in the assignment algorithm
    assert eo_selected == eo_1.janeway_account


@pytest.mark.parametrize("state", ArticleWorkflow.ReviewStates.choices, ids=ArticleWorkflow.ReviewStates.values)
@pytest.mark.django_db
def test_get_selected_eo_by_workload_include_by_state(
    review_settings,
    journal,
    eo_group: Group,
    create_jcom_user: Callable,
    submitted_articles,
    article,
    submitted_article,
    eo_user,
    state,
):
    """
    Select the EO with the highest available workload from the available users, by the state of assigned articles.

    The test checks the state of an article assigned to one of the EO test users and verifies the selection returns
    a different EO user depending on the selected state of the article.

    The idea is that articles in some states (e.g. published, etc.) should not count when computing
    the available workload of an EO user.

    :param review_settings: Settings for the review process (fixture)
    :param journal: The journal object involved in the test (fixture)
    :param eo_group: Group representing editorial officers (fixture, type: Group)
    :param create_jcom_user: Callable fixture to create users with JCOM profile
    :param submitted_articles: A set of articles submitted to the journal (fixture)
    :param article: Test article submitted to the journal (fixture)
    :param submitted_article: A set of articles in published state to the journal (fixture)
    :param eo_user: The default editorial officer user in the database (fixture)
    :param state: The state of the extra article to test if it's counted in the selection logic
    """
    assert article not in submitted_articles
    for submitted in submitted_articles:
        submitted.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        submitted.articleworkflow.save()
    eo_1 = create_jcom_user("eo_1")
    eo_1.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_1, journal=journal, workload=10)
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_2, journal=journal, workload=10)
    # Assigning base eo user zero workload to exclude it
    StaffWorkloadParameters.objects.filter(user=eo_user, journal=journal).update(workload=0)

    counted = state[0] in states_where_article_needs_eo_in_charge

    # Assigning articles to test users
    # Submitted articles are assigned to assign the same number of articles to eo_1 and eo_2
    # so that the state of the extra assigned article determines if eo_1 or eo_2 has the most workload
    for x, submitted in enumerate(submitted_articles):
        if x < 5:
            submitted.articleworkflow.eo_in_charge = eo_1
        else:
            submitted.articleworkflow.eo_in_charge = eo_2
        submitted.articleworkflow.save()

    # Assign an additional article to test user eo_1, if the state of the article is counted in the selection logic
    # eo_1 will have more assigned workload and thus eo_2 will be chosen, otherwise eo_1 will be chosen because it has
    # a lower id
    assert article not in submitted_articles
    article.articleworkflow.state = state[0]
    article.articleworkflow.eo_in_charge = eo_1
    article.articleworkflow.save()

    # If state is counted the additional article is reflected in the queryset (which is used
    # in get_select_eo_by_workload to generate the assigned articles annotation
    if counted:
        assert (
            ArticleWorkflow.objects.filter(
                eo_in_charge=eo_1, state__in=states_where_article_needs_eo_in_charge
            ).count()
            == 6
        )
    else:
        assert (
            ArticleWorkflow.objects.filter(
                eo_in_charge=eo_1, state__in=states_where_article_needs_eo_in_charge
            ).count()
            == 5
        )
    assert (
        ArticleWorkflow.objects.filter(eo_in_charge=eo_2, state__in=states_where_article_needs_eo_in_charge).count()
        == 5
    )

    eo_users = Account.objects.filter(groups__name=EO_GROUP)
    eo_parameters = StaffWorkloadParameters.objects.filter(journal=journal, user__in=eo_users, workload__gt=0)
    eo_selected = get_select_eo_by_workload(eo_parameters)
    if counted:
        assert eo_selected == eo_2.janeway_account
    else:
        assert eo_selected == eo_1.janeway_account


@pytest.mark.parametrize("assign_published", (True, False))
@pytest.mark.django_db
def test_get_selected_editor_by_workload(
    review_settings,
    editors,
    journal,
    submitted_articles,
    fake_request,
    assign_published,
):
    """
    Select the editor with the highest available workload from the available users for the given journal.

    The test simulates a scenario where editors are associated with specific workloads and verifies that the user with
    the highest available workload is correctly selected.

    The test takes into account the pre-existing workload

    :param review_settings: Settings for the review process (fixture)
    :param editors: Set of 3 editors (fixture)
    :param journal: The journal object involved in the test (fixture)
    :param submitted_articles: A set of articles submitted to the journal (fixture)
    :param fake_request: Mocked HTTP request object used for testing purposes (fixture)
    :param assign_published: Parameter to indicate if the articles should be assigned to the editor or not (fixture)
    """
    for submitted in submitted_articles:
        submitted.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        submitted.articleworkflow.save()
    StaffWorkloadParameters.objects.filter(journal=journal).update(workload=10)

    for submitted in submitted_articles:
        editor = (
            Account.objects.filter(accountrole__role__slug=constants.SECTION_EDITOR_ROLE, accountrole__journal=journal)
            .exclude(email=editors[0].email)
            .order_by("?")
            .first()
        )
        BaseAssignToEditor(editor=editor, article=submitted, request=fake_request, first_assignment=True).run()

    editor_selected = get_selected_editor_by_workload(
        StaffWorkloadParameters.objects.filter(user__accountrole__role__slug=constants.SECTION_EDITOR_ROLE),
        journal=journal,
    )
    assert editor_selected == editors[0]


@pytest.mark.parametrize("state", ArticleWorkflow.ReviewStates.choices)
@pytest.mark.django_db
def test_get_selected_editor_by_workload_include_by_state(
    review_settings,
    editors,
    journal,
    submitted_articles,
    article,
    fake_request,
    state,
):
    """
    Select the editor with the highest available workload from the available users, by the state of assigned articles.

    The test checks the state of an article assigned to one of the editor test users and verifies the selection returns
    a different editor user depending on the selected state of the article.

    :param review_settings: Settings for the review process (fixture)
    :param editors: Set of 3 editors (fixture)
    :param journal: The journal object involved in the test (fixture)
    :param submitted_articles: A set of articles submitted to the journal (fixture)
    :param article: Test article submitted to the journal (fixture)
    :param fake_request: Mocked HTTP request object used for testing purposes (fixture)
    :param state: The state of the extra article to test if it's counted in the selection logic
    """
    for submitted in submitted_articles:
        submitted.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        submitted.articleworkflow.save()
    for editor in editors:
        StaffWorkloadParameters.objects.filter(user=editor, journal=journal).update(workload=10)

    # Assigning articles to test users
    # Submitted articles are assigned to allow test_editor_1 to have 1 article less than test_editor_2 assigned
    # by default so that the state of the extra assigned article determines if test_editor_1 or test_editor_2
    # has the most workload
    for x, submitted in enumerate(submitted_articles):
        if x < 3:
            BaseAssignToEditor(editor=editors[0], article=submitted, request=fake_request, first_assignment=True).run()
        elif x < 6:
            BaseAssignToEditor(editor=editors[1], article=submitted, request=fake_request, first_assignment=True).run()
        else:
            BaseAssignToEditor(editor=editors[2], article=submitted, request=fake_request, first_assignment=True).run()

    counted = state[0] in states_where_article_needs_editor
    article.articleworkflow.state = state[0]
    article.articleworkflow.save()
    BaseAssignToEditor(editor=editors[0], article=article, request=fake_request, first_assignment=True).run()

    editor_selected = get_selected_editor_by_workload(
        StaffWorkloadParameters.objects.filter(user__accountrole__role__slug=constants.SECTION_EDITOR_ROLE),
        journal=journal,
    )
    if counted:
        assert editor_selected == editors[1]
    else:
        assert editor_selected == editors[0]


@pytest.mark.django_db
def test_workload_decrease_eo(
    review_settings,
    admin,
    journal,
    coauthors_setting,
    sections,
    eo_group: Group,
    create_jcom_user: Callable,
    submitted_articles,
    eo_user,
):
    """
    Assign 3 different articles to each eo user starting with the same workload.
    Check that every of the 3 article gets assigned to a different eo user.
    """
    for submitted in submitted_articles:
        submitted.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        submitted.articleworkflow.save()
    eo_1 = create_jcom_user("eo_1")
    eo_1.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_1, journal=journal, workload=100)
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.create(user=eo_2, journal=journal, workload=100)
    StaffWorkloadParameters.objects.filter(user=eo_user, journal=journal).update(workload=101)

    for x, article in enumerate(submitted_articles):
        if x < 3:
            article.articleworkflow.eo_in_charge = eo_1
        elif x < 6:
            article.articleworkflow.eo_in_charge = eo_2
        elif x < 9:
            article.articleworkflow.eo_in_charge = eo_user
        article.articleworkflow.save()

    with override_settings(WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS=EO_ARTICLE_ASSIGNMENT_FUNCTIONS):
        first_article = Article.objects.create(
            journal=journal,
            title="A title",
            current_step=4,
            owner=admin.janeway_account,
            correspondence_author=admin.janeway_account,
            section=random.choice(sections),
            stage=STAGE,
        )

        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(first_article.pk,))

        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302
        first_article.refresh_from_db()

        assert first_article.articleworkflow.eo_in_charge == eo_user.janeway_account

        second_article = Article.objects.create(
            journal=journal,
            title="A title",
            current_step=4,
            owner=admin.janeway_account,
            correspondence_author=admin.janeway_account,
            section=random.choice(sections),
            stage=STAGE,
        )

        url = reverse("submit_review", args=(second_article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302
        second_article.refresh_from_db()

        assert second_article.articleworkflow.eo_in_charge == eo_1.janeway_account

        Article.objects.create(
            journal=article.journal,
            title="A title",
            current_step=4,
            owner=admin.janeway_account,
            correspondence_author=admin.janeway_account,
            section=random.choice(sections),
            stage=STAGE,
        )

        eo_users = Account.objects.filter(groups__name=EO_GROUP)
        eo_parameters = StaffWorkloadParameters.objects.filter(
            journal=article.journal, user__in=eo_users, workload__gt=0
        )
        assert get_select_eo_by_workload(eo_parameters) == eo_2.janeway_account


@pytest.mark.skip("Old submission is not used anymore")
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
        assert actor == get_system_user(journal=article.journal)
