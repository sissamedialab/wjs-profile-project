from datetime import timedelta
from typing import Callable

import pytest
from core.models import Account
from django.http import HttpRequest
from django.utils import timezone
from faker import Faker
from plugins.typesetting.models import TypesettingAssignment, TypesettingRound
from review.models import ReviewAssignment, ReviewRound
from submission.models import Article

from wjs.jcom_profile import constants

from ..logic import AssignToReviewer
from ..logic__visibility import (
    GrantPermissionDispatcher,
    GrantPermissionOnEditorAssignment,
    GrantPermissionOnPastEditorAssignment,
    PermissionChecker,
)
from ..models import (
    EditorRevisionRequest,
    PastEditorAssignment,
    PermissionAssignment,
    WjsEditorAssignment,
)
from ..views import ArticleDetails

fake = Faker()


@pytest.mark.django_db
@pytest.mark.parametrize("is_author", [True, False])
def test_director_permission_own_assignment(
    assigned_article: Article,
    director: Account,
    is_author: bool,
    review_settings,
):
    """Director has permission on all editor assignments (except when he is author)."""
    if is_author:
        assigned_article.authors.add(director)

    assert (
        PermissionChecker()(
            assigned_article.articleworkflow,
            director,
            WjsEditorAssignment.objects.get_current(assigned_article),
        )
        != is_author
    )


@pytest.mark.django_db
def test_editor_permission_own_assignment(
    assigned_article: Article,
    review_settings,
):
    """Editor has permission on their own assignment."""
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor

    assert PermissionChecker()(
        assigned_article.articleworkflow, editor, WjsEditorAssignment.objects.get_current(assigned_article)
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "permission_type",
    [PermissionAssignment.PermissionType.NO_NAMES, PermissionAssignment.PermissionType.ALL],
)
def test_editor_permission_assignment_other_editor(
    assigned_article: Article,
    review_settings,
    create_jcom_user,
    permission_type,
):
    """
    Editor has permission on other editors' assignments only if explicitly granted.

    In order to test his, we first check that another editor has no default permission on the assignment.

    Then we grant the permission and check that the permission is granted.

    Finally, we check that the permission is granted only for the requested permission set.
    """
    second_editor = create_jcom_user("second_editor")
    second_editor.add_account_role("section-editor", assigned_article.journal)

    assert not PermissionChecker()(
        assigned_article.articleworkflow,
        second_editor,
        WjsEditorAssignment.objects.get_current(assigned_article),
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )

    GrantPermissionOnEditorAssignment(
        user=second_editor,
        editor_assignment=WjsEditorAssignment.objects.get_current(assigned_article),
    ).run(permission_type=permission_type)

    assert PermissionChecker()(
        assigned_article.articleworkflow,
        second_editor,
        WjsEditorAssignment.objects.get_current(assigned_article),
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )

    see_all = permission_type == PermissionAssignment.PermissionType.ALL
    permission_all = PermissionChecker()(
        assigned_article.articleworkflow,
        second_editor,
        WjsEditorAssignment.objects.get_current(assigned_article),
        permission_type=PermissionAssignment.PermissionType.ALL,
    )

    assert permission_all == see_all


@pytest.mark.django_db
@pytest.mark.parametrize(
    "object_type",
    ["editorassignment", "reviewassignment", "revisionrequest", "pasteditorassignment", "reviewround"],
)
def test_dispatch_grant_on_article(
    assigned_article: Article,
    review_settings,
    create_jcom_user,
    object_type,
):
    """
    GrantPermissionDispatcher create a permission for the correct object and permission is actually granted.
    """
    second_editor = create_jcom_user("other")
    second_editor.add_account_role("section-editor", assigned_article.journal)
    target_user = create_jcom_user("target_user")
    target_user.add_account_role("section-editor", assigned_article.journal)

    if object_type == "reviewround":
        selected_object = assigned_article.reviewround_set.first()
    elif object_type == "pasteditorassignment":
        selected_object = PastEditorAssignment.objects.create(
            editor=second_editor,
            article=assigned_article,
            date_assigned=timezone.now() - timedelta(days=10),
            date_unassigned=timezone.now(),
        )
    elif object_type == "editorassignment":
        selected_object = WjsEditorAssignment.objects.get_current(assigned_article)
    elif object_type == "reviewassignment":
        selected_object = ReviewAssignment.objects.create(
            article=assigned_article,
            reviewer=create_jcom_user(fake.name()),
            editor=second_editor,
            review_round=assigned_article.reviewround_set.first(),
            date_due=timezone.now() + timedelta(days=5),
        )
    elif object_type == "revisionrequest":
        err = EditorRevisionRequest.objects.create(
            article=assigned_article,
            editor=second_editor,
            review_round=assigned_article.reviewround_set.first(),
            date_due=timezone.now() + timedelta(days=5),
        )
        selected_object = err.revisionrequest_ptr

    assert not PermissionChecker()(
        assigned_article.articleworkflow,
        target_user,
        selected_object,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )

    GrantPermissionDispatcher(
        user=target_user,
        object_type=object_type,
        object_id=selected_object.pk,
    ).run(permission_type=PermissionAssignment.PermissionType.NO_NAMES)

    assert PermissionChecker()(
        assigned_article.articleworkflow,
        target_user,
        selected_object,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )


def _create_rr_objects(
    article: Article,
    editor: Account,
    review_round: ReviewRound,
    create_jcom_user: Callable,
) -> tuple[list[EditorRevisionRequest], list[ReviewAssignment]]:
    revision = []
    review = []
    revision.append(
        EditorRevisionRequest.objects.create(
            article=article,
            editor=editor,
            review_round=review_round,
            date_due=timezone.now() + timedelta(days=5),
        ),
    )
    for ___ in range(3):
        review.append(
            ReviewAssignment.objects.create(
                article=article,
                reviewer=create_jcom_user(fake.name()),
                editor=editor,
                review_round=review_round,
                date_due=timezone.now() + timedelta(days=5),
            ),
        )
    return revision, review


@pytest.mark.django_db
@pytest.mark.parametrize("linked_items", [True, False])
def test_set_permission_on_past_assignment(
    assigned_article: Article,
    review_settings,
    create_jcom_user,
    linked_items: bool,
):
    """
    Editor has permission on past editors' assignments only if explicitly granted.

    In order to test his, we first create a past editor and its "shadow" assignment, then we move the current review
    round to a higher number to create a new round. We create a few revision requests and review assignments for the
    past editor and the current editor.

    Then we test that the current editor has no default permission on the past assignment.
    We then assign the permission and check that the permission is granted.
    """
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    past_editor = create_jcom_user("second_editor")
    past_editor.add_account_role("section-editor", assigned_article.journal)

    past_assignment = PastEditorAssignment.objects.create(
        editor=past_editor,
        article=assigned_article,
        date_assigned=timezone.now() - timedelta(days=10),
        date_unassigned=timezone.now(),
    )

    review_assignments = []
    revision_requests = []

    current_rr = assigned_article.current_review_round_object()
    current_rr.round_number = 4
    current_rr.save()

    for index in range(1, 4):
        round_obj = ReviewRound.objects.create(article=assigned_article, round_number=index)
        past_assignment.review_rounds.add(round_obj)
        revisions, reviews = _create_rr_objects(assigned_article, past_editor, round_obj, create_jcom_user)
        review_assignments += reviews
        revision_requests += revisions
    # created but not used directly in tests, they are needed to check that permissions are not
    # automatically inherited. I.e. revision requests or review assignments of the current editor do not interfere
    # with those of the past editor that we are explicitly testing.
    _create_rr_objects(assigned_article, editor, current_rr, create_jcom_user)

    # By default current editor has no permission on past assignments
    assert not PermissionChecker()(
        assigned_article.articleworkflow,
        editor,
        past_assignment,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )

    GrantPermissionOnPastEditorAssignment(
        user=editor,
        editor_assignment=past_assignment,
    ).run(
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        with_reviewers=linked_items,
        with_revisions=linked_items,
    )

    assert PermissionChecker()(
        assigned_article.articleworkflow,
        editor,
        past_assignment,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )
    for revision_request in revision_requests:
        assert (
            PermissionChecker()(
                assigned_article.articleworkflow,
                editor,
                revision_request,
                permission_type=PermissionAssignment.PermissionType.NO_NAMES,
            )
            == linked_items
        )
    for review_assignment in review_assignments:
        assert (
            PermissionChecker()(
                assigned_article.articleworkflow,
                editor,
                review_assignment,
                permission_type=PermissionAssignment.PermissionType.NO_NAMES,
            )
            == linked_items
        )


