"""Backend filter "View only with Action Required" (wjs/specs#3482)."""

import re
from datetime import timedelta

import pytest
from core.models import Account
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.db import connection
from django.http import HttpRequest, QueryDict
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import now
from journal.models import Journal
from plugins.wjs_review import ac_service
from plugins.wjs_review.filters import (
    AuthorArticleWorkflowFilter,
    EOArticleWorkflowFilter,
    ReviewerArticleWorkflowFilter,
    StaffArticleWorkflowFilter,
    WorkOnAPaperArticleWorkflowFilter,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    EditorDecision,
    EditorRevisionRequest,
    WjsEditorAssignment,
)
from plugins.wjs_review.views import EOArchived, EOPending, EOProduction, EOWorkOnAPaper
from review.models import ReviewRound

from wjs.jcom_profile.utils import get_eo_user


def _matches(user: Account, journal: Journal) -> tuple[set[int], set[int]]:
    """Return the workflows kept by `requiring_attention` and the ones flagged by `with_has_ac`, as pk sets."""
    workflows = ArticleWorkflow.objects.all()
    kept = set(workflows.requiring_attention(user, journal).values_list("pk", flat=True))
    flagged = {workflow.pk for workflow in workflows.with_has_ac(user, journal) if workflow.has_ac}
    return kept, flagged


@pytest.mark.django_db
def test_active_ac_matches_and_has_ac_is_true(journal, two_workflows, ac_user):
    with_ac, without_ac = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.REVIEWER_LATE, "Reviewer is late")

    kept, flagged = _matches(ac_user, journal)

    assert kept == {with_ac.pk}, "requiring_attention should keep only the workflow with an active AC"
    assert flagged == {with_ac.pk}, "has_ac should be True only for the workflow with an active AC"
    assert without_ac.pk not in kept | flagged, "A workflow without ACs must not match"


@pytest.mark.django_db
def test_dismissed_only_does_not_match(journal, two_workflows, ac_user):
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.REVIEWER_LATE, "Reviewer is late")
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.NEEDS_ASSIGNMENT, "Needs assignment")
    AttentionCondition.objects.filter(article=with_ac.article, user=ac_user).update(
        status=AttentionCondition.Status.DISMISSED
    )

    kept, flagged = _matches(ac_user, journal)

    assert kept == set(), "A paper whose ACs are all dismissed must not match"
    assert flagged == set(), "has_ac must be False when every AC is dismissed"


@pytest.mark.django_db
def test_unread_messages_only_matches(journal, two_workflows, ac_user):
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.HAS_UNREAD_MESSAGE, "You have unread messages")

    kept, flagged = _matches(ac_user, journal)

    assert kept == {with_ac.pk}, "Unread messages alone are an action required, as for the row highlight"
    assert flagged == {with_ac.pk}, "has_ac must be True when the only AC is unread messages"


@pytest.mark.django_db
def test_ac_of_another_user_does_not_match(journal, two_workflows, ac_user, create_jcom_user):
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.REVIEWER_LATE, "Reviewer is late")
    other = create_jcom_user("other_editor").janeway_account

    kept, flagged = _matches(other, journal)

    assert kept == set(), "The ACs of another user must not make the paper match"
    assert flagged == set(), "has_ac must look at the current user's ACs only"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    (
        AttentionCondition.Status.RESOLVED,
        AttentionCondition.Status.EXPIRED,
        AttentionCondition.Status.SUPPRESSED,
    ),
)
def test_resolved_expired_suppressed_do_not_match(journal, two_workflows, ac_user, status):
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.REVIEWER_LATE, "Reviewer is late")
    AttentionCondition.objects.filter(article=with_ac.article, user=ac_user).update(status=status)

    kept, flagged = _matches(ac_user, journal)

    assert kept == set(), f"A {status} AC must not make the paper match"
    assert flagged == set(), f"has_ac must be False for a {status} AC"


