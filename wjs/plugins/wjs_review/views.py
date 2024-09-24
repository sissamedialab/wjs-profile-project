from itertools import chain
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type, Union

import django_filters
from core import files as core_files
from core import models as core_models
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.paginator import InvalidPage, Page, Paginator
from django.db.models import Q, QuerySet
from django.forms import models as model_forms
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    QueryDict,
)
from django.shortcuts import get_object_or_404
from django.template import Context
from django.urls import resolve, reverse, reverse_lazy
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)
from django_filters.views import FilterView
from journal.models import Issue, Journal
from plugins.typesetting.models import TypesettingAssignment
from review import logic as review_logic
from review.models import ReviewAssignment
from submission.models import Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile import constants
from wjs.jcom_profile import permissions as base_permissions
from wjs.jcom_profile.mixins import HtmxMixin

from . import permissions
from .communication_utils import (
    get_eo_user,
    get_messages_related_to_me,
    group_messages_by_version,
)
from .filters import (
    AuthorArticleWorkflowFilter,
    EOArticleWorkflowFilter,
    MessageFilter,
    ReminderFilter,
    ReviewerArticleWorkflowFilter,
    StaffArticleWorkflowFilter,
    WorkOnAPaperArticleWorkflowFilter,
)
from .forms import (
    ArticleExtraInformationUpdateForm,
    AssignEoForm,
    DecisionForm,
    DeclineReviewForm,
    DeselectReviewerForm,
    EditorDeclinesAssignmentForm,
    EditorRevisionRequestDueDateForm,
    EditorRevisionRequestEditForm,
    EvaluateReviewForm,
    ForwardMessageForm,
    InviteUserForm,
    MessageForm,
    OpenAppealForm,
    ReviewerSearchForm,
    SelectReviewerForm,
    SupervisorAssignEditorForm,
    TimelineFilterForm,
    ToggleMessageReadByEOForm,
    ToggleMessageReadForm,
    UpdateReviewerDueDateForm,
    UploadRevisionAuthorCoverLetterFileForm,
    WithdrawPreprintForm,
)
from .logic import (
    AdminActions,
    render_template_from_setting,
    states_when_article_is_considered_archived,
    states_when_article_is_considered_archived_for_review,
    states_when_article_is_considered_author_pending,
    states_when_article_is_considered_in_production,
    states_when_article_is_considered_in_review,
    states_when_article_is_considered_in_review_for_eo_and_director,
)
from .logic__visibility import PermissionChecker
from .mixins import (
    ArticleAssignedEditorMixin,
    AuthenticatedUserPassesTest,
    EditorRequiredMixin,
    OpenReviewMixin,
    ReviewerRequiredMixin,
)
from .models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    MessageRecipients,
    PermissionAssignment,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from .prophy import Prophy
from .utils import get_report_form

if TYPE_CHECKING:
    from .custom_types import BreadcrumbItem


logger = get_logger(__name__)
Account = get_user_model()


class Manager(AuthenticatedUserPassesTest, TemplateView):
    """Plugin manager page.Just an index."""

    template_name = "wjs_review/index.html"

    def test_func(self):
        """Verify that only staff can access."""
        return base_permissions.has_eo_role(self.request.user)


class BaseRelatedViewsMixin(AuthenticatedUserPassesTest):
    related_views: Dict[str, Dict[str, str]] = {
        constants.EO_GROUP: {
            "wjs_review_eo_pending": _("Pending preprints"),
            "wjs_review_eo_archived": _("Archived preprints"),
            "wjs_review_eo_production": _("Production"),
            "wjs_review_eo_workon": _("Search preprints"),
            "wjs_review_eo_issues_list": _("Pending Issues"),
        },
        constants.DIRECTOR_ROLE: {
            "wjs_review_director_pending": _("Pending preprints"),
            "wjs_review_director_archived": _("Archived preprints"),
            "wjs_review_director_workon": _("Search preprints"),
            "wjs_review_director_issues_list": _("Pending Issues"),
        },
        constants.SECTION_EDITOR_ROLE: {
            "wjs_review_list": _("Pending preprints"),
            "wjs_review_archived_papers": _("Archived preprints"),
            "wjs_review_editor_issues_list": _("Pending Issues"),
        },
        constants.AUTHOR_ROLE: {
            "wjs_review_author_pending": _("Pending preprints"),
            "wjs_review_author_archived": _("Archived preprints"),
        },
        constants.REVIEWER_ROLE: {
            "wjs_review_reviewer_pending": _("Pending preprints"),
            "wjs_review_reviewer_archived": _("Archived preprints"),
        },
        constants.TYPESETTER_ROLE: {
            "wjs_review_typesetter_pending": _("Pending preprints"),
            "wjs_review_typesetter_workingon": _("Working on"),
            "wjs_review_typesetter_archived": _("Archived preprints"),
        },
    }
    extra_links: Dict[str, str]
    role = None

    def load_initial(self, request, *args, **kwargs):
        super().load_initial(request, *args, **kwargs)
        if self.role:
            request.session["role"] = self.role
        current_role = request.session.get("role", self.role)
        if not current_role:
            current_role = base_permissions.main_role(request.journal, request.user)
        if current_role:
            self.extra_links = {
                reverse(view_name): title
                for view_name, title in self.related_views[current_role].items()
                if self._is_available_related_view(request.journal, view_name, request)
            }
        else:
            self.extra_links = {}

    def _is_available_related_view(self, journal: Journal, view_name: str, request: HttpRequest) -> bool:
        """
        Check if the related view is accessible by the user (and it's not the current one).
        """
        url = reverse(view_name)
        if settings.URL_CONFIG == "path":
            url = url.replace(f"/{journal.code}", "")
        resolved = resolve(url)
        view_class = import_string(resolved._func_path)
        # Using __class__ instead of isinstance because derived views are always instances of the base (pending) view
        # and we want to check the exact class.
        view_matches_current_url = self.__class__ == view_class
        view_object = view_class()
        view_object.request = request
        view_object.kwargs = self.kwargs
        view_object.args = self.args
        user_has_permission = view_object.test_func()
        return not view_matches_current_url and user_has_permission

    @property
    def role_label(self):
        return constants.LABELS.get(self.role, self.role)


class ArticleWorkflowBaseMixin(BaseRelatedViewsMixin, ListView):
    model = ArticleWorkflow
    filterset_class = None
    filterset: Optional[django_filters.FilterSet]
    context_object_name = "workflows"
    ordering = ["-modified"]
    title: str
    show_filters = True

    def load_initial(self, request, *args, **kwargs):
        """Setup and validate filterset data."""
        super().load_initial(request, *args, **kwargs)
        if getattr(self, "filterset_class", None):
            self.filterset = self.filterset_class(
                data=self.request.GET if self.request.GET.get("search") else None,
                queryset=self._apply_base_filters(self.model.objects.all()),
                request=self.request,
                journal=self.request.journal,
            )
            self.filterset.is_valid()
        else:
            self.filterset = None

    def _apply_base_filters(self, qs):
        """Apply some base filters before the filterset's "dynamic" ones.

        This function should be overridden by classes that use this mixin if they have particular needs,
        such a filtering on specific users (editor, reviewer,...) or states (pending, production,...) etc.
        """
        return qs.filter(
            article__journal=self.request.journal,
        )

    def get_queryset(self):
        """Filter article by state and filterset values."""
        qs = super().get_queryset()
        base_qs = self._apply_base_filters(qs)
        try:
            if self.filterset.is_valid():
                return self.filterset.filter_queryset(base_qs).distinct()
        except AttributeError:
            pass
        return base_qs.distinct()

    def get_context_data(self, **kwargs):
        """Add the filterset."""
        context = super().get_context_data(**kwargs)
        context["filter"] = self.filterset
        return context


class EditorPending(ArticleWorkflowBaseMixin):
    """Editor's main page."""

    title = _("Pending preprints")
    role = constants.SECTION_EDITOR_ROLE
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/editor/table.html"
    filterset_class = StaffArticleWorkflowFilter
    filterset: StaffArticleWorkflowFilter
    table_configuration_options = {"show_filter_editor": False, "show_filter_reviewer": True, "table_type": "review"}
    """See :py:attr:`EOPending.table_configuration_options` for details."""

    def test_func(self):
        """Allow access only for Editors of this Journal"""
        return base_permissions.has_section_editor_role(self.request.journal, self.request.user)

    def _apply_base_filters(self, qs):
        """
        Keep only articles (workflows) for which the user is editor.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        # Check on user authentication is required because this is run before LoginRequiredMixin as it's called in the
        # setup method of the view.
        if self.request.user.is_authenticated:
            return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
                article__editorassignment__editor__in=[self.request.user],
                state__in=states_when_article_is_considered_in_review,
            )
        return qs.none()


class EditorArchived(EditorPending):
    title = _("Archived preprints")

    def _apply_base_filters(self, qs):
        """
        Keep only articles (workflows) for which the user is editor and a "final" decision has been made.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        past = states_when_article_is_considered_archived_for_review + states_when_article_is_considered_in_production
        state_past = Q(state__in=past) & Q(
            article__editorassignment__editor__in=[self.request.user],
        )
        past_assignment = Q(article__past_editor_assignments__editor__in=[self.request.user])
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(state_past | past_assignment)


