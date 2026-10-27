"""Tests related to the automatic assignment of articles after submission."""

import random
from datetime import timedelta
from typing import Callable

import freezegun
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from journal.models import Issue, IssueType
from plugins.hydra.models import LinkedArticle, LinkType
from plugins.wjs_review.models import Message
from submission import models as submission_models
from submission.models import Article, KeywordArticle, Section

from wjs.jcom_profile import constants
from wjs.jcom_profile.constants import EO_GROUP
from wjs.jcom_profile.models import JCOMProfile, StaffKeyword, StaffWorkloadParameters

from ..communication_utils import get_system_user
from ..events.assignment import (
    annotate_keyword_ranking,
    annotate_workload_availability,
    get_available_editor_parameters,
    get_eo_selection_function,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
    rank_editor_parameters,
    select_eo_by_linked_article,
)
from ..logic import (
    BaseAssignToEditor,
    states_where_article_needs_editor,
    states_where_article_needs_eo_in_charge,
)
from ..models import (
    ArticleWorkflow,
    AutomaticAssignmentAudit,
    PastEditorAssignment,
    Reminder,
    WjsEditorAssignment,
)
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


def _selected_editor(parameters, journal):
    """Return the Account chosen by get_selected_editor_by_workload, or None when nobody is available."""
    parameter = get_selected_editor_by_workload(parameters, journal=journal)
    return parameter.user if parameter else None


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
            expected_editor = _selected_editor(parameters, journal=article.journal)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get(article=article)
            assert editor_assignment.editor == expected_editor