@pytest.mark.django_db
def test_eo_matches_ac_of_eo_system_user_without_own_row(journal, two_workflows, eo_user, eo_human):
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, get_eo_user(journal), ac_service.REVIEWER_LATE, "Reviewer is late")

    kept, flagged = _matches(eo_human, journal)

    assert kept == {with_ac.pk}, "An EO person with no row of their own sees the EO system user's AC"
    assert flagged == {with_ac.pk}, "has_ac must follow the same fallback as visible_to"


@pytest.mark.django_db
def test_eo_own_dismissed_row_hides_eo_system_user_row(journal, two_workflows, eo_user, eo_human):
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, get_eo_user(journal), ac_service.REVIEWER_LATE, "Reviewer is late")
    ac_service.upsert_ac(with_ac.article, eo_human, ac_service.REVIEWER_LATE, "Reviewer is late")
    AttentionCondition.objects.filter(article=with_ac.article, user=eo_human).update(
        status=AttentionCondition.Status.DISMISSED
    )

    kept, flagged = _matches(eo_human, journal)

    assert kept == set(), "A dismissal is personal: the EO system user's copy must not bring the paper back"
    assert flagged == set(), "has_ac must not resurrect a dismissed AC through the EO system user's row"


def _filtered_pks(data: dict, user: Account, journal: Journal, request: HttpRequest) -> tuple[bool, set[int]]:
    """Return whether the filterset is valid and the workflows it keeps, as the list views build it."""
    request.user = user
    filterset = StaffArticleWorkflowFilter(
        data=data, queryset=ArticleWorkflow.objects.all(), journal=journal, request=request
    )
    return filterset.is_valid(), set(filterset.qs.values_list("pk", flat=True))


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_filter_one_keeps_only_matching_papers(journal, two_workflows, ac_user, fake_request):
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.REVIEWER_LATE, "Reviewer is late")

    valid, pks = _filtered_pks({"action_required": "1"}, ac_user, journal, fake_request)

    assert valid, "action_required=1 is a valid choice"
    assert pks == {with_ac.pk}, "action_required=1 should keep only the papers with an active AC for the user"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
@pytest.mark.parametrize("data", ({"action_required": "0"}, {}))
def test_filter_zero_and_absent_keep_all(journal, two_workflows, ac_user, fake_request, data):
    with_ac, without_ac = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.REVIEWER_LATE, "Reviewer is late")

    valid, pks = _filtered_pks(data, ac_user, journal, fake_request)

    assert valid, f"{data} is a valid choice"
    assert {with_ac.pk, without_ac.pk} <= pks, f"{data} must not filter any paper out"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
@pytest.mark.parametrize("value", ("2", "abc", "true"))
def test_unknown_value_is_invalid_and_keeps_all(journal, two_workflows, ac_user, fake_request, value):
    with_ac, without_ac = two_workflows
    ac_service.upsert_ac(with_ac.article, ac_user, ac_service.REVIEWER_LATE, "Reviewer is late")

    valid, pks = _filtered_pks({"action_required": value}, ac_user, journal, fake_request)

    assert not valid, f"action_required={value} is not a valid choice: the views then apply no filter at all"
    assert {with_ac.pk, without_ac.pk} <= pks, f"action_required={value} must not hide papers"


@pytest.mark.parametrize(
    "filter_class",
    (
        AuthorArticleWorkflowFilter,
        ReviewerArticleWorkflowFilter,
        StaffArticleWorkflowFilter,
        EOArticleWorkflowFilter,
        WorkOnAPaperArticleWorkflowFilter,
    ),
)
def test_every_filterset_has_the_filter(filter_class):
    assert "action_required" in filter_class.base_filters, f"{filter_class.__name__} must inherit action_required"