class EOPending(ArticleWorkflowBaseMixin):
    """EO's main page."""

    title = _("Pending preprints")
    role = constants.EO_GROUP
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/eo/table.html"
    filterset_class = EOArticleWorkflowFilter
    filterset: EOArticleWorkflowFilter
    ordering = ["-article__date_submitted"]
    table_configuration_options = {"show_filter_editor": True, "show_filter_reviewer": True, "table_type": "review"}
    """
    Configuration options for the table.

    It's meant to be used to pass options to the table template.

    Avaliable options:
    - show_filter_editor: Show the editor filter
    - show_filter_reviewer: Show the reviewer filter
    - show_filter_typesetter: Show the typesetter filter
    - show_filter_author: Show the author filter
    - hide_editor_age: Hide editor assignment age
    - table_type: Type of the table (review or production)
    - reviewer_status: Hide detailed status information and show reviewer's status only
    - show_author_due_date: Show due dates for authors (for revision request and proofreading)
    """

    def test_func(self):
        """Allow access only to EO (or staff)."""
        return base_permissions.has_admin_role(self.request.journal, self.request.user)

    def _apply_base_filters(self, qs):
        """
        Get all the articles in pending state.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            state__in=states_when_article_is_considered_in_review_for_eo_and_director,
        )


class EOArchived(EOPending):
    title = _("Archived preprints")
    table_configuration_options = {"hide_editor_age": True, "show_filter_editor": True, "show_filter_reviewer": True}

    def _apply_base_filters(self, qs):
        """
        Keep only articles (workflows) for which a "final" decision has been made.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            state__in=states_when_article_is_considered_archived,
        )


class EOProduction(EOPending):
    title = _("Papers in production")
    table_configuration_options = {"show_filter_typesetter": True, "table_type": "production"}
    ordering = ["-article__date_accepted"]

    def _apply_base_filters(self, qs):
        """
        Get all articles in production.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            state__in=states_when_article_is_considered_in_production,
        )


class EOWorkOnAPaper(EOPending):
    """Search tool for EO."""

    title = _("Search preprints")
    filterset_class = WorkOnAPaperArticleWorkflowFilter
    filterset: WorkOnAPaperArticleWorkflowFilter
    paginate_by = 100

    def _apply_base_filters(self, qs):
        """
        Get all the articles in pending state.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).order_by("-article__date_submitted")


class BaseWorkOnIssue(BaseRelatedViewsMixin, ListView):
    """View to list pending issues.

    "Pending" here means that the date of the issue if greater of equal to today.

    We do not need to distinguish by issue type (issue vs collection/special-issue):
    we show them all together.

    """

    title = _("Pending Issues")
    role = constants.DIRECTOR_ROLE
    model = Issue
    template_name = "wjs_review/lists/issue_list.html"
    template_table = "wjs_review/lists/elements/issue/table.html"
    context_object_name = "issues"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(journal=self.request.journal, date__gte=timezone.now().date())
            .order_by("date")
        )


class EOWorkOnIssue(BaseWorkOnIssue):
    role = constants.EO_GROUP

    def test_func(self):
        """Allow access only to EO (or staff)."""
        return base_permissions.has_admin_role(self.request.journal, self.request.user)


class DirectorWorkOnIssue(BaseWorkOnIssue):
    role = constants.DIRECTOR_ROLE

    def test_func(self):
        """Allow access only to director."""
        return base_permissions.has_director_role(self.request.journal, self.request.user)


class EditorWorkOnIssue(BaseWorkOnIssue):
    role = constants.SECTION_EDITOR_ROLE

    def test_func(self):
        """Allow access only to director."""
        return permissions.is_any_special_issue_editor(self.request.journal, self.request.user)

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(managing_editors=self.request.user)
        return queryset


class DirectorPending(ArticleWorkflowBaseMixin):
    """Director's main page."""

    title = _("Pending preprints")
    role = constants.DIRECTOR_ROLE
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/director/table.html"
    filterset_class = StaffArticleWorkflowFilter
    filterset: StaffArticleWorkflowFilter
    table_configuration_options = {
        "show_filter_editor": True,
        "show_filter_reviewer": True,
        "table_type": "review",
        "table_variant": "pending",
    }
    """See :py:attr:`EOPending.table_configuration_options` for details."""

    def test_func(self):
        """Allow access only to director."""
        return base_permissions.has_director_role(self.request.journal, self.request.user)

    def _apply_base_filters(self, qs):
        """
        Get all articles in review state except the ones where the director is also the author.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return (
            ArticleWorkflowBaseMixin._apply_base_filters(self, qs)
            .filter(state__in=states_when_article_is_considered_in_review_for_eo_and_director)
            .exclude(article__authors=self.request.user)
        )


class DirectorArchived(DirectorPending):
    title = _("Archived preprints")
    table_configuration_options = {
        **DirectorPending.table_configuration_options,
        "hide_editor_age": True,
        "table_variant": "archive",
    }

    def _apply_base_filters(self, qs):
        """
        Get all articles in final states except the ones where the director is also the author.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return (
            ArticleWorkflowBaseMixin._apply_base_filters(self, qs)
            .filter(state__in=states_when_article_is_considered_archived_for_review)
            .exclude(article__authors=self.request.user)
        )


class DirectorWorkOnAPaper(DirectorPending):
    """Search tool for Director."""

    title = _("Search preprints")
    filterset_class = WorkOnAPaperArticleWorkflowFilter
    filterset: WorkOnAPaperArticleWorkflowFilter
    paginate_by = 100

    def _apply_base_filters(self, qs):
        """
        Get all the articles in pending state.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).order_by("-article__date_submitted")


class AuthorPending(ArticleWorkflowBaseMixin):
    """Author's main page."""

    title = _("Pending preprints")
    role = constants.AUTHOR_ROLE
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/author/table.html"
    filterset_class = AuthorArticleWorkflowFilter
    filterset: AuthorArticleWorkflowFilter
    show_filters = False
    table_configuration_options = {}
    """See :py:attr:`EOPending.table_configuration_options` for details."""

    def test_func(self):
        """Allow access only for Authors of this Journal"""
        return base_permissions.has_author_role(self.request.journal, self.request.user)

    def _apply_base_filters(self, qs):
        """
        Get all articles in pending states where the user is the author.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            (
                Q(state__in=states_when_article_is_considered_in_review)
                | Q(state__in=states_when_article_is_considered_in_production)
                | Q(state__in=states_when_article_is_considered_author_pending)
            )
            & (Q(article__correspondence_author=self.request.user) | Q(article__authors__in=[self.request.user])),
        )


class AuthorArchived(AuthorPending):
    title = _("Archived preprints")
    show_filters = True
    table_configuration_options = {"show_author_due_date": True, "show_filter_author": True}
    """See :py:attr:`EOPending.table_configuration_options` for details."""

    def _apply_base_filters(self, qs):
        """
        Get all articles in final states where the user is the author.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            Q(state__in=states_when_article_is_considered_archived_for_review)
            & (Q(article__correspondence_author=self.request.user) | Q(article__authors__in=[self.request.user])),
        )