@pytest.mark.parametrize(
    "section_name,has_editors",
    (
        ("Erratum", False),
        ("Addendum", False),
        ("Article", True),
    ),
)
@pytest.mark.django_db
def test_erratum_articles_automatic_assignment(
    review_settings,
    admin,
    corrections_article,
    article,
    directors,
    editors,
    coauthors_setting,
    section_name,
    has_editors,
):
    article_editors = None

    if section_name in ("Erratum", "Addendum"):
        checked_article = corrections_article
    else:
        checked_article = article

    if has_editors:
        article_editors = editors

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)

        expected_editor = None
        if has_editors:
            parameters = StaffWorkloadParameters.objects.filter(user__in=article_editors)
            expected_editor = get_selected_editor_by_workload(parameters, journal=checked_article.journal).user

        url = reverse("submit_review", args=(checked_article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        checked_article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get(article=checked_article)
            assert editor_assignment.editor == expected_editor
        else:
            assert not WjsEditorAssignment.objects.filter(article=checked_article).exists()


@pytest.mark.django_db
def test_editor_assignment_disabled(
    review_settings,
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
):
    expected_editor = editors[0]
    for editor in editors[1:]:
        StaffWorkloadParameters.objects.filter(user=editor, journal=article.journal).update(enabled=False)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        editor_assignment = WjsEditorAssignment.objects.get(article=article)
        assert editor_assignment.editor == expected_editor


@pytest.mark.parametrize(
    "vacancy_start, vacancy_end",
    (
        (
            False,
            True,
        ),
        (
            True,
            True,
        ),
        (
            True,
            False,
        ),
    ),
)
@pytest.mark.django_db
def test_editor_assignment_vacancy(
    review_settings, admin, article, directors, editors, coauthors_setting, vacancy_start, vacancy_end
):
    expected_editor = editors[0]
    for editor in editors[1:]:
        if vacancy_start:
            StaffWorkloadParameters.objects.filter(user=editor, journal=article.journal).update(
                vacancy_start=timezone.now() - timedelta(days=5)
            )
        if vacancy_end:
            StaffWorkloadParameters.objects.filter(user=editor, journal=article.journal).update(
                vacancy_end=timezone.now() + timedelta(days=5)
            )

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        editor_assignment = WjsEditorAssignment.objects.get(article=article)
        assert editor_assignment.editor == expected_editor


@pytest.mark.parametrize(
    "vacancy_start, vacancy_end,enabled",
    (
        (
            False,
            True,
            True,
        ),
        (
            False,
            True,
            False,
        ),
        (
            True,
            True,
            True,
        ),
        (
            True,
            True,
            False,
        ),
        (
            True,
            False,
            True,
        ),
        (
            True,
            False,
            False,
        ),
    ),
)
@pytest.mark.django_db
def test_editor_availability(
    review_settings, admin, article, directors, editors, coauthors_setting, vacancy_start, vacancy_end, enabled
):
    """
    Test the editor's availability and related vacancy behavior.

    This test checks the availability of an editor as determined by vacancy
    period start and end dates, and examines the correctness of vacancy dates
    and the editor's active enabled status.

    :param review_settings: A pytest fixture for review settings.
    :type review_settings: Any
    :param admin: A pytest fixture for admin user.
    :type admin: Any
    :param article: A pytest fixture for an article instance.
    :type article: Any
    :param directors: A pytest fixture for directors.
    :type directors: Any
    :param editors: A pytest fixture for editors.
    :type editors: Any
    :param coauthors_setting: A pytest fixture for coauthor settings.
    :type coauthors_setting: Any
    :param vacancy_start: Indicates if the vacancy start date is set.
    :type vacancy_start: bool
    :param vacancy_end: Indicates if the vacancy end date is set.
    :type vacancy_end: bool
    :param enabled: Indicates if the editor is enabled.
    :type enabled: bool
    """
    editor = editors[0]
    if vacancy_start:
        StaffWorkloadParameters.objects.filter(user=editor, journal=article.journal).update(
            vacancy_start=timezone.now() - timedelta(days=1)
        )
    if vacancy_end:
        StaffWorkloadParameters.objects.filter(user=editor, journal=article.journal).update(
            vacancy_end=timezone.now() + timedelta(days=1)
        )
    StaffWorkloadParameters.objects.filter(user=editor, journal=article.journal).update(enabled=enabled)

    params = editor.jcomprofile.parameters(article.journal)
    assert editor.jcomprofile.is_enabled_as_editor(journal=article.journal) is enabled
    if vacancy_end or vacancy_start and enabled:
        assert editor.jcomprofile.is_available_as_editor(journal=article.journal) is False
    if not vacancy_end and not vacancy_start:
        assert editor.jcomprofile.is_available_as_editor(journal=article.journal) is enabled
        assert editor.jcomprofile.vacancy_dates(journal=article.journal) == ""
    if vacancy_end:
        assert editor.jcomprofile.vacancy_dates(journal=article.journal).endswith(
            params.vacancy_end.strftime("%Y-%m-%d")
        )
    if vacancy_start:
        assert editor.jcomprofile.vacancy_dates(journal=article.journal).startswith(
            f'from {params.vacancy_start.strftime("%Y-%m-%d")}'
        )


@pytest.mark.parametrize(
    "start_offset, end_offset, expected_available",
    (
        (None, None, True),  # no window set -> available
        (-10, -5, True),  # window entirely in the past -> available
        (5, 10, True),  # window entirely in the future -> available
        (-5, 5, False),  # window active now -> on vacation
        (0, 0, False),  # window is exactly today (inclusive bounds) -> on vacation
        (-1, None, False),  # open-ended from a past start -> on vacation
        (5, None, True),  # open-ended from a future start -> available
        (None, 5, False),  # open-ended until a future end -> on vacation
        (None, -1, True),  # open-ended until a past end -> available
    ),
)
@pytest.mark.django_db
def test_get_available_editor_parameters_vacancy_window(
    journal,
    editors,
    start_offset,
    end_offset,
    expected_available,
):
    """
    get_available_editor_parameters excludes a record only when a vacancy window is set and today is in it.

    Windows may be open-ended: only a start means "on leave from that date on", only an end means "on leave until
    that date". A record with both bounds null has no window set and must never be excluded.
    """
    today = timezone.localdate()
    target = editors[0]
    params = StaffWorkloadParameters.objects.get(user=target, journal=journal)
    params.vacancy_start = today + timedelta(days=start_offset) if start_offset is not None else None
    params.vacancy_end = today + timedelta(days=end_offset) if end_offset is not None else None
    params.save()

    available = get_available_editor_parameters(editors, journal)

    assert available.filter(user=target).exists() is expected_available
    # The other editors have no vacancy set and must always remain available.
    assert available.filter(user=editors[1]).exists()
    assert available.filter(user=editors[2]).exists()


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
            expected_editor = _selected_editor(parameters, journal=article.journal)

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
            expected_editor = _selected_editor(parameters, journal=article.journal)

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
            expected_editor = _selected_editor(parameters, journal=article.journal)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

        article.refresh_from_db()
        if has_editors:
            editor_assignment = WjsEditorAssignment.objects.get(article=article)
            assert editor_assignment.editor == expected_editor


@pytest.mark.django_db
def test_jcom_assignment_primary_issue_without_managing_editors_uses_directors(
    review_settings,
    admin,
    article,
    main_director,
    coauthors_setting,
    journal,
    sections,
):
    """Regression test for the JCOM assignment always-truthy-manager bug.

    ``jcom_assign_editors_to_articles`` used to check ``article.primary_issue.managing_editors`` directly,
    which is a related manager and thus always truthy, so any article with a ``primary_issue`` was routed
    to the (empty) special-issue pool even when that issue has no managing editors, and never reached a
    director. The article's primary issue here has no managing editors, so the assignment must fall back
    to the main director, exactly as it does when the article has no primary issue at all.
    """
    issue_without_managing_editors = Issue.objects.create(
        issue_title="Issue without managing editors",
        short_name="issue-without-managing-editors",
        journal=journal,
        date_open=timezone.now() - timezone.timedelta(days=1),
        date_close=timezone.now() + timezone.timedelta(days=1),
        issue_type=IssueType.objects.get(journal=journal, code="collection"),
    )
    issue_without_managing_editors.allowed_sections.set(Section.objects.filter(journal=journal).order_by("?")[:3])
    issue_without_managing_editors.articles.add(article)
    # Article.primary_issue is set by a signal triggered by the m2m save, so the in-memory article object
    # must be reloaded to see the change (see the "special_issue" fixture for the same caveat).
    article.refresh_from_db()
    assert article.primary_issue == issue_without_managing_editors, "Article must have the primary issue set."
    assert not article.primary_issue.managing_editors.exists(), "Precondition: issue must have no managing editors."

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin.janeway_account)

        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302, "Submission should redirect to the next step."

        article.refresh_from_db()
        editor_assignment = WjsEditorAssignment.objects.get(article=article)
        assert (
            editor_assignment.editor == main_director.janeway_account
        ), "Main director must be assigned when the primary issue has no managing editors."


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
            assert _selected_editor(parameters, journal=article.journal) != first_editor
            assert _selected_editor(parameters, journal=article.journal) != second_editor_assignment.editor
        else:
            parameters = StaffWorkloadParameters.objects.filter(user=main_director.janeway_account)
            assert _selected_editor(parameters, journal=article.journal) == main_director.janeway_account


@pytest.mark.parametrize(
    "section_name,has_eo",
    (
        ("Erratum", False),
        ("Addendum", False),
        ("Article", True),
    ),
)
@pytest.mark.django_db
def test_get_selected_eo_by_article_section(
    review_settings,
    journal,
    eo_group: Group,
    create_jcom_user: Callable,
    submitted_articles,
    published_articles,
    eo_user,
    section_name,
    has_eo,
):
    """
    Test the EO (Editorial Office) selection process based on article section.

    This test ensures that the function responsible for selecting an EO user, based
    on the section of a given article and their workload, behaves as expected. It
    evaluates the assignment of EO users for specified section categories like
    Erratum, Addendum, or Article. Different conditions of workload and section
    types are accounted for in the test.

    :param review_settings: Review settings configuration fixture
    :param journal: Journal object fixture
    :param eo_group: Group object associated with EO role
    :param create_jcom_user: Callable to create test users with specific roles
    :param submitted_articles: Fixture providing a list of submitted articles
    :param published_articles: Fixture providing a list of published articles
    :param eo_user: Default EO user fixture
    :param section_name: Name of the section associated with the test article
    :param has_eo: Boolean indicating if an EO is expected to be selected
    :return: None
    """
    submitted_parent = submitted_articles[0]
    submitted_parent.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
    submitted_parent.articleworkflow.save()

    eo_1 = create_jcom_user("eo_1")
    eo_1.groups.add(eo_group)
    StaffWorkloadParameters.objects.update_or_create(user=eo_1, journal=journal, defaults={"workload": 12})
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.update_or_create(user=eo_2, journal=journal, defaults={"workload": 1})
    # Assigning base eo user zero workload to exclude it
    StaffWorkloadParameters.objects.filter(user=eo_user, journal=journal).update(workload=0)

    section, _ = Section.objects.get_or_create(name=section_name, journal=journal)
    test_article = submitted_articles[1]
    test_article.section = section
    test_article.save()
    relationship = None
    if section_name == "Erratum":
        relationship = LinkType.ERRATUM
    elif section_name == "Addendum":
        relationship = LinkType.ADDENDUM
    if relationship:
        LinkedArticle.objects.create(
            from_article=submitted_parent,
            to_article=test_article,
            relationship=relationship,
        )

    eo_users = Account.objects.filter(groups__name=EO_GROUP)
    eo_parameters = StaffWorkloadParameters.objects.filter(journal=journal, user__in=eo_users, workload__gt=0)
    eo_selection_function = get_eo_selection_function(test_article)
    eo_user = eo_selection_function(test_article, eo_parameters)
    if has_eo:
        assert eo_user
    else:
        assert not eo_user


@pytest.mark.parametrize("relationship", (LinkType.ERRATUM, LinkType.ADDENDUM))
@pytest.mark.django_db
def test_select_eo_by_linked_article(
    review_settings,
    journal,
    eo_group: Group,
    create_jcom_user: Callable,
    submitted_articles,
    relationship,
):
    """A correction inherits the EO in charge of the article that it corrects."""
    corrected_article, correction = submitted_articles[0], submitted_articles[1]

    eo_1 = create_jcom_user("eo_1")
    eo_1.groups.add(eo_group)
    parameters = StaffWorkloadParameters.objects.update_or_create(
        user=eo_1, journal=journal, defaults={"workload": 12}
    )[0]
    corrected_article.articleworkflow.eo_in_charge = eo_1.janeway_account
    corrected_article.articleworkflow.save()

    eo_parameters = StaffWorkloadParameters.objects.filter(pk=parameters.pk)

    # Without a link, no EO can be inherited.
    assert select_eo_by_linked_article(correction, eo_parameters) is None

    LinkedArticle.objects.create(
        from_article=corrected_article,
        to_article=correction,
        relationship=relationship,
    )

    assert select_eo_by_linked_article(correction, eo_parameters) == eo_1.janeway_account


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
    StaffWorkloadParameters.objects.update_or_create(user=eo_1, journal=journal, defaults={"workload": 12})
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.update_or_create(user=eo_2, journal=journal, defaults={"workload": 1})
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
    eo_selected = get_select_eo_by_workload(article=published_articles[0], users_parameters=eo_parameters)
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
    StaffWorkloadParameters.objects.update_or_create(user=eo_1, journal=journal, defaults={"workload": 10})
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.update_or_create(user=eo_2, journal=journal, defaults={"workload": 10})
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
    eo_selected = get_select_eo_by_workload(article=article, users_parameters=eo_parameters)
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

    editor_selected = _selected_editor(
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

    editor_selected = _selected_editor(
        StaffWorkloadParameters.objects.filter(user__accountrole__role__slug=constants.SECTION_EDITOR_ROLE),
        journal=journal,
    )
    if counted:
        assert editor_selected == editors[1]
    else:
        assert editor_selected == editors[0]


@pytest.mark.django_db
def test_get_selected_editor_by_workload_returns_annotated_parameter(review_settings, editors, journal):
    """The winning StaffWorkloadParameters row is returned, carrying its workload annotations."""
    StaffWorkloadParameters.objects.filter(journal=journal).update(workload=3)
    StaffWorkloadParameters.objects.filter(user=editors[1], journal=journal).update(workload=7)

    parameter = get_selected_editor_by_workload(
        StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal),
        journal=journal,
    )

    assert isinstance(parameter, StaffWorkloadParameters), "the parameter row must be returned, not the Account"
    assert parameter.user == editors[1], "the editor with the most available workload must win"
    assert parameter.workload == 7, "the returned row must carry its own workload"
    assert parameter.available_workload == 7, "with no open assignments, available workload equals workload"


@pytest.mark.django_db
def test_get_selected_editor_by_workload_returns_none_without_candidates(review_settings, journal):
    """With an empty candidate set, None is returned (callers must not dereference .user blindly)."""
    parameter = get_selected_editor_by_workload(StaffWorkloadParameters.objects.none(), journal=journal)

    assert parameter is None, "no candidates must yield None"


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
    StaffWorkloadParameters.objects.update_or_create(user=eo_1, journal=journal, defaults={"workload": 100})
    eo_2 = create_jcom_user("eo_2")
    eo_2.groups.add(eo_group)
    StaffWorkloadParameters.objects.update_or_create(user=eo_2, journal=journal, defaults={"workload": 100})
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
        assert get_select_eo_by_workload(article=article, users_parameters=eo_parameters) == eo_2.janeway_account


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


@pytest.mark.django_db
def test_annotate_keyword_ranking_keeps_no_match_editors(article, editors, keywords):
    """Editors with no keyword overlap with the article are annotated, not excluded."""
    article.keywords.add(keywords[0])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)

    result = annotate_keyword_ranking(article, parameters)

    assert result.count() == 3
    for parameter in result:
        assert parameter.matching_keywords_count == 0
        assert parameter.max_matching_keyword_weight is None