def _make_view(view_class: type, user: Account, request: HttpRequest, query: str = ""):
    """Build a list view the way the request cycle does up to the queryset: user, querystring, `load_initial`."""
    request.GET = QueryDict(query)
    request.user = user
    view = view_class()
    view.kwargs = {}
    view.args = ()
    view.request = request
    view.load_initial(request)
    return view


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_list_queryset_rows_have_has_ac(journal, in_review_workflows, eo_human, fake_request):
    with_ac, without_ac = in_review_workflows
    ac_service.upsert_ac(with_ac.article, eo_human, ac_service.REVIEWER_LATE, "Reviewer is late")

    view = _make_view(EOPending, eo_human, fake_request)
    flags = {workflow.pk: workflow.has_ac for workflow in view.get_queryset()}

    assert flags[with_ac.pk] is True, "The row with an active AC must be flagged"
    assert flags[without_ac.pk] is False, "The row without ACs must not be flagged"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_eo_with_workload_gets_both_defaults(journal, in_review_workflows, eo_user, fake_request):
    with_ac, without_ac = in_review_workflows
    eo = eo_user.janeway_account
    for workflow in in_review_workflows:
        workflow.eo_in_charge = eo
        workflow.save()
    ac_service.upsert_ac(with_ac.article, eo, ac_service.REVIEWER_LATE, "Reviewer is late")

    view = _make_view(EOPending, eo, fake_request)

    assert view.request.GET.get("action_required") == "1", "An EO with a workload gets the filter by default"
    assert view.request.GET.get("eo_in_charge") == str(eo.pk), "The EO in charge default is kept, as a pk"
    assert set(view.get_queryset().values_list("pk", flat=True)) == {with_ac.pk}, "Only papers with an AC are listed"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
@pytest.mark.parametrize(
    "query,expected_action_required,expected_eo_in_charge",
    (
        ("search=1", None, None),
        ("action_required=0", "0", None),
        ("action_required=0&eo_in_charge=", "0", ""),
        ("action_required=1", "1", None),
    ),
)
def test_explicit_choice_cancels_defaults(
    journal, in_review_workflows, eo_user, fake_request, query, expected_action_required, expected_eo_in_charge
):
    with_ac, without_ac = in_review_workflows
    eo = eo_user.janeway_account

    view = _make_view(EOPending, eo, fake_request, query)

    assert view.request.GET.get("action_required") == expected_action_required, f"{query}: action_required default"
    assert view.request.GET.get("eo_in_charge") == expected_eo_in_charge, f"{query}: eo_in_charge default"
    if expected_action_required != "1":
        listed = set(view.get_queryset().values_list("pk", flat=True))
        assert {with_ac.pk, without_ac.pk} <= listed, f"{query}: every paper of the list must be shown"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_eo_without_workload_gets_no_default(journal, in_review_workflows, eo_human, fake_request):
    view = _make_view(EOPending, eo_human, fake_request)

    assert "action_required" not in view.request.GET, "An EO without a workload gets no filter by default"
    assert "eo_in_charge" not in view.request.GET, "An EO without a workload gets no EO in charge by default"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
@pytest.mark.parametrize("view_class", (EOArchived, EOProduction, EOWorkOnAPaper))
def test_other_eo_lists_have_no_default(journal, eo_user, fake_request, view_class):
    view = _make_view(view_class, eo_user.janeway_account, fake_request)

    assert "action_required" not in view.request.GET, f"{view_class.__name__} must not default the filter"
    assert "eo_in_charge" not in view.request.GET, f"{view_class.__name__} must not default the EO in charge"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_filtered_page_count_is_the_filtered_list(journal, in_review_workflows, eo_human, fake_request):
    with_ac, _ = in_review_workflows
    ac_service.upsert_ac(with_ac.article, eo_human, ac_service.REVIEWER_LATE, "Reviewer is late")
    ac_service.upsert_ac(with_ac.article, eo_human, ac_service.NEEDS_ASSIGNMENT, "Needs assignment")

    view = _make_view(EOPending, eo_human, fake_request, "action_required=1")
    queryset = view.get_queryset()
    paginator = Paginator(queryset, 20)

    assert paginator.count == 1, "Two ACs on one paper must count as one paper in the filtered list"
    assert [workflow.pk for workflow in paginator.page(1).object_list] == [with_ac.pk], "Page 1 holds that paper"