class ReviewerPending(ArticleWorkflowBaseMixin):
    """Reviewer's main page."""

    title = _("Pending preprints")
    role = constants.REVIEWER_ROLE
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/reviewer/table.html"
    filterset_class = ReviewerArticleWorkflowFilter
    filterset: ReviewerArticleWorkflowFilter
    show_filters = False
    table_configuration_options = {"reviewer_status": True, "show_filter_editor": True, "show_filter_author": False}
    """See :py:attr:`EOPending.table_configuration_options` for details."""

    def test_func(self):
        """Allow access only for Reviewers of this Journal"""
        return base_permissions.has_reviewer_role(self.request.journal, self.request.user)

    def _apply_base_filters(self, qs):
        """
        Get all articles with pending reviews from the current user.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            article__reviewassignment__reviewer=self.request.user,
            article__reviewassignment__is_complete=False,
        )


class ReviewerArchived(ReviewerPending):
    """A reviewer's old papers."""

    title = _("Archived preprints")
    show_filters = True
    """See :py:attr:`EOPending.table_configuration_options` for details."""

    def _apply_base_filters(self, qs):
        """
        Get all articles with completed reviews from the current user.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            article__reviewassignment__reviewer=self.request.user,
            article__reviewassignment__is_complete=True,
        )


# refs #584
class EditorAssignsThemselvesAsReviewer(HtmxMixin, ArticleAssignedEditorMixin, EditorRequiredMixin, UpdateView):
    """
    Editor assigns themselves as a reviewer.
    """

    model = ArticleWorkflow
    form_class = SelectReviewerForm
    context_object_name = "workflow"
    template_name = "wjs_review/details/editor_assigns_themselves_as_reviewer.html"

    def form_valid(self, form):
        super().form_valid(form)
        messages.success(self.request, _("You have been assigned as a reviewer."))
        response = HttpResponse("ok")
        response["HX-Redirect"] = self.get_success_url()
        return response

    def get_success_url(self):
        return reverse("wjs_article_details", args=(self.object.pk,))

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["editor_assigns_themselves_as_reviewer"] = True
        return kwargs


class SelectReviewer(BaseRelatedViewsMixin, HtmxMixin, ArticleAssignedEditorMixin, EditorRequiredMixin, UpdateView):
    """
    View only checks the login status at view level because the permissions are checked by the queryset by using
    :py:class:`WjsEditorAssignment` relation with the current user.
    """

    title = _("Select a reviewer")
    model = ArticleWorkflow
    form_class = SelectReviewerForm
    context_object_name = "workflow"

    @property
    def page_title(self):
        return f"{self.title} for {self.object.article.title}"

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}), title=self.object),
            BreadcrumbItem(
                url=reverse("wjs_select_reviewer", kwargs={"pk": self.object.pk}), title=self.title, current=True
            ),
        ]

    def get_success_url(self):
        return reverse("wjs_article_details", args=(self.object.pk,))

    def post(self, request, *args, **kwargs) -> HttpResponse:
        """
        Handle POST requests: instantiate a form instance with the passed POST variables and then check if it's valid.
        """
        self.object = self.get_object()
        if self.htmx:
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    @property
    def search_data(self) -> QueryDict:
        """
        Return the search data from the request.

        As the view can be called by either a GET or a POST request, we need to check both.
        """
        return self.request.GET or self.request.POST

    def get_template_names(self) -> List[str]:
        """Select the template based on the request type."""
        if self.htmx:
            if self.request.POST.get("message"):
                return ["wjs_review/select_reviewer/elements/select_reviewer_message_preview.html"]
            elif self.request.headers.get("Hx-Trigger-Name") == "assign-reviewer":
                return ["wjs_review/select_reviewer/elements/select_reviewer_form.html"]
            elif self.request.headers.get("Hx-Trigger-Name") == "editor-assign-themselves":
                return ["wjs_review/editor_assigns_themselves_as_reviewer.html"]
            elif self.request.headers.get("Hx-Trigger-Name") == "search-reviewer-form":
                return ["wjs_review/select_reviewer/elements/reviewers_table.html"]
        return ["wjs_review/select_reviewer/select_reviewer.html"]

    def paginate_queryset(self, queryset, page_size) -> Tuple[Paginator, Optional[Page], Optional[QuerySet], bool]:
        """
        Paginate the reviewers queryset.

        It's managed explicitly as the view is an UpdateView not a ListView.
        """
        paginator = self.get_paginator(queryset, page_size, allow_empty_first_page=False)
        page_kwarg = "page"
        page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
        try:
            page_number = int(page)
        except ValueError:
            if page == "last":
                page_number = paginator.num_pages
            else:
                raise Http404(_("Page is not “last”, nor can it be converted to an int."))
        if paginator.count == 0:
            return paginator, None, None, False
        try:
            page = paginator.page(page_number)
            return paginator, page, page.object_list, page.has_other_pages()
        except InvalidPage as e:
            raise Http404(
                _("Invalid page (%(page_number)s): %(message)s") % {"page_number": page_number, "message": str(e)}
            )

    def get_paginate_by(self, queryset) -> int:
        """
        Get the number of items to paginate by, or ``None`` for no pagination.
        """
        return get_setting("wjs_review", "review_lists_page_size", self.object.article.journal).processed_value

    def get_paginator(self, queryset, per_page, orphans=0, allow_empty_first_page=True, **kwargs) -> Paginator:
        """Return an instance of the paginator for this view."""
        return Paginator(queryset, per_page, orphans=orphans, allow_empty_first_page=allow_empty_first_page)

    def get_objects_list(self) -> List[Union[Account, Prophy]]:
        """
        Get the list of objects to paginate.
        """
        return list(
            chain(
                Account.objects.filter_reviewers(self.object, self.search_data),
                Prophy(self.object.article).get_not_account_article_prophycandidates(self.search_data),
            ),
        )

    def _render_message_preview(self, form: SelectReviewerForm) -> str:
        logic_context = form.get_message_context()
        preview = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_invitation_message_body",
            journal=self.object.article.journal,
            request=self.request,
            context=logic_context,
            template_is_setting=True,
        )
        return preview

    def get_context_data(self, **kwargs) -> Context:
        context = super().get_context_data(**kwargs)
        context["htmx"] = self.htmx
        context["search_form"] = self.get_search_form()
        paginator, page, objects_list, is_paginated = self.paginate_queryset(
            self.get_objects_list(), self.get_paginate_by(self.get_objects_list())
        )
        querystring = self.request.GET.copy()
        if "page" in querystring:
            del querystring["page"]
        context.update(
            {
                "paginator": paginator,
                "page_obj": page,
                "is_paginated": is_paginated,
                "object_list": objects_list,
                "reviewers": objects_list,
                "querystring": querystring,
            }
        )
        context["reviewer"] = context["form"].data.get("reviewer")
        if context["form"].data.get("reviewer"):
            context["preview"] = self._render_message_preview(form=context["form"])
        return context

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs

    def get_search_form(self) -> ReviewerSearchForm:
        return ReviewerSearchForm(self.search_data if self.search_data else None)

    def form_valid(self, form: SelectReviewerForm) -> HttpResponse:
        """
        Executed when SelectReviewerForm is valid

        Even if the form is valid, checks in logic.AssignToReviewer -called by form.save- may fail as well.
        """
        try:
            messages.success(self.request, _("The reviewer has been succesfully selected."))
            return super().form_valid(form)
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class InviteReviewer(HtmxMixin, ArticleAssignedEditorMixin, EditorRequiredMixin, UpdateView):
    """Invite external users as reviewers.

    The user is created as inactive and his/her account is marked
    without GDPR explicitly accepted, Invited user base
    information are encoded to generate a token to be appended to
    the url for GDPR acceptance.
    """

    model = ArticleWorkflow
    form_class = InviteUserForm
    success_url = reverse_lazy("wjs_review_list")
    context_object_name = "workflow"

    def _render_message_preview(self, form: InviteUserForm) -> str:
        form_context = form.get_message_context()
        preview = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_invitation_message_body",
            journal=self.object.article.journal,
            request=self.request,
            context=form_context,
            template_is_setting=True,
        )
        return preview

    def get_context_data(self, **kwargs) -> Context:
        context = super().get_context_data(**kwargs)
        context["preview"] = self._render_message_preview(form=context["form"])
        return context

    def get_template_names(self) -> List[str]:
        """Select the template based on the request type."""
        if self.request.headers.get("Hx-Trigger-Name") == "invite-reviewer-message":
            return ["wjs_review/select_reviewer/elements/select_reviewer_message_preview.html"]
        return ["wjs_review/select_reviewer/invite_external_reviewer.html"]

    def get_success_url(self):
        return reverse("wjs_article_details", args=(self.object.pk,))

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["instance"] = self.object
        if "prophy_account_id" in self.kwargs.keys():
            kwargs["prophy_account_id"] = self.kwargs["prophy_account_id"]
        return kwargs

    def form_valid(self, form):
        """
        Executed when InviteUserForm is valid

        Even if the form is valid, checks in logic.AssignToReviewer -called by form.save- may fail as well.
        """
        try:
            return super().form_valid(form)
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)

    def post(self, request, *args, **kwargs) -> HttpResponse:
        """
        Handle POST requests: instantiate a form instance with the passed POST variables and then check if it's valid.

        If we have been called via htmx, it means we are just displaying the form in the modal.
        """
        if self.htmx:
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)


class ArticleDetails(HtmxMixin, BaseRelatedViewsMixin, DetailView):
    title = _("Article status")
    model = ArticleWorkflow
    template_name = "wjs_review/details/articleworkflow_detail.html"
    context_object_name = "workflow"
    form_class = TimelineFilterForm

    def test_func(self):
        """Allow access only one has permission on the article."""

        if not self.request.user or not self.request.user.is_authenticated:
            return False

        self.object = self.get_object()
        return PermissionChecker()(
            self.object,
            self.request.user,
            self.object,
            permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        )

    @property
    def page_title(self):
        return f"{self.title}: {self.object.article.title}"

    def get_template_names(self):
        if self.htmx:
            return ["wjs_review/details/sections/timeline.html"]
        return super().get_template_names()

    def get_form(self, data=None):
        form = self.form_class(data)
        form.is_valid()
        return form

    def get_current_review_assignment(self) -> Optional[ReviewAssignment]:
        """
        Get the current review assignment for the current user.
        """
        qs = WorkflowReviewAssignment.objects.filter(
            reviewer=self.request.user,
            article=self.object.article,
            review_round=self.object.article.current_review_round_object(),
            is_complete=False,
        )
        try:
            return qs.get()
        except WorkflowReviewAssignment.DoesNotExist:
            return None
        except WorkflowReviewAssignment.MultipleObjectsReturned:
            logger.warning(
                f"Multiple review assignments for the same user on the same article:"
                f" {self.request.user} - {self.object.article}"
            )
            return qs.first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = self.get_form(self.request.GET)
        messages = get_messages_related_to_me(self.request.user, self.object.article)
        messages = messages.exclude(verbosity=Message.MessageVerbosity.EMAIL)
        context["timeline_messages"] = group_messages_by_version(
            self.object.article, messages, filters=context["form"].cleaned_data
        )
        if self.object.state in states_when_article_is_considered_in_review:
            context["review_versions"] = self.object.get_review_versions(self.request.user)
            context["review"] = True
            context["current_review_assignment"] = self.get_current_review_assignment()
        if self.object.state in states_when_article_is_considered_in_production:
            context["review_versions"] = self.object.get_review_versions(self.request.user)
            context["production_versions"] = self.object.get_production_versions(self.request.user)
            context["production"] = True
        return context


class ReviewerDeclineReview(HtmxMixin, OpenReviewMixin, UpdateView):

    title = _("Decline review")
    form_class = DeclineReviewForm
    template_name = "wjs_review/details/decline_review.html"
    pk_url_kwarg = "pk"

    def get_success_url(self) -> str:
        return reverse("wjs_review_reviewer_pending")

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        """
        Executed when ReviewerDeclineReviewForm is valid

        Even if the form is valid, checks in logic.DeclineReview -called by form.save- may fail as well.
        """
        try:
            super().form_valid(form)
            response = HttpResponse("ok")
            response["HX-Redirect"] = self.get_success_url()
            messages.success(self.request, _("The review has been declined."))
            return response
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class PostponeRevisionRequestDueDate(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """
    View to postpone the date_due of a revision request (done by the editor)
    """

    title = _("Change revision due date")
    model = EditorRevisionRequest
    form_class = EditorRevisionRequestDueDateForm
    template_name = "wjs_review/details/editor_revision_request_date_due_form.html"
    context_object_name = "revision_request"

    def test_func(self):
        """
        Check that the user is the article's editor
        """
        self.article = self.get_object().article.articleworkflow
        return permissions.is_article_editor(self.article, self.request.user)

    def form_valid(self, form):
        """
        Executed when EditorRevisionRequestDueDateForm is valid
        """
        form.save()
        messages.success(self.request, _("The due date has been postponed."))
        response = HttpResponse("ok")
        response["HX-Redirect"] = self.get_success_url()
        return response

    def get_success_url(self):
        return reverse("wjs_article_details", args=(self.object.article.articleworkflow.id,))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["user"] = self.request.user
        return kwargs


class EvaluateReviewRequest(OpenReviewMixin, UpdateView):
    form_class = EvaluateReviewForm
    template_name = "wjs_review/evaluate_review/review_evaluate.html"
    success_url = reverse_lazy("wjs_review_list")
    title = _("Accept/Decline invite to review")
    use_access_code = True

    def get_success_url(self) -> str:
        """Redirect to a different URL according to the decision."""
        self.object.refresh_from_db()
        url = str(self.success_url)
        if self.object.date_accepted:
            url = review_logic.generate_access_code_url(
                "wjs_review_review",
                self.object,
                self.access_code,
            )
        elif self.object.date_declined:
            url = review_logic.generate_access_code_url(
                "wjs_declined_review",
                self.object,
                self.access_code,
            )
        return url

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk}),
                title=self.object.article.articleworkflow,
            ),
            BreadcrumbItem(
                url=reverse("wjs_evaluate_review", kwargs={"assignment_id": self.object.pk}),
                title=self.title,
                current=True,
            ),
        ]

    def get_queryset(self) -> QuerySet[ReviewAssignment]:
        queryset = super().get_queryset()
        if self.kwargs.get("token", None):
            return queryset.filter(reviewer__jcomprofile__invitation_token=self.kwargs.get("token", None))
        else:
            return queryset

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["token"] = self.kwargs.get("token", None)
        return kwargs

    def form_valid(self, form: EvaluateReviewForm) -> HttpResponse:
        """
        Executed when :py:class:`EvaluateReviewForm` is valid.

        Even if the form is valid, checks in :py:class:`logic.EvaluateReview` -called by form.save- may fail as well.
        """
        try:
            return super().form_valid(form)
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class ReviewDeclined(BaseRelatedViewsMixin, OpenReviewMixin):
    title = _("Review Declined")
    template_name = "wjs_review/submit_review/review_declined.html"
    incomplete_review_only = False
    use_access_code = True

    def test_func(self):
        """Allow access only to the reviewer who has completed the review."""
        self.article = self.get_object().article.articleworkflow
        return permissions.is_article_reviewer(self.article, self.request.user) or base_permissions.has_eo_role(
            self.request.user
        )

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk}),
                title=self.object.article.articleworkflow,
            ),
            BreadcrumbItem(
                url=self.request.path_info,
                title=self.title,
                current=True,
            ),
        ]


class ReviewEnd(BaseRelatedViewsMixin, OpenReviewMixin):
    title = _("Review submitted")
    template_name = "wjs_review/submit_review/review_end.html"
    incomplete_review_only = False

    def test_func(self):
        """Allow access only to the reviewer who has completed the review."""
        self.article = self.get_object().article.articleworkflow
        return permissions.is_article_reviewer(self.article, self.request.user) or base_permissions.has_eo_role(
            self.request.user
        )

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk}),
                title=self.object.article.articleworkflow,
            ),
            BreadcrumbItem(
                url=self.request.path_info,
                title=self.title,
                current=True,
            ),
        ]


class ReviewSubmit(BaseRelatedViewsMixin, EvaluateReviewRequest, ReviewerRequiredMixin):
    template_name = "wjs_review/submit_review/review_submit.html"
    title = _("Sumbit review")
    use_access_code = False

    @property
    def allow_draft(self):
        """
        Check if the user is allowed to submit a draft report.

        Used both in the template to hide the draft button and in the view to check the draft status.
        """
        return get_setting(
            "general",
            "enable_save_review_progress",
            self.request.journal,
        ).processed_value

    @property
    def _submitting_report_final(self) -> bool:
        """Check if the user is submitting the final report."""
        return self.request.POST.get("submit_report", None) == "1"

    @property
    def _submitting_report_draft(self) -> bool:
        """Check if the user is submitting a final report."""
        return self.request.POST.get("submit_report", None) == "0" and self.allow_draft

    @property
    def _submitting_report(self) -> bool:
        """Check if the user is submitting a report vs. updating their acceptance status."""
        return self._submitting_report_final or self._submitting_report_draft

    def _get_report_data(self) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Return the data and files for the report form.

        This contains actual data only if user is submitting a report, otherwise we won't pass any data because it will
        trigger form invalid state because acceptance form data are not compatible with report form.
        """
        if self._submitting_report:
            return {"data": self.request.POST or None, "files": self.request.FILES or None}
        else:
            return {"data": None, "files": None}

    def _get_report_form(self):
        """Instantiate ReportForm (instantiated from ReviewAssigment.form object)."""
        form = get_report_form(self.request.journal.code)
        return form(
            review_assignment=self.object,
            submit_final=self._submitting_report_final,
            request=self.request,
            **self._get_report_data(),
        )

    def get_context_data(self, **kwargs) -> Context:
        """Add ReportForm to the context."""
        context = super().get_context_data(**kwargs)
        if "report_form" not in context:
            context["report_form"] = self._get_report_form()
        context["allow_draft"] = self.allow_draft
        return context

    def _process_report(self) -> Union[HttpResponseRedirect, HttpResponse]:
        """
        Process ReportForm and redirect to the appropriate page.

        If form is not valid or exception is raised by the logic, the form is rendered again with the error.
        """
        report_form = self._get_report_form()
        if report_form.is_valid():
            try:
                report_form.save()
                return HttpResponseRedirect(self.get_success_url())
            except (ValueError, ValidationError) as e:
                report_form.add_error(None, e)
        return self.render_to_response(self.get_context_data(report_form=report_form))

    def get_success_url(self) -> str:
        """
        Redirect to a different URL according to the decision.

        If the user is submitting the report, redirect to the end of the review process, otherwise redirect to the
        same page for further updates.
        """
        if self._submitting_report_final:
            return review_logic.generate_access_code_url(
                "wjs_review_end",
                self.object,
                self.access_code,
            )
        else:
            return super().get_success_url()

    def form_valid(self, form: EvaluateReviewForm) -> HttpResponse:
        """
        Executed when :py:class:`EvaluateReviewForm` is valid.

        Even if the form is valid, checks in :py:class:`logic.EvaluateReview` -called by form.save- may fail as well.

        If the user is submitting the report, the ReportForm is processed, skipping
        EvaluateReviewForm. EvaluateReviewForm must still be valid, but in can only be invalid if the user
        declines and not provide a motivation, which excludes the case of submitting the report.
        """
        if self._submitting_report:
            return self._process_report()
        else:
            return super().form_valid(form)