@pytest.mark.django_db
def test_annotate_keyword_ranking_annotates_weight_and_count(article, editors, keywords):
    """Each parameter is annotated with its own matching-keyword count and max weight."""
    article.keywords.add(keywords[0], keywords[1], keywords[2])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    param_two_matches, param_one_match, param_no_match = parameters.order_by("id")

    StaffKeyword.objects.create(parameters=param_two_matches, keyword=keywords[0], weight=1)
    StaffKeyword.objects.create(parameters=param_two_matches, keyword=keywords[1], weight=1)
    StaffKeyword.objects.create(parameters=param_one_match, keyword=keywords[0], weight=5)
    # param_no_match's keyword isn't one of the article's, so it must still be annotated at
    # zero/None, not excluded.
    StaffKeyword.objects.create(parameters=param_no_match, keyword=keywords[9], weight=99)

    result = {p.pk: p for p in annotate_keyword_ranking(article, parameters)}

    assert result[param_two_matches.pk].matching_keywords_count == 2
    assert result[param_two_matches.pk].max_matching_keyword_weight == 1
    assert result[param_one_match.pk].matching_keywords_count == 1
    assert result[param_one_match.pk].max_matching_keyword_weight == 5
    assert result[param_no_match.pk].matching_keywords_count == 0
    assert result[param_no_match.pk].max_matching_keyword_weight is None