@pytest.mark.django_db
def test_anonymous_list_redirects_to_login(journal, client: Client):
    response = client.get(reverse("wjs_review_eo_pending"))

    assert response.status_code == 302, f"An anonymous request must be redirected, got {response.status_code}"
    assert "login" in response.url, f"The redirect must go to the login page, got {response.url}"


LIST_PAGES = (
    ("section-editor", "wjs_review_list"),
    ("section-editor", "wjs_review_archived_papers"),
    ("eo", "wjs_review_eo_pending"),
    ("eo", "wjs_review_eo_archived"),
    ("eo", "wjs_review_eo_production"),
    ("eo", "wjs_review_eo_workon"),
    ("director", "wjs_review_director_pending"),
    ("director", "wjs_review_director_archived"),
    ("director", "wjs_review_director_production"),
    ("director", "wjs_review_director_workon"),
    ("author", "wjs_review_author_archived"),
    ("reviewer", "wjs_review_reviewer_archived"),
)
"""The lists (role, URL name) that show the filter form, so the buttons."""

LIST_PAGES_WITHOUT_FILTERS = (("author", "wjs_review_author_pending"), ("reviewer", "wjs_review_reviewer_pending"))
"""The lists with `show_filters = False`: they never had the buttons."""


def _get_list(client: Client, journal: Journal, user: Account, url_name: str, query: str = ""):
    """Log in as the user and get a paper list."""
    client.force_login(user)
    return client.get(f"{reverse(url_name)}{query}")