class AssignEoToArticle(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    model = ArticleWorkflow
    form_class = AssignEoForm
    template_name = "wjs_review/assign_eo/assign_eo.html"
    context_object_name = "workflow"

    def test_func(self):
        """Verify that only staff can access."""
        return base_permissions.has_eo_role(self.request.user)

    def get_success_url(self):
        return reverse("wjs_article_details", args=(self.object.id,))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs


class ArticleAdminDispatchAssignment(AuthenticatedUserPassesTest, View):
    model = ArticleWorkflow

    def test_func(self):
        """Verify that only staff can access."""
        return base_permissions.has_eo_role(self.request.user)

    def load_initial(self, request, *args, **kwargs):
        """Set current article on object for convenience."""
        super().load_initial(request, *args, **kwargs)
        self.articleworkflow = get_object_or_404(self.model, pk=self.kwargs["pk"])

    def get(self, *args, **kwargs):
        """Dispatch the assignment."""
        AdminActions(
            workflow=self.articleworkflow,
            request=self.request,
            user=self.request.user,
            decision="dispatch",
        ).run()
        return HttpResponseRedirect(reverse("wjs_article_details", args=(self.articleworkflow.id,)))


class ArticleAdminDecision(BaseRelatedViewsMixin, UpdateView):
    model = ArticleWorkflow
    form_class = DecisionForm
    template_name = "wjs_review/make_decision/decision.html"
    context_object_name = "workflow"
    title = _("Make decision")

    def test_func(self):
        """Verify that only EO can access."""
        return base_permissions.has_eo_role(self.request.user)

    def get_success_url(self):
        return reverse("wjs_article_details", args=(self.object.id,))

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["admin_form"] = True
        kwargs["initial"] = {"decision": self.request.GET.get("decision")}
        return kwargs

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}), title=self.object),
            BreadcrumbItem(
                url=reverse("wjs_article_admin_decision", kwargs={"pk": self.object.pk}),
                title=self.title,
                current=True,
            ),
        ]

    def form_valid(self, form):
        """
        Executed when DecisionForm is valid

        Even if the form is valid, checks in logic.HandleDecision -called by form.save- may fail as well.
        """
        try:
            return super().form_valid(form)
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["hide_reviews"] = True
        return context