@pytest.mark.django_db
def test_default_assign_editors_prefers_keyword_match_over_workload(
    review_settings,
    admin,
    article,
    editors,
    coauthors_setting,
):
    """
    An editor with a matching keyword is picked over an editor with more available workload
    but no matching keyword.

    Goes through the real HTTP submission pipeline (like the other tests in this file), rather than
    calling `default_assign_editors_to_articles` directly, so the automatic assignment runs with the
    request context that `get_current_request()` needs.
    """
    keyword = submission_models.Keyword.objects.create(word="test-keyword")
    article.journal.keywords.add(keyword)
    article.keywords.add(keyword)
    article.primary_issue = None
    article.save()

    high_workload_no_match, low_workload_with_match, _ = StaffWorkloadParameters.objects.filter(
        user__in=editors
    ).order_by("id")
    high_workload_no_match.workload = 10
    high_workload_no_match.save()
    low_workload_with_match.workload = 1
    low_workload_with_match.save()
    StaffKeyword.objects.create(parameters=low_workload_with_match, keyword=keyword, weight=1)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    editor_assignment = WjsEditorAssignment.objects.get(article=article)
    assert editor_assignment.editor == low_workload_with_match.user

    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == low_workload_with_match.user, "audit must name the chosen editor"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT, "default algorithm"
    assert audit.matched_by_keyword is True, "won via keyword ranking"
    assert audit.matching_keywords_count == 1, "one shared keyword"
    assert audit.max_matching_keyword_weight == 1, "StaffKeyword weight was 1"
    assert audit.workload_cap == 1, "monthly cap is min(workload=1, brake_on unset)"
    assert audit.workload_available == 1, "no assignments yet this month"
    assert audit.workload_available_30d == 1, "trailing-30-day availability, the ranking tie-break"
    assert audit.keyword_score == pytest.approx(0.01), "single keyword, editor weight 1 of 100"


@pytest.mark.django_db
def test_default_assign_editors_prefers_broad_weighted_coverage(
    review_settings,
    admin,
    article,
    editors,
    keywords,
    coauthors_setting,
):
    """Through the submission pipeline, the editor covering more weighted keywords is assigned."""
    article.primary_issue = None
    article.save()
    article.keywords.clear()
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 75, keywords[2]: 50})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=article.journal)
    parameters.update(workload=10, brake_on=0)
    param_narrow, param_broad, _ = parameters.order_by("id")
    StaffKeyword.objects.create(parameters=param_narrow, keyword=keywords[0], weight=100)
    for keyword in (keywords[0], keywords[1], keywords[2]):
        StaffKeyword.objects.create(parameters=param_broad, keyword=keyword, weight=90)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        response = client.post(reverse("submit_review", args=(article.pk,)), data={"next_step": "next_step"})
        assert response.status_code == 302, "submission accepted"

    assert WjsEditorAssignment.objects.get(article=article).editor == param_broad.user, "0.9 beats 0.444"


@pytest.mark.django_db
def test_default_assign_editors_picks_best_workload_when_no_keyword_match(
    review_settings,
    admin,
    article,
    editors,
    coauthors_setting,
):
    """
    With no keyword overlap for any candidate, all candidates tie on keyword ranking (0 matches,
    no weight) and selection falls through to the workload tie-break within the same ranked
    queryset -- there is no separate "fallback" code path or metric anymore.

    Goes through the real HTTP submission pipeline (like the other tests in this file), rather
    than calling default_assign_editors_to_articles directly, so the automatic assignment runs
    with the request context that get_current_request() needs.
    """
    article.primary_issue = None
    article.save()
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    expected_parameter = (
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters)))
        .filter(available_workload_month__gt=0)
        .first()
    )
    expected_editor = expected_parameter.user

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    editor_assignment = WjsEditorAssignment.objects.get(article=article)
    assert editor_assignment.editor == expected_editor

    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == expected_editor, "audit must name the chosen editor"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT, "same default path, no fallback branch"
    assert audit.matched_by_keyword is False, "no candidate shares a keyword"
    assert audit.matching_keywords_count is None, "keyword fields stay empty without a match"
    assert audit.max_matching_keyword_weight is None, "keyword fields stay empty without a match"
    assert audit.workload_available == expected_parameter.available_workload_month, "monthly availability"
    assert audit.workload_cap == expected_parameter.effective_workload_cap, "effective monthly cap"
    assert audit.workload_available_30d == expected_parameter.available_workload_30d, "30-day tie-break metric"


@pytest.mark.django_db
def test_default_assignment_snapshots_the_candidates(review_settings, admin, article, editors, coauthors_setting):
    """The audit row keeps the candidates the algorithm saw: the winner selected, the others with their reason."""
    keyword = submission_models.Keyword.objects.create(word="test-keyword")
    article.journal.keywords.add(keyword)
    article.keywords.add(keyword)
    article.primary_issue = None
    article.save()
    winner, runner_up, _ = StaffWorkloadParameters.objects.filter(user__in=editors).order_by("id")
    StaffKeyword.objects.create(parameters=winner, keyword=keyword, weight=100)
    StaffKeyword.objects.create(parameters=runner_up, keyword=keyword, weight=10)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        response = client.post(reverse("submit_review", args=(article.pk,)), data={"next_step": "next_step"})
        assert response.status_code == 302

    audit = AutomaticAssignmentAudit.objects.get(article=article)
    candidates = list(audit.candidates.all())
    assert [c.editor for c in candidates] == [winner.user, runner_up.user], "winner first, then the runner-up"
    assert [c.selected for c in candidates] == [True, False], "only the picked editor is selected"
    assert [c.rank for c in candidates] == [1, 2], "ranks follow the algorithm's ordering"
    assert candidates[0].reason == "selected", "the winner is explained as selected"
    assert candidates[1].reason == "lower keyword score", "the runner-up is explained against the winner"
    assert candidates[0].shared_keywords == [
        {"word": "test-keyword", "article_weight": None, "editor_weight": 100}
    ], "the shared keywords are stored with both weights (an unweighted article keyword stays NULL)"
    assert candidates[0].workload_cap is not None, "the workload numbers are stored"


