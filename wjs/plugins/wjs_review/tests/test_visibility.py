from datetime import timedelta
from typing import Callable

import pytest
from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from django.utils import timezone
from faker import Faker
from plugins.typesetting.models import TypesettingAssignment, TypesettingRound
from review.models import ReviewAssignment, ReviewRound
from submission.models import Article

from wjs.jcom_profile import constants

from ..forms__visibility import UserPermissionsForm
from ..logic import (
    AssignToReviewer,
    BaseAssignToEditor,
    CreateReviewRound,
    HandleEditorDeclinesAssignment,
)
from ..logic__visibility import PermissionChecker
from ..models import (
    EditorRevisionRequest,
    PastEditorAssignment,
    PermissionAssignment,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from ..views import ArticleDetails
from ..views__visibility import EditUserPermissions

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


def _create_rr_objects(
    article: Article,
    editor: Account,
    review_round: ReviewRound,
    create_jcom_user: Callable,
    reviews_count: int = 3,
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
    for ___ in range(reviews_count):
        review.append(
            WorkflowReviewAssignment.objects.create(
                article=article,
                reviewer=create_jcom_user(fake.name()),
                editor=editor,
                review_round=review_round,
                date_due=timezone.now() + timedelta(days=5),
            ),
        )
    return revision, review


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
    fake_request.user = normal_user.janeway_account
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


@pytest.mark.django_db
def test_reviewer_permissions_by_round(assigned_article: Article, normal_user: Account, fake_request: HttpRequest):
    """
    Reviewers can access the article detail page only for the current review round.

    In this test case we create 3 review rounds and assign the user as reviewer to the first and third round.

    The user has:

    - review_round is None -> if it's ever been a reviewer it has permission: during check we are not interested
        in the review round
    - review_round is 0 -> permission on current review round -> granted because the third round it's the current
    - review_round is 1 -> permission on review round 1 -> granted because of explicit assignment
    - review_round is 2 -> permission on review round 2 -> denied, no specific assignment / permission
    - review_round is 3 -> permission on review round 3 -> granted because of explicit assignment
    """
    normal_user.add_account_role(constants.REVIEWER_ROLE, assigned_article.journal)
    current_assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    editor = current_assignment.editor
    review_round_1 = assigned_article.current_review_round_object()
    review_round_2 = CreateReviewRound(assignment=current_assignment).run()
    review_round_3 = CreateReviewRound(assignment=current_assignment).run()
    ReviewAssignment.objects.create(
        article=assigned_article,
        reviewer=normal_user,
        editor=editor,
        review_round=review_round_1,
        date_due=timezone.now() + timedelta(days=5),
    )
    ReviewAssignment.objects.create(
        article=assigned_article,
        reviewer=normal_user,
        editor=editor,
        review_round=review_round_3,
        date_due=timezone.now() + timedelta(days=5),
    )
    checker = PermissionChecker()
    assert checker(
        assigned_article.articleworkflow,
        normal_user,
        assigned_article,
        PermissionAssignment.PermissionType.NO_NAMES,
        review_round=review_round_1.round_number,
    )
    assert not checker(
        assigned_article.articleworkflow,
        normal_user,
        assigned_article,
        PermissionAssignment.PermissionType.NO_NAMES,
        review_round=review_round_2.round_number,
    )
    assert checker(
        assigned_article.articleworkflow,
        normal_user,
        assigned_article,
        PermissionAssignment.PermissionType.NO_NAMES,
        review_round=review_round_3.round_number,
    )
    assert checker(
        assigned_article.articleworkflow,
        normal_user,
        assigned_article,
        PermissionAssignment.PermissionType.NO_NAMES,
        review_round=0,
    )
    assert checker(
        assigned_article.articleworkflow, normal_user, assigned_article, PermissionAssignment.PermissionType.NO_NAMES
    )


@pytest.mark.django_db
def test_save_permission_form(assigned_article: Article, normal_user: Account):
    """Permission form creates or updates permission object."""
    object_type = ContentType.objects.get_for_model(assigned_article.articleworkflow)
    form = UserPermissionsForm(
        data={
            "permission_secondary": PermissionAssignment.BinaryPermissionType.DENY.value,
            "permission": PermissionAssignment.PermissionType.NO_NAMES.value,
            "object_id": assigned_article.articleworkflow.pk,
            "object_type": object_type.pk,
        },
        article=assigned_article,
        user=normal_user,
        object=assigned_article.articleworkflow,
        round=assigned_article.current_review_round(),
        author_notes=False,
    )
    assert form.is_valid()
    permission = form.save()
    assert permission.object_id == assigned_article.articleworkflow.pk
    assert permission.content_type_id == str(object_type.pk)
    assert permission.permission_secondary == PermissionAssignment.BinaryPermissionType.DENY.value
    assert permission.permission == PermissionAssignment.PermissionType.NO_NAMES.value
    old_permission_id = permission.pk

    form = UserPermissionsForm(
        data={
            "permission_secondary": PermissionAssignment.BinaryPermissionType.DENY.value,
            "permission": PermissionAssignment.PermissionType.ALL.value,
            "object_id": assigned_article.articleworkflow.pk,
            "object_type": object_type.pk,
        },
        article=assigned_article,
        user=normal_user,
        object=assigned_article.articleworkflow,
        round=assigned_article.current_review_round(),
        author_notes=False,
    )
    assert form.is_valid()
    permission = form.save()
    assert permission.object_id == assigned_article.articleworkflow.pk
    assert permission.content_type_id == object_type.pk
    assert permission.permission_secondary == PermissionAssignment.BinaryPermissionType.DENY.value
    assert permission.permission == PermissionAssignment.PermissionType.ALL.value
    assert permission.pk == old_permission_id


@pytest.mark.django_db
def test_permission_form_view_setup_reviewer(
    assigned_article: Article, normal_user: Account, fake_request: HttpRequest, create_jcom_user
):
    """Permission editor view initializes the form data according to the current preferences."""
    normal_user.add_account_role(constants.REVIEWER_ROLE, assigned_article.journal)
    current_editor_assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    fake_request.user = current_editor_assignment.editor
    revision, reviews = _create_rr_objects(
        assigned_article,
        current_editor_assignment.editor,
        assigned_article.current_review_round_object(),
        create_jcom_user,
    )
    reviews[0].reviewer = normal_user.janeway_account
    reviews[0].save()
    review_assignments_type = ContentType.objects.get_for_model(reviews[2])
    PermissionAssignment.objects.get_or_create(
        content_type_id=review_assignments_type.pk,
        object_id=reviews[2].pk,
        user=normal_user,
        defaults={
            "permission": PermissionAssignment.PermissionType.NO_NAMES.value,
            "permission_secondary": PermissionAssignment.BinaryPermissionType.ALL.value,
        },
    )

    view_obj = EditUserPermissions()
    view_obj.setup(fake_request, pk=assigned_article.articleworkflow.pk, user_id=normal_user.pk)
    objs = view_obj._get_article_objects()
    # 1 article
    # 1 editor revision request
    # 2 review assignments (normal_user review is not included because there is no need to set permissions for it)
    # 1 editor revision request for author notes
    # (remember that objects are returned in reverse order for more ergonomic display)
    assert len(objs) == 5
    assert objs[-1].object == assigned_article.articleworkflow
    assert objs[-1].round == -1
    assert isinstance(objs[1].object, EditorRevisionRequest)
    assert objs[1].round == 1
    # EditorRevisionRequest is "duplicated" in next review round for selecting author notes
    # remember that "real" rounds start at 1; consider round 0 as the "initial submission"
    assert isinstance(objs[0].object, EditorRevisionRequest)
    assert objs[0].round == 2
    for obj in objs[2:-1]:
        assert obj.round == 1
        assert isinstance(obj.object, WorkflowReviewAssignment)

    initial = view_obj.get_initial()
    assert len(initial) == 5
    for index, item in enumerate(initial):
        object_type = ContentType.objects.get_for_model(objs[index].object)
        assert item["object"] == objs[index].object
        assert item["object_type"] == object_type.pk
        assert item["object_id"] == objs[index].object.pk
        # Remember that we are looking at the default permissions of a reviewer (normal_user is the first reviewer)
        if index in (4,):
            assert item["permission"] == PermissionAssignment.PermissionType.NO_NAMES
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.ALL
        elif index in (
            0,
            1,
        ):
            assert item["permission"] == PermissionAssignment.PermissionType.NO_NAMES
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.DENY
        elif index in (3,):
            # this is the review for which we created a custom permission (see above)
            assert item["permission"] == PermissionAssignment.PermissionType.NO_NAMES
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.ALL
        elif index in (2,):
            assert item["permission"] == PermissionAssignment.PermissionType.DENY
            assert item["permission_secondary"] == PermissionAssignment.PermissionType.DENY


@pytest.mark.django_db
def test_permission_form_view_setup_editor(
    assigned_article: Article, normal_user: Account, fake_request: HttpRequest, create_jcom_user
):
    """
    Permissions for past and current editors are mapped to initial set of values.

    Objects created:
    1 Article
    - 1° Review round -> default user
        3 reviews
        1 editor revision
        1 PastEditor
    - 2° Review round -> normal user
        3 reviews
        1 editor revision

    Article is accessible for current and past editor
    - 1° Review round objects -> original editor has all permissions, current editor has none
    - 2° Review round objects -> original editor has none, current editor has all

    Involved objects:

    | row | object type           |                       | round | User with permissions |
    | --- | --------------------- | --------------------- | ----- | --------------------- |
    | 0   | ArticleWorkflow       | author cover          | **0** | editor 1, editor 2    |
    | 1   | RevisionRequest R1    | revision req R1       | **1** | editor 1              |
    | 2   | ReviewAssignment 1 R1 |                       | 1     | editor 1              |
    | 3   | ReviewAssignment 2 R1 |                       | 1     | editor 1              |
    | 4   | ReviewAssignment 3 R1 |                       | 1     | editor 1              |
    | 5   | RevisionRequest R2    | revision req R2       | **2** |                       |
    | 6   | RevisionRequest R1    | revision author cover | 2     | editor 1              |
    | 7   | ReviewAssignment 1 R2 |                       | 2     | editor 2              |
    | 8   | ReviewAssignment 2 R2 |                       | 2     | editor 2              |
    | 9   | ReviewAssignment 3 R2 |                       | 2     | editor 2              |
    | 10  | RevisionRequest R2    | revision author cover | **3** | editor 2              |
    """
    normal_user.add_account_role(constants.SECTION_EDITOR_ROLE, assigned_article.journal)
    current_editor_assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    fake_request.user = current_editor_assignment.editor
    _create_rr_objects(
        assigned_article,
        current_editor_assignment.editor,
        assigned_article.current_review_round_object(),
        create_jcom_user,
    )
    past_assignment = HandleEditorDeclinesAssignment(
        assignment=current_editor_assignment,
        editor=current_editor_assignment.editor,
        request=fake_request,
    ).run()
    new_assignment = BaseAssignToEditor(
        editor=normal_user.janeway_account,
        article=assigned_article,
        request=fake_request,
    ).run()
    CreateReviewRound(assignment=new_assignment).run()
    _create_rr_objects(
        assigned_article,
        normal_user.janeway_account,
        assigned_article.current_review_round_object(),
        create_jcom_user,
    )
    view_obj = EditUserPermissions()
    fake_request.user = normal_user.janeway_account
    view_obj.setup(fake_request, pk=assigned_article.articleworkflow.pk, user_id=normal_user.pk)
    objs = view_obj._get_article_objects()
    # 1 article
    # 2 editor revision request
    # 6 review assignments
    # 2 editor revision request for author notes
    assert len(objs) == 11
    assert isinstance(objs[0].object, EditorRevisionRequest)
    assert objs[0].round == 3
    assert objs[0].author_notes
    # EditorRevisionRequest is "duplicated" in next review round for selecting author notes
    assert isinstance(objs[1].object, EditorRevisionRequest)
    assert objs[1].round == 2
    assert not objs[1].author_notes
    assert isinstance(objs[2].object, EditorRevisionRequest)
    assert objs[2].round == 2
    assert objs[2].author_notes
    assert isinstance(objs[6].object, EditorRevisionRequest)
    assert objs[6].round == 1
    assert not objs[6].author_notes
    for obj in objs[3:6]:
        assert obj.round == 2
        assert isinstance(obj.object, WorkflowReviewAssignment)
    for obj in objs[7:-1]:
        assert obj.round == 1
        assert isinstance(obj.object, WorkflowReviewAssignment)
    assert objs[-1].object == assigned_article.articleworkflow
    assert objs[-1].round == -1

    # Permissions for current user
    fake_request.user = normal_user.janeway_account
    initial = view_obj.get_initial()
    assert len(initial) == 11
    for index, item in enumerate(initial):
        object_type = ContentType.objects.get_for_model(objs[index].object)
        assert item["object"] == objs[index].object
        assert item["object_type"] == object_type.pk
        assert item["object_id"] == objs[index].object.pk
        if index == 0:
            assert item["permission"] == PermissionAssignment.PermissionType.ALL
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.ALL
        elif index in (2, 6, 7, 8, 9):
            # Objects of the previous review round
            assert item["permission"] == PermissionAssignment.PermissionType.DENY
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.DENY
        elif index in (1, 3, 4, 5, 10):
            # Objects of the current review round
            assert item["permission"] == PermissionAssignment.PermissionType.ALL
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.ALL

    # Permissions for original editor
    fake_request.user = past_assignment.editor
    view_obj = EditUserPermissions()
    view_obj.setup(fake_request, pk=assigned_article.articleworkflow.pk, user_id=past_assignment.editor.pk)
    initial = view_obj.get_initial()
    assert len(initial) == 11
    for index, item in enumerate(initial):
        object_type = ContentType.objects.get_for_model(objs[index].object)
        assert item["object"] == objs[index].object
        assert item["object_type"] == object_type.pk
        assert item["object_id"] == objs[index].object.pk
        if index == 0:
            assert item["permission"] == PermissionAssignment.PermissionType.DENY
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.DENY
        elif index in (2, 6, 7, 8, 9, 10):
            # Objects of the previous review round
            assert item["permission"] == PermissionAssignment.PermissionType.ALL
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.ALL
        elif index in (1, 3, 4, 5):
            # Objects of the current review round
            assert item["permission"] == PermissionAssignment.PermissionType.DENY
            assert item["permission_secondary"] == PermissionAssignment.BinaryPermissionType.DENY