class ArticleDecision(BaseRelatedViewsMixin, ArticleAssignedEditorMixin, EditorRequiredMixin, UpdateView):
    model = ArticleWorkflow
    form_class = DecisionForm
    template_name = "wjs_review/make_decision/decision.html"
    context_object_name = "workflow"
    title = _("Make decision")

    def get_queryset(self) -> QuerySet[ArticleWorkflow]:
        """Filter queryset to ensure only :py:class:`ArticleWorkflow` in EDITOR_SELECTED state are filtered."""
        return super().get_queryset().filter(state=ArticleWorkflow.ReviewStates.EDITOR_SELECTED)

    def get_success_url(self):
        """
        Redirect after decision.

        If the editor has not make a decision (state is still EDITOR_SELECTED), redirect to the Editor decision page,
        otherwise redirect to the article details page.

        ArticleWorkflow must be reloaded from the database to ensure the state is updated.
        """
        self.object.refresh_from_db()
        if self.object.state == self.object.ReviewStates.EDITOR_SELECTED:
            return reverse("wjs_article_decision", args=(self.object.id,))
        else:
            return reverse("wjs_article_details", args=(self.object.id,))

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["initial"] = {"decision": self.request.GET.get("decision")}
        kwargs["has_pending_reviews"] = self.pending_reviews.exists()
        return kwargs

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}), title=self.object),
            BreadcrumbItem(
                url=reverse("wjs_article_decision", kwargs={"pk": self.object.pk}), title=self.title, current=True
            ),
        ]

    def form_valid(self, form):
        """
        Executed when DecisionForm is valid

        Even if the form is valid, checks in logic.HandleDecision -called by form.save- may fail as well.
        """
        try:
            return super().form_valid(form)
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)

    @property
    def current_reviews(self) -> QuerySet[ReviewAssignment]:
        """Return the reviews for the current review round for the article."""
        return self.object.article.reviewassignment_set.filter(
            review_round=self.object.article.current_review_round_object(),
        )

    @property
    def submitted_reviews(self) -> QuerySet[ReviewAssignment]:
        """Return the submitted reviews for the current review round."""
        return self.current_reviews.filter(date_complete__isnull=False, date_accepted__isnull=False).exclude(
            decision="withdrawn"
        )

    @property
    def declined_reviews(self) -> QuerySet[ReviewAssignment]:
        """Return the declined reviews for the current review round."""
        # Attention: this does not return withdrawn reviews, is this what's intended?
        return self.current_reviews.filter(date_declined__isnull=False)

    @property
    def open_reviews(self) -> QuerySet[ReviewAssignment]:
        """Return accepted but not completed reviews for the current review round."""
        return self.current_reviews.filter(
            date_complete__isnull=True,
            date_accepted__isnull=False,
            date_declined__isnull=True,
        )

    @property
    def pending_reviews(self) -> QuerySet[ReviewAssignment]:
        """Return not completed reviews for the current review round."""
        return self.current_reviews.filter(
            date_complete__isnull=True,
            date_declined__isnull=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["declined_reviews"] = self.declined_reviews
        context["submitted_reviews"] = self.submitted_reviews
        context["open_reviews"] = self.open_reviews
        context["form_fields"] = get_report_form(self.object.article.journal.code)().fields
        context["open_reviewers_list"] = ", ".join([review.reviewer.full_name() for review in self.open_reviews])
        return context


class ArticleMessages(HtmxMixin, BaseRelatedViewsMixin, FilterView):
    """
    All messages of a certain user that are related to an article.
    """

    title = _("Messages")
    model = Message
    template_name = "wjs_review/article_messages/article_messages.html"
    context_object_name = "messages_list"
    filterset_class = MessageFilter

    def load_initial(self, request, *args, **kwargs):
        """Filter only messages related to a certain article and that the current user can see."""
        super().load_initial(request, *args, **kwargs)
        self.workflow = get_object_or_404(ArticleWorkflow, pk=self.kwargs["pk"])
        self.article = self.workflow.article

    def test_func(self):
        """Allow access only one has permission on the article."""

        if not self.request.user or not self.request.user.is_authenticated:
            return False

        return PermissionChecker()(
            self.article.articleworkflow,
            self.request.user,
            self.article,
            permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        )

    def get_template_names(self):
        if self.htmx:
            return ["wjs_review/article_messages/elements/messages_list.html"]
        return super().get_template_names()

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.article.articleworkflow.pk}),
                title=str(self.article.articleworkflow),
            ),
            BreadcrumbItem(url=self.request.path, title=_("Messages"), current=True),
        ]

    def get_queryset(self):
        """Return the list of messages that the user is entitled to see for this article."""
        return get_messages_related_to_me(user=self.request.user, article=self.article)

    def get_context_data(self, **kwargs):
        """Add the article to the context."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.article.articleworkflow
        context["article"] = self.article
        # Retrieve manytomany through model:
        # - self.get_queryset() gives Messages
        # - the toggle form wants MessageRecipients (because the "read" flag is in the through-table)
        # This works because there is only one MessageRecipient for each Message-Recipient combination.
        messagerecipients_records = MessageRecipients.objects.filter(
            message__in=self.get_queryset(),
            recipient=self.request.user,
        )
        forms = {
            mr.message.pk: ToggleMessageReadForm(instance=mr, prefix=f"toggle-{mr.pk}")
            for mr in messagerecipients_records
        }
        context["forms"] = forms
        # The following is context to allow the EO to mark messages as read
        # TODO Refactor ArticleMessages to not create a form for each message. Issue 55
        message_records = Message.objects.filter(
            id__in=self.get_queryset(),
        )
        eo_forms = {
            mr.id: ToggleMessageReadByEOForm(instance=mr, prefix=f"toggle-eo-{mr.pk}") for mr in message_records
        }
        context["eo_forms"] = eo_forms
        return context


class MessageAttachmentDownloadView(AuthenticatedUserPassesTest, DetailView):
    """Let the recipients of a message with attachment download the attachment."""

    model = Message
    pk_url_kwarg = "message_id"

    def test_func(self):
        """The recipients and the actor of the message can download the file."""
        user = self.request.user
        message = self.get_object()
        return (
            user == message.actor
            or user in message.recipients.all()
            or base_permissions.has_admin_role(self.request.journal, user)
        )

    def get(self, request, *args, **kwargs):
        """Serve the attachment file."""
        attachment = core_models.File.objects.get(pk=self.kwargs["attachment_id"])
        article = self.get_object().target
        # Here, public=True means that the downloaded file will have a human-readable name, not the uuid
        return core_files.serve_file(request, attachment, article, public=True)


class WriteMessage(BaseRelatedViewsMixin, CreateView):
    """A view to let the user write a new message.

    The view also lists all messages of a certain article that the user can see.
    """

    model = Message
    template_name = "wjs_review/write_message/write_messages.html"
    form_class = MessageForm
    note = False
    to_author = False
    to_typesetter = False
    source_message = None
    "The message we are replying to"

    def load_initial(self, request, *args, **kwargs):
        """Filter only messages related to a certain article and that the current user can see."""
        super().load_initial(request, *args, **kwargs)
        self.workflow = get_object_or_404(ArticleWorkflow, pk=self.kwargs["pk"])
        self.article = self.workflow.article
        if self.kwargs.get("original_message_pk"):
            self.source_message = get_object_or_404(Message, pk=self.kwargs["original_message_pk"])
            is_actor_author = permissions.is_article_author(self.workflow, self.source_message.actor)
            is_actor_typesetter = permissions.is_article_typesetter(self.workflow, self.source_message.actor)
            is_current_author = permissions.is_article_author(self.workflow, self.request.user)
            is_current_typesetter = permissions.is_article_typesetter(self.workflow, self.request.user)
            if is_actor_author and is_current_typesetter:
                self.to_author = True
            elif is_actor_typesetter and is_current_author:
                self.to_typesetter = True
        else:
            self.source_message = None
        if self.kwargs.get("recipient_id"):
            self.recipient = get_object_or_404(Account, pk=self.kwargs["recipient_id"])
        else:
            self.recipient = None
        messages = get_messages_related_to_me(user=self.request.user, article=self.article)
        self.messages = messages.filter(Q(recipients__in=[self.recipient]) | Q(actor=self.recipient))

    def test_func(self):
        """
        Allow access if specific permissions are met.

        - Generic message: any permission on the article
        - Note to self: any permission on the article
        - Reply to message: any permission on the article
        - Message Typesetter -> Author: Must be a typesetter
        - Message Author -> Typesetter: Must be an author
        """
        if self.to_author:
            return permissions.is_article_typesetter(
                self.workflow,
                self.request.user,
            )
        if self.to_typesetter:
            return permissions.is_article_author(
                self.workflow,
                self.request.user,
            )

        if not self.request.user or not self.request.user.is_authenticated:
            return False

        return PermissionChecker()(
            self.article.articleworkflow,
            self.request.user,
            self.article,
            permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        )

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        if self.note:
            return [
                BreadcrumbItem(
                    url=reverse("wjs_article_details", kwargs={"pk": self.article.articleworkflow.pk}),
                    title=str(self.article.articleworkflow),
                ),
                BreadcrumbItem(url=self.request.path, title=self.title, current=True),
            ]
        else:
            return [
                BreadcrumbItem(
                    url=reverse("wjs_article_details", kwargs={"pk": self.article.articleworkflow.pk}),
                    title=str(self.article.articleworkflow),
                ),
                BreadcrumbItem(
                    url=reverse("wjs_article_messages", kwargs={"pk": self.article.articleworkflow.pk}),
                    title=_("Messages"),
                ),
                BreadcrumbItem(url=self.request.path, title=self.title, current=True),
            ]

    @property
    def title(self):
        if self.note:
            return _("Add a personal note")
        if self.source_message:
            return _('Reply to message "%s"') % self.source_message.subject
        if self.to_author:
            return _("Write a message to the author")
        if self.to_typesetter:
            return _("Write a message to the typesetter")
        return _("Write a message")

    def get_default_recipients(self):
        """Return the default recipients for the message."""
        if self.source_message:
            recipients = list(self.source_message.messagerecipients_set.all().values_list("recipient", flat=True)) + [
                self.source_message.actor.pk
            ]
            return list(filter(lambda x: x != self.request.user.pk, recipients))
        if self.to_author:
            # If the message is directly to the author, the EO is the default recipient
            # (used, for instance, when typ writes to au with EO moderation)
            return [get_eo_user(self.workflow.article).pk]
        if self.to_typesetter:
            # If the message is to the typesetter, the typesetter is the default recipient
            return [
                TypesettingAssignment.objects.filter(
                    round__article=self.workflow.article,
                )
                .order_by("round__round_number")
                .last()
                .typesetter.pk
            ]

        return [self.recipient] if self.recipient else []

    def get_to_be_forwarded_to(self) -> Account | None:
        """
        Return the final recipient of the message.
        """
        if self.to_author:
            return self.workflow.article.correspondence_author

    def get_recipients_from_formset(self):
        """
        Get the recipients from the formset.

        It allows to inject the recipients from the formset (which is only used to build the UI) into the form.
        """
        recipients_formset = self.get_form_class().get_formset_class()(
            prefix="recipientsFS",
            form_kwargs={
                "actor": self.request.user,
                "article": self.article,
            },
            data=self.request.POST,
        )
        if recipients_formset.is_valid():
            recipients = [f.cleaned_data["recipient"].id for f in recipients_formset if "recipient" in f.cleaned_data]
            if recipients:
                return recipients
        return []

    def get_form_kwargs(self) -> Dict[str, Any]:
        """Add article (target) to the form's kwargs.

        Actor will be evinced by the form directly from the request.
        """
        kwargs = super().get_form_kwargs()
        if "data" in kwargs:
            cloned_data = kwargs["data"].copy()
            if self.source_message:
                cloned_data["recipients"] = self.get_default_recipients()
            elif self.to_author:
                cloned_data["recipients"] = self.get_default_recipients()
            elif self.to_typesetter:
                cloned_data["recipients"] = self.get_default_recipients()
            else:
                cloned_data["recipients"] = self.get_recipients_from_formset()
            kwargs["data"] = cloned_data
        kwargs["actor"] = self.request.user
        kwargs["target"] = self.article
        kwargs["note"] = self.note
        kwargs["hide_recipients"] = self.note or self.to_author or self.to_typesetter
        return kwargs

    def get_sender_label(self):
        """Return the label for the sender field."""
        return permissions.main_role_by_article(self.article.articleworkflow, self.request.user)

    def get_initial(self):
        """Populate the hidden fields.

        Some of these (actor, content_type, object_id, message_type) will be overriden in the form's clean() method but
        we include the correct values here also for good practice.

        """
        default_subject = (
            f"Re: {self.source_message.subject}"
            if self.source_message
            else _(f"Message from {self.get_sender_label()}")
        )
        to_be_forwarded_to = self.get_to_be_forwarded_to()
        return {
            "actor": self.request.user.pk,
            "recipient": self.recipient.pk if self.recipient else None,
            "content_type": ContentType.objects.get_for_model(self.article).pk,
            "object_id": self.article.pk,
            "message_type": Message.MessageTypes.USER,
            "recipients": self.get_default_recipients(),
            "subject": default_subject,
            "to_be_forwarded_to": to_be_forwarded_to,
        }

    def get_success_url(self):
        """Point back to the article's detail page."""
        return reverse("wjs_article_details", kwargs={"pk": self.workflow.pk})

    def get_context_data(self, **kwargs):
        """Add the article and the recipient to the context."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        context["article"] = self.article
        context["recipient"] = self.recipient
        context["message_list"] = self.messages
        context["note"] = self.note
        context["hide_recipients"] = self.note or self.to_author or self.to_typesetter
        return context

    def form_valid(self, form):
        """If the form is valid, save the message and return a response."""
        response = super().form_valid(form)
        if self.note:
            messages.success(self.request, _("Note saved."))
        else:
            messages.success(self.request, _("The message has been sent."))
        return response


class ToggleMessageReadView(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """A view to let the user toggle read/unread flag on a message."""

    model = MessageRecipients
    form_class = ToggleMessageReadForm
    template_name = "wjs_review/article_messages/elements/toggle_message_read.html"
    context_object_name = "message"

    def test_func(self):
        """User must be the recipient."""
        return self.request.user.pk == self.kwargs["recipient_id"]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["prefix"] = f"toggle-{self.object.pk}"
        return kwargs

    def get_object(self, queryset=None):
        """Return the object the view is displaying.

        Since we are looking at a through table of m2m relationship, we can get the instance using the message id and
        recipient id.

        If this is not overridden, we get:
        AttributeError: Generic detail view ToggleMessageReadView must be called with either an object pk or a slug in
        the URLconf.

        """
        return get_object_or_404(
            MessageRecipients,
            message_id=self.kwargs["message_id"],
            recipient_id=self.kwargs["recipient_id"],
        )

    def form_valid(self, form):
        """If the form is valid, save the associate model (the flag on the MessageRecipient).

        Then, just return a response with the flag template rendered. I.e. do not redirect anywhere.

        """
        self.object = form.save()
        return self.render_to_response(self.get_context_data(form=form, message=self.object.message))


class ToggleMessageReadByEOView(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """A view to let the EO toggle read/unread flag on a message by other two actors."""

    model = Message
    form_class = ToggleMessageReadByEOForm
    template_name = "wjs_review/article_messages/elements/toggle_message_read_by_eo.html"
    context_object_name = "message"

    def test_func(self):
        """User must be part of the EO."""
        return base_permissions.has_eo_role(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["prefix"] = f"toggle-eo-{self.object.pk}"
        return kwargs

    def get_object(self, queryset=None):
        return get_object_or_404(
            Message,
            pk=self.kwargs["message_id"],
        )

    def form_valid(self, form):
        """If the form is valid, save the associate model (the flag on the Message read_by_eo).

        Then, just return a response with the flag template rendered. I.e. do not redirect anywhere.
        """
        self.object = form.save()
        return self.render_to_response(self.get_context_data(form=form, message=self.object))


class UploadRevisionAuthorCoverLetterFile(AuthenticatedUserPassesTest, UpdateView):
    """
    Basic view to upload the optional file of the author cover letter.

    We keep the author's cover letter file in wjs EditorRevisionRequest model instead of in Janeway's RevisionRequest
    model (where the covering letter/authors note text field is saved) in order to keep the plugin pluggable
    (i.e. Janeway can still work well without wjs_review).

    Also, since it's the author's direct reply to the editor's revision request, this is not semantically wrong.

    """

    model = EditorRevisionRequest
    pk_url_kwarg = "revision_id"
    template_name = "wjs_review/revision/upload_revision_author_cover_letter_file.html"
    form_class = UploadRevisionAuthorCoverLetterFileForm

    def test_func(self):
        """User must be corresponding author of the article."""
        return self.model.objects.filter(
            pk=self.kwargs[self.pk_url_kwarg],
            article__correspondence_author=self.request.user,
        ).exists()

    def get_success_url(self):
        """Redirect to the article details page."""
        return reverse("do_revisions", kwargs={"article_id": self.object.article.pk, "revision_id": self.object.pk})


class ArticleRevisionUpdate(BaseRelatedViewsMixin, UpdateView):
    title = _("Submit Revision")
    model = EditorRevisionRequest
    form_class = EditorRevisionRequestEditForm
    pk_url_kwarg = "revision_id"
    template_name = "wjs_review/revision/revision_form.html"
    context_object_name = "revision_request"
    meta_data_fields = ["title", "abstract"]

    def load_initial(self, request, *args, **kwargs):
        """Store a reference to the article for easier processing."""
        super().load_initial(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, pk=self.kwargs[self.pk_url_kwarg])

    def test_func(self):
        """User must be corresponding author of the article."""
        return self.model.objects.filter(
            pk=self.kwargs[self.pk_url_kwarg],
            article__correspondence_author=self.request.user,
        ).exists()

    @property
    def page_title(self):
        return f"{self.title} for {self.object.article.title}"

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}),
                title=self.object.article.articleworkflow,
            ),
            BreadcrumbItem(
                url=reverse(
                    "do_revisions",
                    kwargs={"article_id": self.object.article.articleworkflow.pk, "revision_id": self.object.pk},
                ),
                title=self.title,
                current=True,
            ),
        ]

    def _get_reviews(self) -> QuerySet[WorkflowReviewAssignment]:
        return WorkflowReviewAssignment.objects.filter(
            article=self.object.article,
            is_complete=True,
            for_author_consumption=True,
        ).not_withdrawn()

    def _get_revisions(self) -> QuerySet[EditorRevisionRequest]:
        return EditorRevisionRequest.objects.filter(
            article=self.object.article,
        ).order_by("-review_round__round_number")

    def get_form_kwargs(self) -> Dict[str, Any]:
        save_metadata = bool(self.request.POST.get("save_metadata"))
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        # when saving metadata form main view form must not be instatiated as submitted
        # so we remove data / files to skip form instatiation and validation
        if save_metadata:
            del kwargs["data"]
            del kwargs["files"]
        return kwargs

    def _get_metadata_form_class(self) -> Type[model_forms.BaseModelForm]:
        """Generate a MetadataForm class for the article."""
        return model_forms.modelform_factory(Article, fields=self.meta_data_fields)

    def _get_metadata_form(self) -> Optional[model_forms.BaseModelForm]:
        """
        Return the MetadataForm instance for the article.

        Form might be None if the article is not in a state where metadata can be edited.
        """
        form_class = self._get_metadata_form_class()

        if self.request.POST.get("save_metadata"):
            meta_data_form = form_class(self.request.POST, instance=self.object.article)
            meta_data_form.is_valid()
            return meta_data_form
        else:
            return form_class(instance=self.object.article)

    def form_valid(self, form):
        """
        Executed when either EditorRevisionRequestEditForm or MetadataForm is valid.

        Depending on the form and the submit button, different actions are taken:
        - if the submit button is "confirmed", it means the user has confirmed the revision, the control is passed to
          ```AuthorHandleRevision``` logic class to complete the revision submission process and redirect to article
          status page;
        - if the submit button is "save_metadata", it means the user has updated the metadata, we can just save the
          form, update aricle object associated with the revision request and redirect back to revision request page;
        - in all the other cases we just save the form and redirect back to revision request page.
        """
        if self.request.POST.get(self.form_class.CONFIRMED_BUTTON_NAME):
            self.object = form.finish()
            return HttpResponseRedirect(self.get_success_url())
        meta_data_form = self._get_metadata_form()
        if meta_data_form and meta_data_form.is_valid():
            self.object.article = meta_data_form.save()
        self.object = form.save()
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        """
        Redirect to the article details page if the revision confirmation is submitted or to the revision request page.
        """
        if self.request.POST.get(self.form_class.CONFIRMED_BUTTON_NAME):
            return reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk})
        else:
            return reverse(
                "do_revisions",
                kwargs={"article_id": self.object.article.pk, "revision_id": self.object.pk},
            )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["article"] = self.object.article
        context["reviews"] = self._get_reviews()
        context["revisions"] = self._get_revisions()
        context["meta_data_form"] = self._get_metadata_form()
        return context


class ArticleRevisionFileUpdate(AuthenticatedUserPassesTest, View):
    model = EditorRevisionRequest
    pk_url_kwarg = "revision_id"
    context_object_name = "revision_request"

    def test_func(self):
        """User must be corresponding author of the article."""
        return self.model.objects.filter(
            pk=self.kwargs[self.pk_url_kwarg],
            article__correspondence_author=self.request.user,
        ).exists()

    def load_initial(self, request, *args, **kwargs):
        """Store a reference to the revision request for easier processing."""
        super().load_initial(request, *args, **kwargs)
        self.object = get_object_or_404(EditorRevisionRequest, pk=self.kwargs[self.pk_url_kwarg])

    def get(self, *args, **kwargs):
        """Use files from some previous version of the paper.

        We retrieve the files (of a certain type: manuscript, supplementary,...)
        from the selected version (technically an EditorRevisionRequest linked to a certain review round),
        and set them as the Article.TYPE_files.
        """
        src_file_attr = getattr(self.object, f'{self.kwargs["file_type"]}_files')
        dst_file_attr = getattr(self.object.article, f'{self.kwargs["file_type"]}_files')
        dst_file_attr.set(src_file_attr.all())
        messages.success(self.request, "Files replaced.")
        return HttpResponseRedirect(
            reverse("do_revisions", kwargs={"article_id": self.object.article.pk, "revision_id": self.object.pk}),
        )


class ArticleReminders(HtmxMixin, BaseRelatedViewsMixin, FilterView):
    """All reminders related to an article."""

    title = _("Scheduled reminders")
    model = Reminder
    template_name = "wjs_review/reminders/article_reminders.html"
    context_object_name = "reminders"
    filterset_class = ReminderFilter

    def load_initial(self, request, *args, **kwargs):
        """Store a reference to the article for easier processing."""
        super().load_initial(request, *args, **kwargs)
        self.workflow = get_object_or_404(ArticleWorkflow, pk=self.kwargs["pk"])

    def test_func(self):
        """Let's show reminders only to EO or staff."""
        return base_permissions.has_admin_role(self.request.journal, self.request.user)

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.workflow.pk}),
                title=self.workflow,
            ),
            BreadcrumbItem(
                url=self.request.path,
                title=self.title,
                current=True,
            ),
        ]

    def get_template_names(self):
        if self.htmx:
            return ["wjs_review/reminders/elements/reminders_list.html"]
        return super().get_template_names()

    def get_queryset(self):
        """Get reminders related to an article via ReviewAssignment or WjsEditorAssignment or similar."""
        qs = super().get_queryset()
        review_assignments = WorkflowReviewAssignment.objects.filter(article=self.workflow.article).values_list("pk")
        reviewer_reminders = Q(
            content_type=ContentType.objects.get_for_model(WorkflowReviewAssignment),
            object_id__in=review_assignments,
        )
        editor_assignments = WjsEditorAssignment.objects.filter(article=self.workflow.article).values_list("pk")
        editor_reminders = Q(
            content_type=ContentType.objects.get_for_model(WjsEditorAssignment),
            object_id__in=editor_assignments,
        )
        result = qs.filter(editor_reminders | reviewer_reminders)
        return result.order_by("-date_due")

    def get_context_data(self, **kwargs):
        """Add the article to the context."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        context["article"] = self.workflow.article
        return context


class UpdateReviewerDueDate(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """
    View to allow the Editor to postpone Reviewer Report due date.
    """

    model = ReviewAssignment
    form_class = UpdateReviewerDueDateForm
    template_name = "wjs_review/details/update_reviewer_due_date.html"
    context_object_name = "assignment"
    reviewer = False

    def load_initial(self, request, *args, **kwargs):
        """Fetch the ReviewAssignment instance for easier processing."""
        super().load_initial(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, pk=self.kwargs[self.pk_url_kwarg])

    def test_func(self):
        """User must be the article's editor"""
        articleworkflow = self.object.article.articleworkflow
        if self.object.is_complete:
            raise Http404(_("This review has already been completed."))
        if self.reviewer:
            return permissions.is_article_reviewer(articleworkflow, self.request.user)
        return permissions.is_article_editor(articleworkflow, self.request.user) or base_permissions.has_eo_role(
            self.request.user
        )

    @property
    def title(self):
        if self.reviewer:
            return _("Postpone Due Date")
        return _("Postpone report due date")

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        super().form_valid(form)
        messages.success(self.request, _("Due date updated successfully."))
        response = HttpResponse("ok")
        response.headers["HX-Redirect"] = self.get_success_url()
        return response

    def get_success_url(self):
        """Point back to the article's detail page."""
        return reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk})