@pytest.mark.django_db
def test_default_assignment_without_shared_keywords_snapshots_no_candidates(
    review_settings, admin, article, editors, coauthors_setting
):
    """Only editors sharing a keyword are explained: a keyword-less assignment has an empty snapshot."""
    article.primary_issue = None
    article.save()
    article.keywords.clear()

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        response = client.post(reverse("submit_review", args=(article.pk,)), data={"next_step": "next_step"})
        assert response.status_code == 302

    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.candidates.count() == 0, "no editor shares a keyword with the article"


@pytest.mark.django_db
def test_default_assign_editors_article_without_keywords_is_assigned_by_workload(
    review_settings, admin, article, editors, keywords, coauthors_setting
):
    """An article with no keywords is still assigned, and its audit row records no keyword match."""
    article.primary_issue = None
    article.save()
    article.keywords.clear()
    for parameters in StaffWorkloadParameters.objects.filter(user__in=editors):
        StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    assert WjsEditorAssignment.objects.filter(article=article).exists(), "article must still get an editor"
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.matched_by_keyword is False, "an article without keywords cannot match"
    assert audit.matching_keywords_count is None, "keyword fields stay empty without a match"
    assert audit.max_matching_keyword_weight is None, "keyword fields stay empty without a match"


@pytest.mark.django_db
def test_default_assign_editors_no_assignment_when_all_editors_over_monthly_cap(
    review_settings,
    admin,
    article,
    editors,
    coauthors_setting,
):
    """
    No assignment is made -- and the article stays EDITOR_TO_BE_SELECTED -- when every available
    section editor is already at their monthly workload cap.

    default_assign_editors_to_articles's own .filter(available_workload_month__gt=0) step can still
    empty the candidate pool even though `parameters` (before that filter) is non-empty -- removing
    the old keyword-exclusion fallback did not remove this path, it just means there is no longer a
    separate "no keyword match" reason for an empty pool. This is a regression guard for that.

    Goes through the real HTTP submission pipeline (like the other tests in this file), rather than
    calling default_assign_editors_to_articles directly, so the automatic assignment runs with the
    request context that get_current_request() needs.
    """
    article.primary_issue = None
    article.save()

    for editor in editors:
        parameters = StaffWorkloadParameters.objects.get(user=editor, journal=article.journal)
        parameters.workload = 1
        parameters.brake_on = 0
        parameters.save()

        prior_article = Article.objects.create(
            journal=article.journal,
            title=f"Prior article for {editor.username}",
            owner=editor,
            correspondence_author=editor,
        )
        WjsEditorAssignment.objects.create(
            article=prior_article,
            editor=editor,
            editor_type="section-editor",
        )

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    article.refresh_from_db()
    assert not WjsEditorAssignment.objects.filter(article=article).exists()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    assert not AutomaticAssignmentAudit.objects.filter(article=article).exists(), "no audit without assignment"


@pytest.mark.django_db
def test_default_assign_editors_excludes_eo_disabled_editor(
    review_settings,
    admin,
    article,
    editors,
    coauthors_setting,
):
    """
    An editor with brake_on == -1 (EO-disabled, wjs/specs#3184) is never picked by automatic
    assignment, even with an otherwise-empty workload and no keyword competition -- it must be
    hard-excluded by annotate_workload_availability's effective_workload_cap, the same way an
    over-cap editor is (see test_default_assign_editors_no_assignment_when_all_editors_over_monthly_cap).
    """
    article.primary_issue = None
    article.save()

    disabled_editor, other_disabled_editor, available_editor = editors
    for editor in (disabled_editor, other_disabled_editor):
        parameters = StaffWorkloadParameters.objects.get(user=editor, journal=article.journal)
        parameters.workload = 10
        parameters.brake_on = -1
        parameters.save()

    available_parameters = StaffWorkloadParameters.objects.get(user=available_editor, journal=article.journal)
    available_parameters.workload = 10
    available_parameters.brake_on = 0
    available_parameters.save()

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    editor_assignment = WjsEditorAssignment.objects.get(article=article)
    assert editor_assignment.editor == available_editor

    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == available_editor, "a brake_on=-1 editor must never be the audited choice"


@pytest.mark.django_db
def test_jcom_assign_editors_ignores_keywords(
    review_settings, admin, article, main_director, director_editor, coauthors_setting
):
    """
    JCOM/JCOMAL director assignment must keep ignoring keywords: a director with a matching
    keyword but less available workload must NOT be preferred over the higher-workload one.

    Uses the `main_director`/`director_editor` fixtures (both grant DIRECTOR_MAIN_ROLE) rather than
    `directors` (which only grants the plain DIRECTOR_ROLE): `jcom_assign_editors_to_articles`
    filters on DIRECTOR_MAIN_ROLE, so `directors` would yield no candidates at all.

    Goes through the real HTTP submission pipeline (like the other tests in this file), rather than
    calling `jcom_assign_editors_to_articles` directly, so the automatic assignment runs with the
    request context that `get_current_request()` needs. The `journal` fixture's code is already
    "JCOM" by default, so no journal-code override is needed here.
    """
    keyword = submission_models.Keyword.objects.create(word="test-keyword")
    article.journal.keywords.add(keyword)
    article.keywords.add(keyword)
    article.primary_issue = None
    article.save()

    high_workload_no_match = StaffWorkloadParameters.objects.get(
        user=main_director.janeway_account,
        journal=article.journal,
    )
    low_workload_with_match = StaffWorkloadParameters.objects.get(
        user=director_editor.janeway_account,
        journal=article.journal,
    )
    high_workload_no_match.workload = 10
    high_workload_no_match.save()
    low_workload_with_match.workload = 1
    low_workload_with_match.save()
    StaffKeyword.objects.create(parameters=low_workload_with_match, keyword=keyword, weight=99)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=JCOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin.janeway_account)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    editor_assignment = WjsEditorAssignment.objects.get(article=article)
    assert editor_assignment.editor == main_director.janeway_account

    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == main_director.janeway_account, "audit must name the chosen director"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.JCOM, "JCOM algorithm"
    assert audit.matched_by_keyword is False, "JCOM never matches keywords"
    assert audit.workload_cap == 10, "director workload was set to 10"
    assert audit.workload_available == 10, "director had no open assignments"
    assert audit.keyword_score is None, "JCOM doesn't use keywords"