@pytest.mark.django_db
@pytest.mark.parametrize("linked_items", [True, False])
def test_set_past_editor_permission_on_current_assignment(
    assigned_article: Article,
    review_settings,
    create_jcom_user,
    linked_items: bool,
):
    """
    Past editor has permission on current assignments only if explicitly granted.

    In order to test his, we first create a past editor and its "shadow" assignment, then we move the current review
    round to a higher number to create a new round. We create a few revision requests and review assignments for the
    past editor and the current editor.

    Then we test that the past editor has no default permission on the current assignment.
    We then assign the permission and check that the permission is granted.
    """
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    past_editor = create_jcom_user("second_editor")
    past_editor.add_account_role("section-editor", assigned_article.journal)

    PastEditorAssignment.objects.create(
        editor=past_editor,
        article=assigned_article,
        date_assigned=timezone.now() - timedelta(days=10),
        date_unassigned=timezone.now(),
    )

    review_assignments = []
    revision_requests = []

    current_assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    current_rr = assigned_article.current_review_round_object()
    current_rr.round_number = 2
    current_rr.save()

    for index in range(1, 4):
        round_obj, __ = ReviewRound.objects.get_or_create(article=assigned_article, round_number=index)
        revisions, reviews = _create_rr_objects(
            assigned_article,
            past_editor if index < current_rr.round_number else editor,
            round_obj,
            create_jcom_user,
        )
        if index >= current_rr.round_number:
            review_assignments += reviews
            revision_requests += revisions

    # By default past editor has no permission on current assignment
    assert not PermissionChecker()(
        assigned_article.articleworkflow,
        past_editor,
        current_assignment,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )

    GrantPermissionOnEditorAssignment(
        user=past_editor,
        editor_assignment=current_assignment,
    ).run(
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        with_reviewers=linked_items,
        with_revisions=linked_items,
    )

    assert PermissionChecker()(
        assigned_article.articleworkflow,
        past_editor,
        current_assignment,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
    )
    for revision_request in revision_requests:
        assert (
            PermissionChecker()(
                assigned_article.articleworkflow,
                past_editor,
                revision_request,
                permission_type=PermissionAssignment.PermissionType.NO_NAMES,
            )
            == linked_items
        )
    for review_assignment in review_assignments:
        assert (
            PermissionChecker()(
                assigned_article.articleworkflow,
                past_editor,
                review_assignment,
                permission_type=PermissionAssignment.PermissionType.NO_NAMES,
            )
            == linked_items
        )


@pytest.mark.django_db
def test_article_denied_random_user(assigned_article: Article, normal_user: Account, fake_request: HttpRequest):
    """Normal user can't open the article status page."""
    fake_request.user = normal_user.janeway_account
    view_obj = ArticleDetails()
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert not view_obj.test_func()


@pytest.mark.django_db
def test_can_open_article_author(assigned_article: Article, fake_request: HttpRequest):
    """Any author can open the article status page."""
    view_obj = ArticleDetails()
    for author in assigned_article.authors.all():
        fake_request.user = author
        view_obj.setup(fake_request, pk=assigned_article.pk)
        assert view_obj.test_func()


@pytest.mark.django_db
def test_can_open_article_assigned_editor(assigned_article: Article, normal_user: Account, fake_request: HttpRequest):
    """Assigned editors can open the article status page, normal editors can't."""
    normal_user.add_account_role("section-editor", assigned_article.journal)

    view_obj = ArticleDetails()
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    view_obj.setup(fake_request, pk=assigned_article.pk)
    # Current editor has access to the page
    assert view_obj.test_func()

    fake_request.user = normal_user.janeway_account
    view_obj.setup(fake_request, pk=assigned_article.pk)
    # Random editor has no access to the page
    assert not view_obj.test_func()

    PastEditorAssignment.objects.create(
        editor=normal_user.janeway_account,
        article=assigned_article,
        date_assigned=timezone.now() - timedelta(days=15),
        date_unassigned=timezone.now() - timedelta(days=5),
    )
    # Past editor has access to the page
    assert view_obj.test_func()