class EditorDeclineAssignmentView(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    template_name = "wjs_review/details/editor_rejects_assignment.html"
    form_class = EditorDeclinesAssignmentForm
    model = ArticleWorkflow

    def load_initial(self, request, *args, **kwargs):
        """Fetch the ArticleWorkflow instance for easier processing."""
        super().load_initial(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, pk=self.kwargs["pk"])

    def test_func(self):
        """User must be the article's Editor and must be assigned to the article."""
        return permissions.is_article_editor(self.object, self.request.user)

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.object
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        """
        Delete declined WjsEditorAssignment using :py:class:`HandleEditorDeclinesAssignment`.

        If the service raises a ValidationError, the error is passed to the template.
        If the action is successful, a success message is attached and the user is redirected to the article list page.
        """
        try:
            super().form_valid(form)
            messages.success(self.request, _("Assignment declined successfully."))
            response = HttpResponse("ok")
            response.headers["HX-Redirect"] = reverse("wjs_review_list")
            return response
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class DeselectReviewer(BaseRelatedViewsMixin, UpdateView):
    """
    The editor can withdraw a pending review assignment
    """

    title = _("Deselect Reviewer")
    model = WorkflowReviewAssignment
    form_class = DeselectReviewerForm
    template_name = "wjs_review/details/deselect_reviewer.html"
    context_object_name = "assignment"

    def test_func(self):
        """
        The user must be the article's editor.
        """
        return permissions.is_article_editor_or_eo(self.get_object().article.articleworkflow, self.request.user)

    def get_success_url(self):
        messages.add_message(
            self.request,
            messages.SUCCESS,
            _("Reviewer deassigned successfully."),
        )
        return reverse("wjs_article_details", args=(self.object.article.articleworkflow.pk,))

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk}),
                title=self.object.article.articleworkflow,
            ),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["user"] = self.request.user
        return kwargs

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "editor": self.object.editor,
            "assignment": self.object,
            "article": self.object.article,
        }

    def get_initial(self):
        initial = super().get_initial()
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="editor_deassign_reviewer_subject",
            journal=self.object.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="editor_deassign_reviewer_default",
            journal=self.object.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        initial["notification_subject"] = message_subject
        initial["notification_body"] = message_body
        return initial