@pytest.mark.django_db
def test_default_special_issue_assignment_ignores_keywords(
    review_settings,
    admin,
    article,
    directors,
    editors,
    coauthors_setting,
    special_issue,
):
    """
    Special-issue assignment must keep ignoring keywords: a managing editor with a matching
    keyword but less available workload must NOT be preferred over the higher-workload one.

    Uses the `special_issue` fixture (whose managing editors are the `editors` fixture, giving 3
    candidates) to prove the special-issue branch of `default_assign_editors_to_articles` never
    invokes keyword matching at all, not merely that it happens to pick the same editor by
    coincidence (see `test_jcom_assign_editors_ignores_keywords` for the analogous JCOM/director
    case).
    """
    keyword = submission_models.Keyword.objects.create(word="test-keyword")
    article.journal.keywords.add(keyword)
    article.keywords.add(keyword)

    low_workload_with_match, high_workload_no_match, third_editor = list(
        special_issue.managing_editors.all().order_by("id"),
    )

    low_workload_param = StaffWorkloadParameters.objects.get(user=low_workload_with_match, journal=article.journal)
    high_workload_param = StaffWorkloadParameters.objects.get(user=high_workload_no_match, journal=article.journal)
    third_param = StaffWorkloadParameters.objects.get(user=third_editor, journal=article.journal)
    low_workload_param.workload = 1
    low_workload_param.save()
    high_workload_param.workload = 10
    high_workload_param.save()
    # Keep the third candidate's workload well below the high-workload editor's, so it cannot
    # accidentally tie with (and win over, by id) the expected editor.
    third_param.workload = 2
    third_param.save()
    StaffKeyword.objects.create(parameters=low_workload_param, keyword=keyword, weight=99)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    editor_assignment = WjsEditorAssignment.objects.get_current(article=article)
    assert editor_assignment.editor == high_workload_no_match

    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == high_workload_no_match, "audit must name the chosen managing editor"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT_SPECIAL_ISSUE, "special-issue branch"
    assert audit.matched_by_keyword is False, "special issues bypass keyword matching"
    assert audit.workload_cap == 10, "managing editor workload was set to 10"
    assert audit.workload_available == 10, "managing editor had no open assignments"
    assert audit.workload_available_30d is None, "special issues don't use the 30-day metric"
    assert audit.keyword_score is None, "special issues don't use keywords"


@pytest.mark.django_db
def test_no_audit_when_no_editor_assigned(review_settings, admin, article, editors, coauthors_setting):
    """No audit row is written when no editor could be assigned at all."""
    article.primary_issue = None
    article.save()
    StaffWorkloadParameters.objects.filter(user__in=editors).update(enabled=False)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    assert not WjsEditorAssignment.objects.filter(article=article).exists(), "precondition: nobody assigned"
    assert not AutomaticAssignmentAudit.objects.filter(article=article).exists(), "no audit without assignment"


@pytest.mark.django_db
def test_annotate_workload_availability_uses_smaller_of_workload_and_brake_on(article, editors, journal):
    """When brake_on is set (> 0), the effective cap is min(workload, brake_on), not workload alone."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 10
    parameters.brake_on = 3
    parameters.save()

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.effective_workload_cap == 3
    assert result.available_workload_month == 3
    assert result.available_workload_30d == 3


@pytest.mark.django_db
def test_annotate_workload_availability_brake_on_minus_one_forces_zero_cap(article, editors, journal):
    """A brake_on of -1 is the EO-disabled sentinel: the effective cap is forced to 0 regardless of workload."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 10
    parameters.brake_on = -1
    parameters.save()

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.effective_workload_cap == 0
    assert result.available_workload_month <= 0
    assert result.available_workload_30d <= 0


@pytest.mark.django_db
def test_annotate_workload_availability_ignores_zero_brake_on(article, editors, journal):
    """A brake_on of 0 (the model default) means "no EO override": fall back to plain workload."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 10
    parameters.brake_on = 0
    parameters.save()

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.effective_workload_cap == 10
    assert result.available_workload_month == 10
    assert result.available_workload_30d == 10


@pytest.mark.django_db
def test_annotate_workload_availability_counts_only_current_month(article, editors, journal, fake_request):
    """Only WjsEditorAssignment rows assigned in the current calendar month count against the monthly cap."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    last_month_article = Article.objects.create(
        journal=journal,
        title="Old article",
        owner=editors[0],
        correspondence_author=editors[0],
    )
    old_assignment = WjsEditorAssignment.objects.create(
        article=last_month_article,
        editor=editors[0],
        editor_type="section-editor",
    )
    WjsEditorAssignment.objects.filter(pk=old_assignment.pk).update(
        assigned=timezone.now() - timedelta(days=45),
    )

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1


@pytest.mark.django_db
def test_annotate_workload_availability_scoped_to_journal(
    article,
    editors,
    journal,
    journal_factory,
):
    """
    The monthly assignment count must be scoped to each StaffWorkloadParameters' own journal.

    An editor who also works for another journal (e.g. JCOM and JCOMAL) must not have an assignment
    made in that other journal count against their capacity here: `StaffWorkloadParameters` is unique
    per (user, journal), so the subquery must correlate on the outer row's own journal, not just the
    editor.
    """
    other_journal = journal_factory("JOTHER")
    editor = editors[0]

    parameters = StaffWorkloadParameters.objects.get(user=editor, journal=journal)
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    StaffWorkloadParameters.objects.update_or_create(user=editor, journal=other_journal, defaults={"workload": 1})

    other_journal_article = Article.objects.create(
        journal=other_journal,
        title="Other journal article",
        owner=editor,
        correspondence_author=editor,
    )
    WjsEditorAssignment.objects.create(
        article=other_journal_article,
        editor=editor,
        editor_type="section-editor",
    )

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1


