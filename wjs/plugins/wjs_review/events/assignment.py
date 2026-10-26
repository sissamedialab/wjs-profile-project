"""Assignment events functions, that are called when an article is submitted.

Journal level configuration is made using the 'WJS_ARTICLE_ASSIGNMENT_FUNCTIONS' setting
"""

import datetime
from typing import TYPE_CHECKING, Callable, Optional

from core.models import AccountRole, Role
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import (
    Case,
    F,
    FloatField,
    Func,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Least, NullIf
from django.utils import timezone
from django.utils.module_loading import import_string
from journal.models import Journal
from plugins.wjs_submission.correction.links import correction_parent
from plugins.wjs_submission.workflow import is_correction
from submission.models import Article
from utils.logic import get_current_request

from wjs.jcom_profile.constants import DIRECTOR_MAIN_ROLE, EO_GROUP, SECTION_EDITOR_ROLE
from wjs.jcom_profile.models import StaffWorkloadParameters

if TYPE_CHECKING:
    from ..models import ArticleWorkflow, WjsEditorAssignment  # noqa


Account = get_user_model()


def get_special_issue_parameters(article):
    """
    Get special issue StaffWorkloadParameters depending on article special issue editors.

    :param article: The assigned article.
    :return: The Editor assignment parameters for a special issue article.
    """
    editors = [
        editor
        for issue in article.issues.filter(issue_type__code="collection")
        for editor in issue.managing_editors.all()
    ]
    return StaffWorkloadParameters.objects.filter(
        journal=article.journal,
        user__in=editors,
        workload__gt=0,
    )


def get_selected_editor_by_workload(users_parameters: QuerySet, journal: Journal) -> Account | None:
    """
    Select the user with the highest workload remaining, regardless the algorithm.

    The workload is calculated as the number of assigned papers in a state where the editor is required to
    process the paper (ie: accepted / rejected / published papers are not considered because they do not generate
    an actual workload on the editor).

    :param users_parameters: The StaffWorkloadParameters for the selected users.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The selected user. If no user is selected, returns None.
    :rtype: Account
    """
    from ..logic import states_where_article_needs_editor
    from ..models import WjsEditorAssignment

    articles = Article.objects.filter(articleworkflow__state__in=states_where_article_needs_editor, journal=journal)
    assigned_papers = (
        WjsEditorAssignment.objects.filter(article__in=articles, editor=OuterRef("user_id"))
        .annotate(count=Func(F("id"), function="Count"))
        .values("count")
    )
    annotated_parameters = users_parameters.annotate(
        assignment_count=Subquery(assigned_papers),
        available_workload=F("workload") - F("assignment_count"),
    )
    parameter = annotated_parameters.order_by("-available_workload", "id").first()
    if parameter:
        return parameter.user
    return None


def get_available_editor_parameters(editors, journal: Journal) -> QuerySet[StaffWorkloadParameters]:
    """
    Retrieve available editor parameters for a given journal and list of editors.

    This function filters and returns a queryset of `StaffWorkloadParameters`
    objects associated with the specified `journal` and `editors`, considering
    only those who have a workload greater than zero, are not disabled, and don't
    fall into a specified vacancy period.

    :param editors: List of users who are editors to filter parameters for.
    :type editors: QuerySet or list[User]
    :param journal: The journal object used to filter parameters.
    :type journal: Journal
    :return: A queryset of `StaffWorkloadParameters` objects matching the criteria.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    editors_id = list(editors)
    base_available = StaffWorkloadParameters.objects.filter(
        journal=journal, user__in=editors_id, workload__gt=0, enabled=True
    )
    base_available1 = base_available.annotate_vacancy()
    q = base_available1.filter(no_vacancy=True)
    return q


def annotate_keyword_ranking(
    article: Article,
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """
    Annotate StaffWorkloadParameters with how well they match the article's keywords. Never excludes.

    Delegates the actual keyword-matching computation to
    :py:meth:`Account.objects.get_editors_with_keywords` (the same annotation used for manual
    editor selection in the "Select new editor" page), skipping its per-editor matching-keywords
    list (not needed here), so only one shared implementation of "how do we count/weight matching
    keywords" exists. Editors with no matching keyword are kept, annotated with
    ``matching_keywords_count=0``/``max_matching_keyword_weight=None`` — ranking them last is the
    caller's job (see :py:func:`rank_editor_parameters`), not this function's.

    :param article: The article whose keywords are matched against.
    :type article: Article
    :param users_parameters: The candidate StaffWorkloadParameters to annotate.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: ``users_parameters`` annotated with ``matching_keywords_count``/
        ``max_matching_keyword_weight``/``keyword_score`` (sourced from ``get_editors_with_keywords``),
        unfiltered.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    annotated_accounts = Account.objects.filter(
        pk__in=users_parameters.values_list("user_id", flat=True),
    ).get_editors_with_keywords(article, include_matching_keywords=False)
    return users_parameters.annotate(
        matching_keywords_count=Coalesce(
            Subquery(annotated_accounts.filter(pk=OuterRef("user_id")).values("num_shared_kwds")[:1]),
            0,
        ),
        max_matching_keyword_weight=Subquery(
            annotated_accounts.filter(pk=OuterRef("user_id")).values("max_matching_keyword_weight")[:1],
        ),
        keyword_score=Subquery(
            annotated_accounts.filter(pk=OuterRef("user_id")).values("keyword_score")[:1],
            output_field=FloatField(),
        ),
    )


def annotate_workload_availability(
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """
    Annotate parameters with monthly-cap and trailing-30-day workload availability. Never filters.

    The effective monthly cap is the smaller of ``workload`` and ``brake_on``. ``brake_on``
    defaults to 0, which means "no EO override" (a 0 ``brake_on`` is ignored and the cap falls
    back to plain ``workload``; only ``brake_on > 0`` participates in the ``min()``). A
    ``brake_on`` of -1 is a distinct sentinel meaning "EO-disabled": the effective cap is forced
    to 0 regardless of ``workload``, so ``available_workload_month`` is never positive and
    automatic assignment's ``available_workload_month__gt=0`` filter excludes the editor
    entirely.

    Two independent windows are annotated, per the "Editor Selection logic" spec's "Workload"
    section: ``available_workload_month`` (current calendar month, used by automatic assignment's
    own *filtering* exclusion) and ``available_workload_30d`` (trailing 30 days, used purely as
    the *ranking* tie-break by both automatic and manual assignment). Both counts exclude
    WITHDRAWN articles. Deselected assignments need no explicit exclusion: BaseDeassignEditor
    (`logic.py`) deletes the WjsEditorAssignment row outright on deselection, so it is already
    gone from either count by construction.

    Never filters or orders the queryset — both are the caller's decision (automatic assignment
    filters on ``available_workload_month__gt=0``; the manual selection view deliberately does
    not, so an EO can see and pick an over-cap editor).

    :param users_parameters: The candidate StaffWorkloadParameters to annotate.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: ``users_parameters`` annotated with ``assignment_count_month``, ``assignment_count_30d``,
        ``effective_workload_cap``, ``available_workload_month`` and ``available_workload_30d``.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    from ..models import ArticleWorkflow, WjsEditorAssignment  # noqa: F811

    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    window_30d = timezone.localtime() - datetime.timedelta(days=30)
    not_withdrawn = ~Q(article__articleworkflow__state=ArticleWorkflow.ReviewStates.WITHDRAWN)

    assigned_this_month = (
        WjsEditorAssignment.objects.filter(
            not_withdrawn,
            editor=OuterRef("user_id"),
            article__journal=OuterRef("journal_id"),
            assigned__gte=month_start,
        )
        .annotate(count=Func(F("id"), function="Count"))
        .values("count")
    )
    assigned_last_30d = (
        WjsEditorAssignment.objects.filter(
            not_withdrawn,
            editor=OuterRef("user_id"),
            article__journal=OuterRef("journal_id"),
            assigned__gte=window_30d,
        )
        .annotate(count=Func(F("id"), function="Count"))
        .values("count")
    )
    return users_parameters.annotate(
        assignment_count_month=Coalesce(Subquery(assigned_this_month), 0),
        assignment_count_30d=Coalesce(Subquery(assigned_last_30d), 0),
        effective_workload_cap=Case(
            When(brake_on=-1, then=Value(0)),
            When(brake_on__gt=0, then=Least(F("workload"), F("brake_on"), output_field=IntegerField())),
            default=F("workload"),
            output_field=IntegerField(),
        ),
        available_workload_month=F("effective_workload_cap") - F("assignment_count_month"),
        available_workload_30d=F("effective_workload_cap") - F("assignment_count_30d"),
    )


def rank_editor_parameters(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]:
    """
    Apply the canonical final ordering: weighted keyword score, shared-keyword count, 30-day availability, then id.

    Uses ``F("keyword_score").desc(nulls_last=True)`` rather than a plain
    ``"-keyword_score"`` string: Postgres sorts NULLs FIRST under DESC by default,
    which would rank a non-matching editor ahead of any editor with a real (if low) match —
    exactly backwards for this ranking.

    The shared-keyword count (``matching_keywords_count``, never NULL: non-matchers are 0) breaks exact
    score ties before availability, e.g. when every editor keyword weight is 0 and all matchers score 0.0.

    :param users_parameters: A queryset already annotated (``keyword_score``) by both
        annotate_keyword_ranking and annotate_workload_availability.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The same queryset, ordered best-candidate-first.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    return users_parameters.order_by(
        F("keyword_score").desc(nulls_last=True),
        F("matching_keywords_count").desc(),
        F("available_workload_30d").desc(nulls_last=True),
        "id",
    )


def default_assign_editors_to_articles(article: Article, **kwargs) -> Optional["WjsEditorAssignment"]:
    """
    Assign editors to article for review. Default algorithm.

    Keyword matching never excludes a candidate: an editor with no matching keyword is kept in
    the ranked queryset, ranked last (behind any editor with a real match), per the "Editor
    Selection logic" spec (rev 1.1.3). The only exclusions in the non-special-issue branch are
    availability (get_available_editor_parameters), authorship, and running out of monthly
    workload (available_workload_month__gt=0) — there is no separate "no keyword match" fallback
    path or metric anymore.
    """
    from ..logic import BaseAssignToEditor

    is_special_issue = bool(article.primary_issue and article.primary_issue.managing_editors.exists())
    if is_special_issue:
        # Special issues keep their own dedicated editor pool and pure-workload selection;
        # keyword matching is not applied here (see spec's "bypasses" section:
        # https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic#implementation-details).
        parameters = get_special_issue_parameters(article)
    else:
        editors = AccountRole.objects.filter(
            journal=article.journal,
            role=Role.objects.get(slug=SECTION_EDITOR_ROLE),
        ).values_list("user")
        parameters = get_available_editor_parameters(journal=article.journal, editors=editors)
    parameters = parameters.exclude(user__in=article.author_accounts.all())
    if parameters:
        request = get_current_request()
        if is_special_issue:
            user = get_selected_editor_by_workload(parameters, journal=article.journal)
        else:
            ranked_parameters = rank_editor_parameters(
                annotate_workload_availability(annotate_keyword_ranking(article, parameters)).filter(
                    available_workload_month__gt=0,
                ),
            )
            matched_parameter = ranked_parameters.first()
            user = matched_parameter.user if matched_parameter else None
        if user:
            assignment = BaseAssignToEditor(editor=user, article=article, request=request, first_assignment=True).run()
            return assignment


def jcom_assign_editors_to_articles(article: Article, **kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign editors to article for review. JCOM algorithm."""
    from ..logic import BaseAssignToEditor

    if article.primary_issue and article.primary_issue.managing_editors.exists():
        parameters = get_special_issue_parameters(article)
    else:
        # Event though we should only ever have one and only one "main director",
        # we keep this more generic implementation, that fits well with special-issue case,
        # and can easily be used to split assignment among several "directors" in the future,
        # by simply changing the filter on the role.
        directors = AccountRole.objects.filter(
            journal=article.journal,
            role=Role.objects.get(slug=DIRECTOR_MAIN_ROLE),
        ).values_list("user")
        parameters = get_available_editor_parameters(journal=article.journal, editors=directors)
    parameters = parameters.exclude(user__in=article.author_accounts.all())
    if parameters:
        request = get_current_request()
        user = get_selected_editor_by_workload(parameters, journal=article.journal)
        if user:
            assignment = BaseAssignToEditor(editor=user, article=article, request=request, first_assignment=True).run()
            return assignment


def assign_editor_random(article: Article, **kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign a random editor, for test purposes."""
    from ..logic import BaseAssignToEditor

    if (
        selected_editor_id := AccountRole.objects.filter(
            journal=article.journal,
            role=Role.objects.get(slug=SECTION_EDITOR_ROLE),
        )
        .values_list("user")
        .order_by("?")
        .first()
    ):
        request = get_current_request()
        selected_editor = Account.objects.get(id=selected_editor_id[0])
        assignment = BaseAssignToEditor(
            editor=selected_editor, article=article, request=request, first_assignment=True
        ).run()
        return assignment


def should_assign_editor(article: Article) -> bool:
    """
    Verify if editor assignment is enabled for the given article.

    :param article: Article instance
    :type article: Article
    :return: editor assignment is enabled
    :rtype: bool
    """
    return not is_correction(article)


def dispatch_assignment(article: Article) -> Optional["WjsEditorAssignment"]:
    """Dispatch editors assignment on journal basis, selecting the requested assignment algorithm."""
    journal = article.journal

    if not should_assign_editor(article):
        return None
    assignment_function = import_string(
        settings.WJS_ARTICLE_ASSIGNMENT_FUNCTIONS.get(
            journal.code, settings.WJS_ARTICLE_ASSIGNMENT_FUNCTIONS.get(None)
        ),
    )
    return assignment_function(article)


def get_select_eo_by_workload(
    article: Article, users_parameters: QuerySet[StaffWorkloadParameters]
) -> Optional["Account"]:
    """
    Select EO based on their workload.

    The workload is calculated as the number of assigned papers in a state where the EO is required to
    process the paper (i.e.: accepted / rejected / published papers are not considered because they do not generate
    an actual workload on the EO.

    :param users_parameters: The StaffWorkloadParameters for the selected users.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The selected EO. If no EO is selected, returns None.
    :rtype: Account
    """
    from ..logic import states_where_article_needs_eo_in_charge
    from ..models import ArticleWorkflow  # noqa

    assigned_papers = (
        ArticleWorkflow.objects.filter(
            state__in=states_where_article_needs_eo_in_charge, eo_in_charge=OuterRef("user_id")
        )
        .annotate(count=Func(F("id"), function="Count"))
        .values("count")
    )
    annotated_parameters = users_parameters.annotate(
        assignment_count=Coalesce(Subquery(assigned_papers), 0),
        available_workload=(F("workload") - (F("assignment_count") + 1))
        / Cast(NullIf(F("workload"), 0), FloatField()),
    )

    parameter = annotated_parameters.order_by(F("available_workload").desc(nulls_last=True), "id").first()

    if parameter:
        return parameter.user
    return None


def select_eo_random(article: Article, users_parameters: QuerySet[StaffWorkloadParameters]) -> Optional["Account"]:
    """Select a random EO member, for test purposes."""
    users = users_parameters.values_list("user", flat=True)

    return Account.objects.filter(pk__in=users).exclude(workload=0).order_by("?").first()


def select_eo_by_linked_article(
    article: Article, users_parameters: QuerySet[StaffWorkloadParameters]
) -> Optional["Account"]:
    """
    Select the EO in charge for a given article based on linked ancestor article.

    This function identifies the linked ancestor article of the given article and retrieves the
    Executive Officer in charge from the associated workflow. If no linked ancestor exists,
    it returns None.

    :param article: The article for which the linked ancestor is evaluated.
    :type article: Article
    :param users_parameters: Staff workload parameters that may influence the evaluation.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The account of the linked EO in charge or None if no ancestor link exists.
    :rtype: Optional[Account]
    """
    parent = correction_parent(article)
    if parent:
        return parent.articleworkflow.eo_in_charge


def get_eo_selection_function(
    article: Article,
) -> Callable[["Article", QuerySet[StaffWorkloadParameters]], Optional["Account"]]:
    """
    Determine the appropriate EO selection function for an article.

    Identifies the EO selection function based on the journal code of the article. If the article
    is a correction, a specific function for selecting EO by linked article is returned.
    Otherwise, it retrieves the default or journal-specific EO assignment function from settings.

    :param article: The article for which the EO selection function is determined.
    :type article: Article
    :return: Callable that determines the EO responsible for the article.
    :rtype: Callable[[Article, QuerySet[StaffWorkloadParameters]], Optional[Account]]
    """
    journal = article.journal.code
    if is_correction(article):
        return select_eo_by_linked_article
    else:
        return import_string(
            settings.WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS.get(
                journal,
                settings.WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS.get(None),
            ),
        )


def dispatch_eo_assignment(article: Article, **kwargs) -> Optional["Account"]:
    """
    Dispatch EO assignment.

    Contrary to :py:function:`wjs_review.events.handlers.dispatch_assignment`, this function directly assigns the EO
    to the article as we don't have a workflow for EO assignment.
    """
    eo_users = Account.objects.filter(groups__name=EO_GROUP)
    users_parameters = StaffWorkloadParameters.objects.filter(
        journal=article.journal, user__in=eo_users, workload__gt=0
    )
    eo_selection_function = get_eo_selection_function(article)
    eo_user = eo_selection_function(article, users_parameters)
    if eo_user:
        article.articleworkflow.eo_in_charge = eo_user
        article.articleworkflow.save()