class SupervisorAssignEditor(BaseRelatedViewsMixin, UpdateView):
    """
    If the user is an editor of a special issue, they will be able to assign the paper to a different editor
    """

    model = ArticleWorkflow
    form_class = SupervisorAssignEditorForm
    template_name = "wjs_review/assign_editor/select_editor.html"
    title = _("Select an Editor")
    context_object_name = "workflow"

    def test_func(self):
        """
        The user must be the article's editor or the director or a member of the EO.

        This view can be used for the assignment of different editors in a Special Issue,
        but we don't check if the editor belongs to a S.I. (e.g. `permissions.can_assign_special_issue_by_article()`),
        because the process is common.
        """
        return permissions.is_article_supervisor(self.get_object(), self.request.user)

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}), title=self.object),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def get_success_url(self):
        messages.add_message(
            self.request,
            messages.SUCCESS,
            _("Editor assigned successfully."),
        )
        return reverse("wjs_article_details", args=(self.object.pk,))

    def _get_current_editor(self) -> Account | None:
        """Get the current editor of the article."""
        try:
            return WjsEditorAssignment.objects.get_current(self.object).editor
        except WjsEditorAssignment.DoesNotExist:
            return None

    def _editors_with_keywords(self) -> QuerySet[Account]:
        """
        Provides a list of available editors annotated with related keywords.

        The list is filtered by removing current editor, if any.
        """
        article_authors = self.object.article.authors.all()
        try:
            current_editor = WjsEditorAssignment.objects.get_current(self.object).editor
        except WjsEditorAssignment.DoesNotExist:
            current_editor = None
        return Account.objects.get_editors_with_keywords(self.object.article, current_editor).exclude(
            pk__in=article_authors
        )

    def get_context_data(self, **kwargs) -> Context:
        context = super().get_context_data(**kwargs)
        context["editors_with_keywords"] = self._editors_with_keywords()
        context["current_editor"] = self._get_current_editor()
        return context

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["instance"] = self.object
        kwargs["selectable_editors"] = self._editors_with_keywords()
        return kwargs