@pytest.mark.django_db
def test_annotate_workload_availability_does_not_exclude_exhausted_editors(article, editors, journal):
    """
    An editor with no availability left this month is annotated, not filtered out.

    Filtering on available_workload_month is now the caller's job (default_assign_editors_to_articles),
    not this function's — the manual selection view deliberately does NOT filter on it (EO can
    override the cap), so the annotation function itself must stay non-filtering.
    """
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    WjsEditorAssignment.objects.create(article=article, editor=editors[0], editor_type="section-editor")

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.available_workload_month == 0


@pytest.mark.django_db
def test_annotate_workload_availability_excludes_withdrawn_papers(article, editors, journal):
    """A withdrawn paper does not count against either workload window."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    withdrawn_article = Article.objects.create(
        journal=journal,
        title="Withdrawn article",
        owner=editors[0],
        correspondence_author=editors[0],
    )
    withdrawn_article.articleworkflow.state = ArticleWorkflow.ReviewStates.WITHDRAWN
    withdrawn_article.articleworkflow.save()
    WjsEditorAssignment.objects.create(
        article=withdrawn_article,
        editor=editors[0],
        editor_type="section-editor",
    )

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1
    assert result.assignment_count_30d == 0
    assert result.available_workload_30d == 1


@pytest.mark.django_db
def test_annotate_workload_availability_30d_and_month_windows_diverge(article, editors, journal):
    """
    An assignment from 25 days ago, but before the start of the current calendar month, counts
    against the 30-day window but not the calendar-month window — proving the two windows are
    genuinely independent, not the same count read twice.

    Frozen at a fixed date (2026-01-20) so the scenario doesn't depend on which day of the month
    the test suite happens to run on.
    """
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    with freezegun.freeze_time("2026-01-20T12:00:00+00:00"):
        other_article = Article.objects.create(
            journal=journal,
            title="Other article",
            owner=editors[0],
            correspondence_author=editors[0],
        )
        old_assignment = WjsEditorAssignment.objects.create(
            article=other_article,
            editor=editors[0],
            editor_type="section-editor",
        )
        WjsEditorAssignment.objects.filter(pk=old_assignment.pk).update(
            assigned=timezone.now() - timedelta(days=25),
        )

        result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1
    assert result.assignment_count_30d == 1
    assert result.available_workload_30d == 0


@pytest.mark.django_db
def test_annotate_workload_availability_ignores_future_assignments(article, editors, journal):
    """
    Assignments dated after "now" count in neither window.

    Never happens live, but the historical replay freezes "now" at each article's submission date while
    later assignments (from articles outside the replayed batch) already exist in the database.
    """
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    parameters.workload = 5
    parameters.brake_on = 0
    parameters.save()
    future_assignment = WjsEditorAssignment.objects.create(
        article=article, editor=editors[0], editor_type="section-editor"
    )
    # Freeze on the 10th and date the assignment on the 20th: inside both windows' lower bounds, but after "now".
    frozen_now = timezone.now().replace(day=10, hour=12, minute=0, second=0, microsecond=0)
    WjsEditorAssignment.objects.filter(pk=future_assignment.pk).update(assigned=frozen_now.replace(day=20))

    with freezegun.freeze_time(frozen_now):
        annotated = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert annotated.assignment_count_month == 0, "monthly window must ignore assignments after 'now'"
    assert annotated.assignment_count_30d == 0, "30-day window must ignore assignments after 'now'"


@pytest.mark.django_db
def test_rank_editor_parameters_orders_by_keyword_score_then_workload(article, editors, keywords, journal):
    """
    Final order: highest keyword_score, then highest 30-day availability, then id.

    Replaces the previous "highest single weight, then count" rule (spec 2026-09-29): broad weighted
    coverage (three keywords at 80) now beats a single strong keyword (one at 100).
    """
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100, keywords[2]: 100})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal)
    parameters.update(workload=10, brake_on=0)
    param_broad, param_narrow, param_no_match = parameters.order_by("id")
    for keyword in (keywords[0], keywords[1], keywords[2]):
        StaffKeyword.objects.create(parameters=param_broad, keyword=keyword, weight=80)
    StaffKeyword.objects.create(parameters=param_narrow, keyword=keywords[0], weight=100)

    ranked = list(
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters)))
    )

    assert ranked == [param_broad, param_narrow, param_no_match], "0.8 beats 0.333; no match last"


@pytest.mark.django_db
def test_rank_editor_parameters_breaks_score_ties_by_30d_availability(article, editors, keywords, journal):
    """With equal keyword_score, the editor with more 30-day availability ranks first."""
    _set_article_keyword_weights(article, {keywords[0]: 100})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal)
    parameters.update(workload=10, brake_on=0)
    param_busy, param_free, _ = parameters.order_by("id")
    for parameter in (param_busy, param_free):
        StaffKeyword.objects.create(parameters=parameter, keyword=keywords[0], weight=100)
    busy_article = Article.objects.create(journal=journal, title="Busy", owner=param_busy.user)
    WjsEditorAssignment.objects.create(article=busy_article, editor=param_busy.user, editor_type="section-editor")

    ranked = list(
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters)))
    )

    assert ranked.index(param_free) < ranked.index(param_busy), "tie on score broken by 30-day availability"


@pytest.mark.django_db
def test_rank_editor_parameters_breaks_score_ties_by_shared_keyword_count(article, editors, keywords, journal):
    """With an equal keyword_score (all weights 0), more shared keywords beats more 30-day availability."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal)
    parameters.update(workload=10, brake_on=0)
    param_two, param_one, _ = parameters.order_by("id")
    StaffKeyword.objects.create(parameters=param_two, keyword=keywords[0], weight=0)
    StaffKeyword.objects.create(parameters=param_two, keyword=keywords[1], weight=0)
    StaffKeyword.objects.create(parameters=param_one, keyword=keywords[0], weight=0)
    # The one-keyword editor is the less busy one, so workload alone would rank it first.
    busy_article = Article.objects.create(journal=journal, title="Busy", owner=param_two.user)
    WjsEditorAssignment.objects.create(article=busy_article, editor=param_two.user, editor_type="section-editor")

    ranked = list(
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters)))
    )

    scores = {p.pk: p.keyword_score for p in ranked}
    assert scores[param_two.pk] == scores[param_one.pk], "precondition: the two editors tie on keyword_score"
    assert ranked.index(param_two) < ranked.index(
        param_one
    ), "shared keyword count breaks the score tie before workload"