@pytest.mark.django_db
def test_can_open_article_assigned_reviewer(
    assigned_article: Article,
    normal_user: Account,
    fake_request: HttpRequest,
    review_form,
):
    """Assigned reviewers can open the article status page, normal reviewers can't."""
    normal_user.add_account_role("reviewer", assigned_article.journal)
    view_obj = ArticleDetails()

    view_obj.setup(fake_request, pk=assigned_article.pk)
    # Random reviewer has no access to the page
    assert not view_obj.test_func()

    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = editor
    AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=WjsEditorAssignment.objects.get_current(assigned_article).editor,
        form_data={"message": "Please review this article"},
        request=fake_request,
    ).run()
    fake_request.user = normal_user.janeway_account
    view_obj.setup(fake_request, pk=assigned_article.pk)
    # Article reviewer has access to the page
    assert view_obj.test_func()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expected",
    (
        True,
        False,
    ),
)
def test_article_detail_permission_author(
    assigned_article: Article,
    fake_request: HttpRequest,
    normal_user: Account,
    expected: bool,
):
    """Access to article detail view is allowed only for authors of the article itself."""
    view_obj = ArticleDetails()
    if expected:
        fake_request.user = assigned_article.authors.first()
    else:
        fake_request.user = normal_user
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert view_obj.test_func() == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expected",
    (
        True,
        False,
    ),
)
def test_article_detail_permission_editor(
    assigned_article: Article,
    fake_request: HttpRequest,
    normal_user: Account,
    expected: bool,
):
    """Access to article detail view is allowed only for editors associated to the article itself."""
    view_obj = ArticleDetails()
    if expected:
        fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    else:
        normal_user.add_account_role(constants.SECTION_EDITOR_ROLE, assigned_article.journal)
        fake_request.user = normal_user
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert view_obj.test_func() == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expected",
    (
        True,
        False,
    ),
)
def test_article_detail_permission_reviewer(
    assigned_article: Article,
    fake_request: HttpRequest,
    normal_user: Account,
    expected: bool,
):
    """Access to article detail view is allowed only for reviewers associated to the article itself."""
    view_obj = ArticleDetails()
    normal_user.add_account_role(constants.REVIEWER_ROLE, assigned_article.journal)
    if expected:
        ReviewAssignment.objects.create(
            article=assigned_article,
            reviewer=normal_user,
            editor=WjsEditorAssignment.objects.get_current(assigned_article).editor,
            review_round=assigned_article.reviewround_set.first(),
            date_due=timezone.now() + timedelta(days=5),
        )
    fake_request.user = normal_user
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert view_obj.test_func() == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expected",
    (
        True,
        False,
    ),
)
def test_article_detail_permission_past_editors(
    assigned_article: Article,
    fake_request: HttpRequest,
    normal_user: Account,
    expected: bool,
):
    """Access to article detail view is allowed only for reviewers associated to the article itself."""
    view_obj = ArticleDetails()
    normal_user.add_account_role(constants.SECTION_EDITOR_ROLE, assigned_article.journal)
    if expected:
        PastEditorAssignment.objects.create(
            editor=normal_user.janeway_account,
            article=assigned_article,
            date_assigned=timezone.now() - timedelta(days=15),
            date_unassigned=timezone.now() - timedelta(days=5),
        )
    fake_request.user = normal_user
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert view_obj.test_func() == expected


@pytest.mark.django_db
def test_article_detail_permission_eo(
    assigned_article: Article, fake_request: HttpRequest, normal_user: Account, eo_user: Account
):
    """Access to article detail view is always allowed to EO."""
    view_obj = ArticleDetails()
    fake_request.user = eo_user
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert view_obj.test_func()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expected",
    (
        True,
        False,
    ),
)
def test_article_detail_permission_director(
    assigned_article: Article,
    fake_request: HttpRequest,
    director: Account,
    normal_user: Account,
    journal_factory,
    expected: bool,
):
    """Access to article detail view is allowed only for director of the article journal."""
    view_obj = ArticleDetails()
    if expected:
        fake_request.user = director
    else:
        journal2 = journal_factory("J2")
        normal_user.add_account_role(constants.DIRECTOR_MAIN_ROLE, journal2)
        fake_request.user = normal_user
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert view_obj.test_func() == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expected",
    (
        True,
        False,
    ),
)
def test_article_detail_permission_typesetter(
    assigned_article: Article,
    fake_request: HttpRequest,
    normal_user: Account,
    expected: bool,
):
    """Access to article detail view is allowed only for typesetters of the current article."""
    view_obj = ArticleDetails()
    normal_user.add_account_role(constants.TYPESETTER_ROLE, assigned_article.journal)
    if expected:
        typsetting_round = TypesettingRound.objects.create(article=assigned_article)
        TypesettingAssignment.objects.create(
            round=typsetting_round,
            typesetter=normal_user,
            assigned=timezone.now() - timedelta(days=15),
        )
    fake_request.user = normal_user
    view_obj.setup(fake_request, pk=assigned_article.pk)
    assert view_obj.test_func() == expected