class JournalEditorsView(BaseRelatedViewsMixin, ListView):

    title = _("Journal Editors")
    model = Account
    template_name = "wjs_review/journal_editors/editor_list.html"
    context_object_name = "editor_list"

    def test_func(self):
        """Allow access only to EO and directors."""
        user = self.request.user
        journal = self.request.journal
        return base_permissions.has_eo_or_director_role(journal=journal, user=user)

    def get_queryset(self):
        qs = Account.objects.filter(
            accountrole__journal=self.request.journal,
            accountrole__role__slug__in=(constants.EDITOR_ROLE, constants.SECTION_EDITOR_ROLE),
        )
        return qs


class ForwardMessage(BaseRelatedViewsMixin, CreateView):
    """Forward a Message.

    See ForwardMessageForm for details on the forwarded message.
    """

    model = Message
    template_name = "wjs_review/write_message/write_messages.html"
    form_class = ForwardMessageForm
    pk_url_kwarg = "original_message_pk"

    def test_func(self):
        """Allow access only to EO (or staff)."""
        return base_permissions.has_admin_role(self.request.journal, self.request.user)

    def load_initial(self, request, *args, **kwargs):
        """Fetch the original message that we are going to forward."""
        super().load_initial(request, *args, **kwargs)
        self.original_message = self.get_object()
        self.workflow = self.original_message.target.articleworkflow

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.workflow.pk}),
                title=str(self.workflow),
            ),
            BreadcrumbItem(
                url=reverse("wjs_article_messages", kwargs={"pk": self.workflow.pk}),
                title=_("Messages"),
            ),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    @property
    def title(self):
        return _('Forward message "%s"') % self.original_message.subject

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["original_message"] = self.original_message
        kwargs["actor"] = self.original_message.actor
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        return {
            "subject": self.original_message.subject,
            "body": self.original_message.body,
        }

    def get_context_data(self, **kwargs):
        """Add the workflow."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        context["introduction"] = _("Please check this message before forwarding it.")
        context["forward"] = self.original_message.to_be_forwarded_to
        context["hide_recipients"] = True
        return context

    def get_success_url(self):
        """Point back to the paper's status page."""
        return reverse("wjs_article_details", kwargs={"pk": self.workflow.pk})


class DownloadAnythingDROPME(View):
    """DROPME! UNSAFE!"""

    def get(self, request, *args, **kwargs):
        """Serve any File."""
        attachment = core_models.File.objects.get(pk=self.kwargs["file_id"])
        article = Article.objects.get(pk=self.kwargs["article_id"])
        return core_files.serve_file(request, attachment, article, public=True)


class ArticleExtraInformationUpdateView(BaseRelatedViewsMixin, UpdateView):
    title = _("Update Article Information")
    model = ArticleWorkflow
    template_name = "wjs_review/details/articleworkflow_form.html"
    form_class = ArticleExtraInformationUpdateForm

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}), title=self.object),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def test_func(self):
        articleworkflow = self.get_object()
        return permissions.is_article_author(articleworkflow, self.request.user) or permissions.has_eo_role_by_article(
            articleworkflow, self.request.user
        )


class AdminOpensAppealView(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """A view to move a paper to under appeal state.

    This passage can only be triggered by the EO.
    """

    title = _("Open Appeal")
    model = ArticleWorkflow
    form_class = OpenAppealForm
    template_name = "wjs_review/details/eo_select_editor.html"
    context_object_name = "workflow"

    def load_initial(self, request, *args, **kwargs):
        super().load_initial(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])

    def test_func(self):
        """Allow access only to EO (or staff)."""
        return base_permissions.has_admin_role(self.request.journal, self.request.user)

    def get_success_url(self):
        """Point back to the paper's status page."""
        return reverse("wjs_article_details", kwargs={"pk": self.object.pk})

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["instance"] = self.object
        return kwargs

    def form_valid(self, form):
        """If the form is valid, save the message and return a response."""
        super().form_valid(form)
        response = HttpResponse("ok")
        response.headers["HX-Redirect"] = self.get_success_url()
        messages.success(self.request, _("The paper has been moved to under appeal state."))
        return response


class AuthorWithdrawPreprint(BaseRelatedViewsMixin, UpdateView):
    """View for author to withdraw preprint."""

    title = _("Withdraw Preprint")
    model = ArticleWorkflow
    form_class = WithdrawPreprintForm
    success_url = reverse_lazy("wjs_review_author_archived")
    template_name = "wjs_review/details/withdraw_preprint.html"
    context_object_name = "workflow"

    def test_func(self):
        """User must be corresponding author of the article."""
        return self.model.objects.filter(
            pk=self.kwargs["pk"],
            article__correspondence_author=self.request.user,
        ).exists()

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}), title=self.object),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.object
        kwargs["request"] = self.request
        return kwargs

    def _get_message_context(self):
        """Get the context for the message template."""
        current_editor = WjsEditorAssignment.objects.get_current(self.object.article).editor
        return {
            "supervisor": current_editor if current_editor is not None else get_eo_user(self.object.article),
            "article": self.object.article,
        }

    def get_initial(self):
        initial = super().get_initial()
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="author_withdraws_preprint_subject",
            journal=self.object.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="author_withdraws_preprint_body",
            journal=self.object.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        initial["notification_subject"] = message_subject
        initial["notification_body"] = message_body
        return initial


class ToggleIssueBatch(HtmxMixin, AuthenticatedUserPassesTest, DetailView):
    """A view to toggle the issue batch state."""

    model = Issue
    template_name = "wjs_review/lists/elements/issue/_toggle_batch.html"
    context_object_name = "issue"
    fields = ["batch_publish"]

    def test_func(self):
        """User must be the journal's EO."""
        return base_permissions.has_eo_role(self.request.user)

    def post(self, request, *args, **kwargs):
        """Toggle value of IssueParameters.batch_publish."""
        self.object = self.get_object()
        self.object.issueparameters.batch_publish = not self.object.issueparameters.batch_publish
        self.object.issueparameters.save()
        return self.get(request, *args, **kwargs)