@pytest.mark.django_db
def test_rank_editor_parameters_puts_no_keyword_match_last_not_first(article, editors, keywords):
    """
    Regression guard: a NULL keyword_score (no keyword match at all) must sort
    LAST, not first. Postgres sorts NULLs FIRST under a plain "-keyword_score"
    DESC order_by — rank_editor_parameters must use nulls_last=True explicitly, or a
    non-matching editor would rank ahead of any editor with a real match.
    """
    article.keywords.add(keywords[0])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    for parameter in parameters:
        parameter.workload = 10
        parameter.save()
    param_no_match, param_low_weight_match, _ = parameters.order_by("id")
    StaffKeyword.objects.create(parameters=param_low_weight_match, keyword=keywords[0], weight=1)

    ranked = list(
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters))),
    )

    assert ranked[0] == param_low_weight_match
    assert ranked[0].max_matching_keyword_weight == 1
    assert ranked[0].keyword_score is not None, "the matched editor has a score"
    assert param_no_match in ranked[1:]


def _set_article_keyword_weights(article, weights_by_keyword):
    """Attach keywords to the article with the given KeywordArticle weights (None allowed)."""
    for keyword, weight in weights_by_keyword.items():
        article.keywords.add(keyword)
        KeywordArticle.objects.filter(article=article, keyword=keyword).update(weight=weight)


def _score_for(article, editor):
    """Return the keyword_score of one editor for the article."""
    return (
        Account.objects.get_editors_with_keywords(article, include_matching_keywords=False)
        .get(pk=editor.pk)
        .keyword_score
    )


@pytest.mark.django_db
def test_keyword_score_matches_worked_example(article, editors, keywords, journal):
    """Spec example: article weights 100/75/50/25, editor shares k0 (80) and k2 (100) -> 13000 / 25000 = 0.52."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 75, keywords[2]: 50, keywords[3]: 25})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=80)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[2], weight=100)

    assert _score_for(article, editors[0]) == pytest.approx(0.52), "weighted coverage of the article's keywords"


@pytest.mark.django_db
def test_keyword_score_caps_editor_weight_at_100(article, editors, keywords, journal):
    """An editor weight of 999 counts as 100, so full coverage scores exactly 1."""
    _set_article_keyword_weights(article, {keywords[0]: 100})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=999)

    assert _score_for(article, editors[0]) == pytest.approx(1.0), "editor weights above 100 are capped"


@pytest.mark.django_db
def test_keyword_score_null_article_weight_counts_as_100(article, editors, keywords, journal):
    """A NULL KeywordArticle.weight counts as 100: (100*100) / ((100 + 50) * 100) = 2/3."""
    _set_article_keyword_weights(article, {keywords[0]: None, keywords[1]: 50})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)

    assert _score_for(article, editors[0]) == pytest.approx(2 / 3), "NULL article weight counts as 100"


@pytest.mark.django_db
def test_keyword_score_is_none_without_shared_keywords(article, editors, keywords, journal):
    """No shared keyword, or an article without keywords, gives None rather than 0."""
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[5], weight=100)
    article.keywords.clear()

    assert _score_for(article, editors[0]) is None, "article without keywords has no score"

    _set_article_keyword_weights(article, {keywords[0]: 100})

    assert _score_for(article, editors[0]) is None, "editor without a shared keyword has no score"


@pytest.mark.django_db
def test_keyword_score_is_none_when_all_article_weights_are_zero(article, editors, keywords, journal):
    """All-zero article weights give no score instead of a division-by-zero error."""
    _set_article_keyword_weights(article, {keywords[0]: 0})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)

    assert _score_for(article, editors[0]) is None, "zero denominator must not crash"


@pytest.mark.django_db
def test_keyword_score_ignores_other_journal_keywords(article, editors, keywords, journal, journal_factory):
    """Keywords the editor holds only in another journal's parameters don't count."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)
    other_parameters = StaffWorkloadParameters.objects.update_or_create(
        user=editors[0], journal=journal_factory("JOTHER")
    )[0]
    StaffKeyword.objects.create(parameters=other_parameters, keyword=keywords[1], weight=100)

    assert _score_for(article, editors[0]) == pytest.approx(0.5), "only this journal's editor keywords count"


@pytest.mark.django_db
def test_keyword_score_not_inflated_by_past_assignments(article, editors, keywords, journal):
    """Being a past editor of the article must not multiply the score (join-multiplication guard)."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)
    before = _score_for(article, editors[0])
    now = timezone.now()
    for days in (10, 5):
        PastEditorAssignment.objects.create(
            article=article,
            editor=editors[0],
            date_assigned=now - timedelta(days=days + 1),
            date_unassigned=now - timedelta(days=days),
        )

    assert before == pytest.approx(0.5), "precondition: half coverage"
    assert _score_for(article, editors[0]) == pytest.approx(before), "past assignments must not change the score"


@pytest.mark.django_db
def test_annotate_keyword_ranking_carries_keyword_score(article, editors, keywords, journal):
    """annotate_keyword_ranking exposes keyword_score on the StaffWorkloadParameters rows."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal)
    matched, unmatched, _ = parameters.order_by("id")
    StaffKeyword.objects.create(parameters=matched, keyword=keywords[0], weight=100)

    result = {p.pk: p for p in annotate_keyword_ranking(article, parameters)}

    assert result[matched.pk].keyword_score == pytest.approx(0.5), "score carried onto parameters"
    assert result[unmatched.pk].keyword_score is None, "no match keeps None"