def _buttons(content: str) -> dict[str, str]:
    """Return the opening tag of each "action_required" button of the page, by value."""
    found = {}
    for tag in re.findall(r"<button[^>]*>", content):
        if 'name="action_required"' in tag:
            found[re.search(r'value="(\d)"', tag).group(1)] = tag
    return found


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
@pytest.mark.parametrize("role,path", LIST_PAGES)
def test_every_filtered_list_has_the_two_buttons(journal, client, user_for_role, role, path):
    response = _get_list(client, journal, user_for_role(role), path)

    assert response.status_code == 200, f"{path} should render for a {role}, got {response.status_code}"
    content = response.content.decode()
    assert set(_buttons(content)) == {"0", "1"}, f"{path} must have the View All and View only buttons, as submit"
    assert "(current page)" not in content, f"{path} must not say the filter works on the current page only"
    assert "visually-hidden" not in content.split("selectAll")[-1][:600], f"{path} must not hide rows in the browser"


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
@pytest.mark.parametrize("role,path", LIST_PAGES_WITHOUT_FILTERS)
def test_lists_without_filters_have_no_buttons(journal, client, user_for_role, role, path):
    response = _get_list(client, journal, user_for_role(role), path)

    assert response.status_code == 200, f"{path} should render for a {role}, got {response.status_code}"
    assert _buttons(response.content.decode()) == {}, f"{path} has no filter form, so no action_required buttons"


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
@pytest.mark.parametrize(
    "query,selected,not_selected",
    (("", "0", "1"), ("?action_required=0", "0", "1"), ("?action_required=1", "1", "0")),
)
def test_selected_button_follows_the_parameter(journal, client, user_for_role, query, selected, not_selected):
    response = _get_list(client, journal, user_for_role("eo"), "wjs_review_eo_pending", query)

    buttons = _buttons(response.content.decode())
    assert "btn-primary" in buttons[selected], f"{query!r}: the {selected} button must be the selected one"
    assert "btn-outline-primary" in buttons[not_selected], f"{query!r}: the {not_selected} button must not be"
    assert 'aria-pressed="true"' in buttons[selected], f"{query!r}: the selected button must say so to screen readers"
    assert 'aria-pressed="false"' in buttons[not_selected], f"{query!r}: the other button must say it is not pressed"


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
def test_filter_is_applied_by_the_server_on_the_whole_list(
    journal, client, user_for_role, three_in_review, monkeypatch
):
    oldest = three_in_review[0]
    eo = user_for_role("eo")
    ac_service.upsert_ac(oldest.article, eo, ac_service.REVIEWER_LATE, "Reviewer is late")
    monkeypatch.setattr(EOPending, "paginate_by", 1)

    unfiltered = _get_list(client, journal, eo, "wjs_review_eo_pending", "?action_required=0")
    filtered = _get_list(client, journal, eo, "wjs_review_eo_pending", "?action_required=1")

    assert oldest.pk not in [w.pk for w in unfiltered.context["workflows"]], "Without the filter, page 1 is the newest"
    assert [w.pk for w in filtered.context["workflows"]] == [oldest.pk], "The filter must find the paper on page 1"
    assert filtered.context["paginator"].count == 1, "The page count must be the one of the filtered list"
    assert filtered.context["querystring"].get("action_required") == "1", "Pagination links keep the choice"


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
def test_rows_without_ac_have_no_icons_and_rows_with_ac_keep_them(journal, client, user_for_role, three_in_review):
    eo = user_for_role("eo")
    ac_service.upsert_ac(three_in_review[0].article, eo, ac_service.REVIEWER_LATE, "Reviewer is late")

    response = _get_list(client, journal, eo, "wjs_review_eo_pending", "?action_required=0")

    content = response.content.decode()
    assert content.count('aria-label="1 attention condition"') == 1, "Only the paper with an AC has the warning icon"
    assert "Reviewer is late" in content, "The popover of the paper with an AC lists its AC"


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
def test_list_page_query_count_does_not_grow_with_acs(journal, client, user_for_role, three_in_review):
    eo = user_for_role("eo")
    _get_list(client, journal, eo, "wjs_review_eo_pending", "?action_required=0")  # warm caches

    def count_queries() -> int:
        with CaptureQueriesContext(connection) as queries:
            _get_list(client, journal, eo, "wjs_review_eo_pending", "?action_required=0")
        return len(queries)

    ac_service.upsert_ac(three_in_review[0].article, eo, ac_service.REVIEWER_LATE, "Reviewer is late")
    one_ac = count_queries()
    for workflow in three_in_review[1:]:
        ac_service.upsert_ac(workflow.article, eo, ac_service.REVIEWER_LATE, "Reviewer is late")
    three_acs = count_queries()

    assert three_acs == one_ac, f"The page ran {one_ac} queries with 1 AC and {three_acs} with 3"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_filter_reuses_the_annotation_instead_of_building_the_rule_again(
    journal, two_workflows, eo_human, fake_request
):
    """Building the rule costs two lookups (EO role, EO system user): the list views annotate first, filter second."""
    with_ac, _ = two_workflows
    ac_service.upsert_ac(with_ac.article, eo_human, ac_service.REVIEWER_LATE, "Reviewer is late")
    fake_request.user = eo_human
    filterset = StaffArticleWorkflowFilter(
        data={"action_required": "1"}, queryset=ArticleWorkflow.objects.all(), journal=journal, request=fake_request
    )
    annotated = ArticleWorkflow.objects.all().with_has_ac(eo_human, journal)
    assert filterset.is_valid(), "The choice is valid"

    with CaptureQueriesContext(connection) as queries:
        filtered = filterset.filter_queryset(annotated)

    assert len(queries) == 0, f"The filter must reuse has_ac, not rebuild the rule ({len(queries)} queries ran)"
    assert set(filtered.values_list("pk", flat=True)) == {with_ac.pk}, "The reused annotation must filter correctly"


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
def test_sort_and_pagination_links_of_the_eo_default_view_keep_the_filters(journal, client, eo_user, three_in_review):
    """The links of the page (sort headers, pagination) are built from the querystring the view ends up with."""
    eo = eo_user.janeway_account
    for workflow in three_in_review:
        workflow.eo_in_charge = eo
        workflow.save()
    ac_service.upsert_ac(three_in_review[0].article, eo, ac_service.REVIEWER_LATE, "Reviewer is late")

    first = _get_list(client, journal, eo, "wjs_review_eo_pending")
    link_querystring = first.context["querystring"].urlencode()
    second = _get_list(client, journal, eo, "wjs_review_eo_pending", f"?{link_querystring}&o=-last_revision_date")

    assert [w.pk for w in first.context["workflows"]] == [three_in_review[0].pk], "The default view is filtered"
    assert second.context["filter"].is_valid(), f"A link of the default view must be valid: {link_querystring}"
    assert [w.pk for w in second.context["workflows"]] == [three_in_review[0].pk], "A link must keep the filters"


@pytest.mark.django_db
@pytest.mark.usefixtures("review_settings")
@pytest.mark.parametrize(
    "query,expected_hidden", (("?action_required=1", "1"), ("?action_required=0", "0"), ("", None))
)
def test_search_keeps_the_choice_of_the_buttons(journal, client, user_for_role, query, expected_hidden):
    """Search and Enter submit the form without a button value: the current choice travels in a hidden input."""
    content = _get_list(client, journal, user_for_role("eo"), "wjs_review_eo_pending", query).content.decode()

    hidden = re.findall(r'<input[^>]*type="hidden"[^>]*name="action_required"[^>]*>', content)
    if expected_hidden is None:
        assert hidden == [], "With no choice there is nothing to keep"
        return
    assert len(hidden) == 1, f"{query}: one hidden input must carry the current choice, found {len(hidden)}"
    assert f'value="{expected_hidden}"' in hidden[0], f"{query}: the hidden input must carry {expected_hidden}"
    assert content.index(hidden[0]) < content.index('name="action_required" value'), "A clicked button must win"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_anonymous_user_gets_an_empty_list_instead_of_an_error(journal, two_workflows, fake_request):
    """The fallback (a queryset without the annotation) must not build the rule for an anonymous user."""
    fake_request.user = AnonymousUser()
    filterset = StaffArticleWorkflowFilter(
        data={"action_required": "1"}, queryset=ArticleWorkflow.objects.all(), journal=journal, request=fake_request
    )

    assert filterset.is_valid(), "The choice is valid"
    assert list(filterset.qs) == [], "An anonymous user has no attention conditions: nothing to show, no error"


@pytest.mark.django_db
@pytest.mark.usefixtures("install_wjs_submission_settings")
def test_filtered_page_count_survives_revisions_and_editor_sorting(
    journal, in_review_workflows, eo_human, ac_user, fake_request
):
    """`Max` over several revision requests, the editor annotation (one assignment) and `distinct()` keep one paper."""
    with_ac, without_ac = in_review_workflows
    for round_number, days in ((1, 20), (2, 10)):
        review_round = ReviewRound.objects.create(round_number=round_number, article=with_ac.article)
        EditorRevisionRequest.objects.create(
            article=with_ac.article,
            editor=ac_user,
            review_round=review_round,
            editor_decision=EditorDecision.objects.create(
                review_round=review_round,
                editor=ac_user,
                workflow=with_ac,
                decision=ArticleWorkflow.Decisions.MINOR_REVISION,
            ),
            date_due=now() - timedelta(days=days),
            date_completed=now() - timedelta(days=days - 1),
        )
    WjsEditorAssignment.objects.create(article=with_ac.article, editor=ac_user, editor_type="section-editor")
    ac_service.upsert_ac(with_ac.article, eo_human, ac_service.REVIEWER_LATE, "Reviewer is late")
    ac_service.upsert_ac(with_ac.article, eo_human, ac_service.NEEDS_ASSIGNMENT, "Needs assignment")

    view = _make_view(EOPending, eo_human, fake_request, "action_required=1&o=editor_sorting")
    paginator = Paginator(view.get_queryset(), 20)

    assert paginator.count == 1, f"Two revisions, an editor and two ACs must still be one paper, got {paginator.count}"
    assert [w.pk for w in paginator.page(1).object_list] == [with_ac.pk], "Page 1 holds the paper with an AC"
    assert without_ac.pk not in [w.pk for w in paginator.page(1).object_list], "The paper without ACs is filtered out"
