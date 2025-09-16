import datetime
from itertools import chain
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type, Union

import django_filters
from core import files as core_files
from core import models as core_models
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.paginator import InvalidPage, Page, Paginator
from django.db.models import F, Q, QuerySet
from django.db.models.functions import Coalesce
from django.forms import models as model_forms
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
    QueryDict,
)
from django.shortcuts import get_object_or_404
from django.template import Context
from django.urls import resolve, reverse, reverse_lazy
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    RedirectView,
    TemplateView,
    UpdateView,
    View,
)
from django_filters.views import FilterMixin, FilterView
from events import logic as event_logic
from journal.logic import get_all_tables_from_html
from journal.models import Issue, Journal
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from review import logic as review_logic
from submission.models import Article, FrozenAuthor
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile import constants
from wjs.jcom_profile import permissions as base_permissions
from wjs.jcom_profile.constants import role_label
from wjs.jcom_profile.mixins import HtmxMixin
from wjs.jcom_profile.models import IssueParameters, StaffWorkloadParameters
from wjs.jcom_profile.utils import get_eo_user

from . import permissions
from .communication_utils import get_messages_related_to_me, group_messages_by_version
from .filters import (
    AuthorArticleWorkflowFilter,
    EOArticleWorkflowFilter,
    MessageFilter,
    MessagesOverviewFilter,
    ReminderFilter,
    ReviewerArticleWorkflowFilter,
    StaffArticleWorkflowFilter,
    WorkOnAPaperArticleWorkflowFilter,
)
from .forms import (
    ArticleExtraInformationUpdateForm,
    AssignEoForm,
    ConfirmVersionForm,
    DecisionForm,
    DeclineReviewForm,
    DeselectReviewerForm,
    EditMetadataForm,
    EditorDeclinesAssignmentForm,
    EditorRevisionRequestDueDateForm,
    EditorRevisionRequestEditForm,
    EditorRevisionRequestForm,
    EvaluateReviewForm,
    ForwardMessageForm,
    InviteUserForm,
    MessageForm,
    OpenAppealForm,
    ReviewerSearchForm,
    SelectReviewerForm,
    SupervisorAssignEditorForm,
    TimelineFilterForm,
    ToggleDisableRemindersForm,
    ToggleMessageReadByEOForm,
    ToggleMessageReadForm,
    UpdateReviewerDueDateForm,
    UploadArticleForm,
    WithdrawPreprintForm,
)
from .logic import (
    AdminActions,
    HandleMessage,
    render_template_from_setting,
    states_when_article_is_considered_archived,
    states_when_article_is_considered_archived_with_under_appeal,
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
    PaginatedViewMixin,
    ReviewerRequiredMixin,
)
from .models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    MessageRecipients,
    PermissionAssignment,
    Reminder,
    TypesettingVersion,
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
            "wjs_review_eo_production": _("Production"),
            "wjs_review_eo_archived": _("Archived preprints"),
            "wjs_review_eo_issues_list": _("Pending Issues"),
            "wjs_review_eo_workon": _("Search preprints"),
            "wjs_messages_overview": _("Activity"),
        },
        constants.DIRECTOR_ROLE: {
            "wjs_review_director_pending": _("Pending preprints"),
            "wjs_review_director_production": _("Production"),
            "wjs_review_director_archived": _("Archived preprints"),
            "wjs_review_director_issues_list": _("Pending Issues"),
            "wjs_review_director_workon": _("Search preprints"),
            "wjs_messages_overview": _("Activity"),
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
            "wjs_review_typesetter_archived": _("Archived preprints"),
            "wjs_review_typesetter_workingon": _("Working on"),
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
        return role_label(self.role)


class ArticleWorkflowBaseMixin(BaseRelatedViewsMixin, PaginatedViewMixin, ListView):
    model = ArticleWorkflow
    filterset_class = None
    filterset: Optional[django_filters.FilterSet]
    context_object_name = "workflows"
    ordering = ["-modified"]
    title: str
    show_filters = True
    configuration_options: Dict[str, Any] = {}
    paginate_by = 50

    def load_initial(self, request, *args, **kwargs):
        """Setup and validate filterset data."""
        super().load_initial(request, *args, **kwargs)
        data = self.request.GET.copy()
        if base_permissions.has_eo_role(request.user):
            data["eo_in_charge"] = request.user
        if getattr(self, "filterset_class", None):
            self.filterset = self.filterset_class(
                data=self.request.GET,
                queryset=self._apply_base_filters(self.model.objects.all()),
                request=self.request,
                journal=self.request.journal,
                configuration_options=self.configuration_options,
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
    configuration_options = {
        "show_filter_editor": False,
        "show_filter_reviewer": True,
        "table_type": "review",
        "is_pending": True,
    }
    """See :py:attr:`EOPending.configuration_options` for details."""

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
    paginate_by = 20

    def _apply_base_filters(self, qs):
        """
        Keep only articles (workflows) for which the user is editor and a "final" decision has been made.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        past = states_when_article_is_considered_archived + states_when_article_is_considered_in_production
        state_under_appeal = Q(state__in=[ArticleWorkflow.ReviewStates.UNDER_APPEAL])
        state_past = Q(state__in=past) & Q(
            article__editorassignment__editor__in=[self.request.user],
        )
        # It can happen that an Editor is de-assigned and then reassigned again. Without the following line the article
        # will appear both in Pending and Archived listings.
        past_assignment = Q(article__past_editor_assignments__editor__in=[self.request.user])
        active_assignment = Q(article__editorassignment__editor__in=[self.request.user])
        active_assignment_in_review = active_assignment & Q(state__in=states_when_article_is_considered_in_review)
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            state_past | (past_assignment & ~active_assignment_in_review) | (active_assignment & state_under_appeal)
        )


class EOPending(ArticleWorkflowBaseMixin):
    """EO's main page."""

    title = _("Pending preprints")
    role = constants.EO_GROUP
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/eo/table.html"
    filterset_class = EOArticleWorkflowFilter
    filterset: EOArticleWorkflowFilter
    ordering = ["-article__date_submitted"]
    configuration_options = {
        "show_filter_editor": True,
        "show_filter_reviewer": True,
        "table_type": "review",
        "is_pending": True,
    }
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
    prefilter_by_eo = True

    def test_func(self):
        """Allow access only to EO (or staff)."""
        return base_permissions.has_admin_role(self.request.journal, self.request.user)

    def load_initial(self, request, *args, **kwargs):
        """Setup and validate filterset data."""
        data = self.request.GET.copy()
        if (
            base_permissions.has_eo_role(request.user)
            and not self.request.GET.get("search")
            and self.prefilter_by_eo
            and StaffWorkloadParameters.objects.filter(journal=request.journal, user=request.user, workload__gt=0)
        ):
            data["eo_in_charge"] = request.user
        self.request.GET = data
        super().load_initial(request, *args, **kwargs)

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
    configuration_options = {
        "hide_editor_age": True,
        "show_filter_editor": True,
        "show_filter_reviewer": True,
        "table_type": "archived",
    }
    paginate_by = 20
    prefilter_by_eo = False

    def _apply_base_filters(self, qs):
        """
        Keep only articles (workflows) for which a "final" decision has been made.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return (
            ArticleWorkflowBaseMixin._apply_base_filters(self, qs)
            .filter(
                state__in=states_when_article_is_considered_archived,
            )
            .annotate(
                sort_date=Coalesce(
                    F("article__date_published"),
                    F("article__date_declined"),
                    F("latest_state_change"),
                )
            )
            .order_by("-sort_date")
        )


class EOProduction(EOPending):
    title = _("Papers in production")
    configuration_options = {"show_filter_typesetter": True, "table_type": "production"}
    ordering = ["-article__date_accepted"]
    prefilter_by_eo = False

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
    prefilter_by_eo = False

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


class IssueParametersUpdateView(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """View to allow EO to modify our custom issue parameters."""

    model = IssueParameters
    fields = ["latex_fragment", "batch_publish"]
    template_name = "wjs_review/lists/elements/issue/_issue_parameters_modal.html"
    success_url = reverse_lazy("wjs_review_eo_issues_list")
    context_object_name = "issueparameters"

    def test_func(self):
        """Allow access only to EO."""
        return base_permissions.has_admin_role(self.request.journal, self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["latex_fragment"].widget = forms.Textarea(attrs={"cols": 80, "rows": 5})
        return form

    def form_valid(self, form):
        super().form_valid(form)
        messages.success(
            self.request,
            mark_safe(
                _("Issue parameters correctly set for ") + self.get_object().issue.update_display_title(save=False)
            ),
        )
        response = HttpResponse("ok")
        response["HX-Redirect"] = self.success_url
        return response


class DirectorPending(ArticleWorkflowBaseMixin):
    """Director's main page."""

    title = _("Pending preprints")
    role = constants.DIRECTOR_ROLE
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/director/table.html"
    filterset_class = StaffArticleWorkflowFilter
    filterset: StaffArticleWorkflowFilter
    configuration_options = {
        "show_filter_editor": True,
        "show_filter_reviewer": True,
        "table_type": "review",
        "is_pending": True,
    }
    """See :py:attr:`EOPending.configuration_options` for details."""

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
    configuration_options = {
        **DirectorPending.configuration_options,
        "hide_editor_age": True,
        "table_type": "archived",
    }
    paginate_by = 20

    def _apply_base_filters(self, qs):
        """
        Get all articles in final states except the ones where the director is also the author.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return (
            (
                ArticleWorkflowBaseMixin._apply_base_filters(self, qs)
                .filter(state__in=states_when_article_is_considered_archived)
                .exclude(article__authors=self.request.user)
            )
            .annotate(
                sort_date=Coalesce(
                    F("article__date_published"),
                    F("article__date_declined"),
                    F("latest_state_change"),
                )
            )
            .order_by("-sort_date")
        )


class DirectorProduction(DirectorPending):
    title = _("Papers in production")
    configuration_options = {"show_filter_typesetter": True, "table_type": "production"}
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
    configuration_options = {}
    """See :py:attr:`EOPending.configuration_options` for details."""

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
    configuration_options = {"show_author_due_date": True, "show_filter_author": True}
    """See :py:attr:`EOPending.configuration_options` for details."""
    paginate_by = 20

    def _apply_base_filters(self, qs):
        """
        Get all articles in final states where the user is the author.

        Method uses explicitly FilterSetMixin.get_queryset because the mro is a bit complicated and we want to make
        sure to use the original method.
        """
        return ArticleWorkflowBaseMixin._apply_base_filters(self, qs).filter(
            Q(state__in=states_when_article_is_considered_archived)
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
    configuration_options = {
        "reviewer_status": True,
        "show_filter_editor": True,
        "show_filter_author": False,
        "pending_list": True,
        "archived_list": False,
    }
    """See :py:attr:`EOPending.configuration_options` for details."""

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
    configuration_options = {
        "reviewer_status": True,
        "show_filter_editor": True,
        "show_filter_author": False,
        "pending_list": False,
        "archived_list": True,
    }
    """See :py:attr:`EOPending.configuration_options` for details."""
    paginate_by = 20

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


class SelectReviewerView(
    BaseRelatedViewsMixin, HtmxMixin, ArticleAssignedEditorMixin, EditorRequiredMixin, PaginatedViewMixin, UpdateView
):
    """Select user as reviewer.

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
        if self.htmx and not self.request.headers.get("Hx-Trigger") == "select-reviewer-form":
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
            if self.request.headers.get("Hx-Trigger-Name") == "search-reviewer-form":
                return ["wjs_review/select_reviewer/elements/reviewers_table.html"]
            elif self.request.headers.get("Hx-Trigger") in ["preview-btn-select", "update-btn-select"]:
                return ["wjs_review/select_reviewer/elements/select_reviewer_message_preview.html"]
            else:
                # we return this template both when the Select button is pressed in the reviewers list and when the
                # form is submitted with an error
                return ["wjs_review/select_reviewer/elements/select_reviewer_form.html"]
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
        context.update(
            {
                "paginator": paginator,
                "page_obj": page,
                "is_paginated": is_paginated,
                "object_list": objects_list,
                "reviewers": objects_list,
                "eo_user": get_eo_user(self.object.article),
            }
        )
        context["reviewer"] = context["form"].data.get("reviewer")
        if context["form"].data.get("reviewer"):
            context["preview"] = self._render_message_preview(form=context["form"])

        review_versions = self.object.get_review_versions(self.request.user)
        context["review_version"] = review_versions[0]
        return context

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        initial["author_note_visible"] = False if self.object.article.current_review_round() == 1 else True
        return initial

    def get_search_form(self) -> ReviewerSearchForm:
        return ReviewerSearchForm(self.search_data if self.search_data else None)

    def form_valid(self, form: SelectReviewerForm) -> HttpResponse:
        """
        Executed when SelectReviewerForm is valid

        Even if the form is valid, checks in logic.AssignToReviewer -called by form.save- may fail as well.
        """
        try:
            super().form_valid(form)
            messages.success(self.request, _("The reviewer has been succesfully selected."))
            response = HttpResponse("ok")
            response["HX-Redirect"] = self.get_success_url()
            return response
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class InviteReviewerView(HtmxMixin, ArticleAssignedEditorMixin, EditorRequiredMixin, UpdateView):
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
        review_versions = self.object.get_review_versions(self.request.user)
        context["review_version"] = review_versions[0]
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
            super().form_valid(form)
            messages.success(self.request, _("The reviewer has been succesfully selected."))
            response = HttpResponse("ok")
            response["HX-Redirect"] = self.get_success_url()
            return response
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)

    def post(self, request, *args, **kwargs) -> HttpResponse:
        """
        Handle POST requests: instantiate a form instance with the passed POST variables and then check if it's valid.

        If we have been called via htmx, it means we are just displaying the form in the modal.
        """
        if self.htmx and not self.request.headers.get("Hx-Trigger") == "new-reviewer-invite-form":
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)


class ArticleIdToDetails(RedirectView):
    """Utility redirect from article-id to WJS status page."""

    permanent = False
    query_string = True

    def get_redirect_url(self, *args, **kwargs):
        """Given the article-id, redirect to the WJS status page."""
        try:
            article = Article.objects.get(pk=kwargs["article_id"])
            return reverse("wjs_article_details", kwargs={"pk": article.articleworkflow.pk})
        except Article.DoesNotExist:
            try:
                articleworkflow = ArticleWorkflow.objects.get(pk=kwargs["article_id"])
                return reverse("wjs_article_details", kwargs={"pk": articleworkflow.pk})
            except ArticleWorkflow.DoesNotExist:
                raise Http404(_("Article with id {article_id} does not exist").format(article_id=kwargs["article_id"]))


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

    def get_current_review_assignment(self) -> Optional[WorkflowReviewAssignment]:
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

    def pending_proofs_version(self, production_versions: List[TypesettingVersion]) -> Union[TypesettingVersion, bool]:
        """
        If the author uploaded proofs for the version before the current one, return the GalleyProofing.
        Otherwise, return False.
        """
        # From TypesettingVersion seems hard to retrieve the information if the author uploaded proofs for the previous
        # version and if he did, to get the TypesettingVersion object. This is because for the latest
        # TypesettingVersion, the GalleyProofing is always empty, since the action that fills It also creates
        # a new version.
        if permissions.is_article_typesetter(self.object, self.request.user):
            if len(production_versions) > 1:
                if production_versions[1].has_proofing_files:
                    return production_versions[1]
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = self.get_form(self.request.GET)
        messages = get_messages_related_to_me(self.request.user, self.object.article)
        messages = messages.exclude(verbosity=Message.MessageVerbosity.EMAIL)
        context["timeline_messages"] = group_messages_by_version(
            self.object.article, messages, filters=context["form"].cleaned_data
        )
        if self.object.state in (
            states_when_article_is_considered_in_review + states_when_article_is_considered_archived_with_under_appeal
        ):
            context["review"] = True
            context["current_review_assignment"] = self.get_current_review_assignment()
        if self.object.state in (
            states_when_article_is_considered_in_production
            + states_when_article_is_considered_archived_with_under_appeal
        ):
            production_versions = self.object.get_production_versions(self.request.user)
            context["production_versions"] = production_versions
            context["pending_proofs_version"] = self.pending_proofs_version(production_versions)
            context["production"] = True
            # During production we want to show review versions too (for authorized users)
            context["review"] = True
        context["review_versions"] = self.object.get_review_versions(self.request.user)
        # We explicitly set the article in the context because it is often used in templatetags
        # and, when the view answers to an HTMX request, the rendered templates might not define it
        # (as in `{% with article=workflow.article %}...`)
        context["article"] = context["workflow"].article
        return context


class ReviewerDeclineReview(HtmxMixin, OpenReviewMixin, UpdateView):

    title = _("Decline review")
    form_class = DeclineReviewForm
    template_name = "wjs_review/details/decline_review.html"
    pk_url_kwarg = "pk"

    def get_success_url(self) -> str:
        return reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk})

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


class EvaluateReviewRequest(BaseRelatedViewsMixin, OpenReviewMixin, UpdateView):
    form_class = EvaluateReviewForm
    template_name = "wjs_review/evaluate_review/review_evaluate.html"
    success_url = reverse_lazy("wjs_review_list")
    title = _("Accept/Decline invite to review")
    use_access_code = True
    allow_anonymous_access = True
    object = None  # noqa: A003 - Not set as default from Django base classes

    def load_initial(self, request, *args, **kwargs):
        if self.allow_anonymous_access and request.user.is_anonymous:
            self.extra_links = {}
        else:
            super().load_initial(request, *args, **kwargs)

    def test_func(self):
        """
        This is needed because the permission logic is inside OpenReviewMixin but we still need a test_func to use
        BaseRelatedViewsMixin.
        """
        return True

    def get(self, request, *args, **kwargs):
        if self._check_accepted_review_assignment():
            return HttpResponsePermanentRedirect(self.get_success_url())
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self._check_accepted_review_assignment():
            return HttpResponsePermanentRedirect(self.get_success_url())
        return super().post(request, *args, **kwargs)

    def _check_accepted_review_assignment(self):
        """
        Check if the review assignment has been already accepted or not.

        Check on declined assignments is not needed because they are filtered out in the get_queryset method.

        :return: RA accepted review assignment or not
        """
        if not self.object:
            self.object = self.get_object()
        if self.object.date_accepted:
            return True
        return False

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

    def get_queryset(self) -> QuerySet[WorkflowReviewAssignment]:
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
    allow_anonymous_access = True

    def load_initial(self, request, *args, **kwargs):
        if self.allow_anonymous_access and request.user.is_anonymous:
            self.extra_links = {}
        else:
            super().load_initial(request, *args, **kwargs)

    def test_func(self):
        """
        This is needed because the permission logic is inside OpenReviewMixin but we still need a test_func to use
        BaseRelatedViewsMixin.

        We also need the invited reviewer (AnonymousUser) to have access to this page.
        """
        return True

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
    use_access_code = True
    allow_anonymous_access = True
    allow_editor_access = True
    allow_typesetter_access = True

    def load_initial(self, request, *args, **kwargs):
        if self.allow_anonymous_access and request.user.is_anonymous:
            self.extra_links = {}
        else:
            super().load_initial(request, *args, **kwargs)

    def test_func(self):
        """
        This is needed because the permission logic is inside OpenReviewMixin but we still need a test_func to use
        BaseRelatedViewsMixin.

        We also need the invited reviewer (AnonymousUser) to have access to this page.
        """
        return True

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_fields"] = get_report_form(self.object.article.journal.code)().fields
        return context


class ReviewSubmit(EvaluateReviewRequest, ReviewerRequiredMixin):
    template_name = "wjs_review/submit_review/review_submit.html"
    title = _("Submit review")
    use_access_code = True

    def _check_accepted_review_assignment(self):
        """Skip the check for accepted review assignment as it's part of the :py:meth:`get_queryset` method call."""
        return False

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
    title = _("Make decision (EO)")

    def test_func(self):
        """Verify that only EO can access."""
        return base_permissions.has_eo_role(self.request.user)

    def get_success_url(self):
        return reverse("wjs_article_details", args=(self.object.id,))

    @property
    def current_reviews(self) -> QuerySet[WorkflowReviewAssignment]:
        """Return the reviews for the current review round for the article."""
        return WorkflowReviewAssignment.objects.filter(
            review_round=self.object.article.current_review_round_object(),
        )

    @property
    def submitted_reviews(self) -> QuerySet[WorkflowReviewAssignment]:
        """Return the submitted reviews for the current review round."""
        return self.current_reviews.filter(date_complete__isnull=False, date_accepted__isnull=False).exclude(
            decision="withdrawn"
        )

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
        context["submitted_reviews"] = self.submitted_reviews
        context["form_fields"] = get_report_form(self.object.article.journal.code)().fields
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

        If the editor has not made a decision (state is still EDITOR_SELECTED), redirect to the Editor decision page,
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
        today = timezone.now().date()
        # kwargs["data"] "wins" over self.request.GET because it's the form data sent by the user.
        # But kwargs["data"] is not always present, so I try to be as safe as possible. Hope this is not "unreadable"
        decision = kwargs.get("data", {}).get("decision", self.request.GET.get("decision", None))
        if decision:
            self.title = ArticleWorkflow.Decisions(decision).label
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["initial"] = {"decision": decision}
        if decision in (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.Decisions.MAJOR_REVISION):
            if decision == ArticleWorkflow.Decisions.MINOR_REVISION:
                days_setting_name = "default_author_minor_revision_days"
                days_max_setting_name = "default_author_minor_revision_days_max"
            else:
                days_setting_name = "default_author_major_revision_days"
                days_max_setting_name = "default_author_major_revision_days_max"
            revision_days = get_setting(
                setting_group_name="wjs_review",
                setting_name=days_setting_name,
                journal=self.object.article.journal,
            ).process_value()
            revision_days_max = get_setting(
                setting_group_name="wjs_review",
                setting_name=days_max_setting_name,
                journal=self.object.article.journal,
            ).process_value()
            date_due_initial = today + datetime.timedelta(days=revision_days)
            date_due_max = today + datetime.timedelta(days=revision_days_max)
            kwargs["initial"]["date_due"] = date_due_initial
            kwargs["date_due_max"] = date_due_max
            kwargs["revision_days_max"] = revision_days_max
        elif decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
            revision_days = get_setting(
                setting_group_name="wjs_review",
                setting_name="default_author_technical_revision_days",
                journal=self.object.article.journal,
            )
            kwargs["initial"]["date_due"] = today + datetime.timedelta(days=revision_days.processed_value)
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
    def current_reviews(self) -> QuerySet[WorkflowReviewAssignment]:
        """Return the reviews for the current review round for the article."""
        return WorkflowReviewAssignment.objects.filter(
            review_round=self.object.article.current_review_round_object(),
        )

    @property
    def submitted_reviews(self) -> QuerySet[WorkflowReviewAssignment]:
        """Return the submitted reviews for the current review round."""
        return self.current_reviews.filter(date_complete__isnull=False, date_accepted__isnull=False).exclude(
            decision="withdrawn"
        )

    @property
    def pending_reviews(self) -> QuerySet[WorkflowReviewAssignment]:
        """Return not completed reviews for the current review round."""
        return self.current_reviews.filter(
            date_complete__isnull=True,
            date_declined__isnull=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["submitted_reviews"] = self.submitted_reviews
        context["form_fields"] = get_report_form(self.object.article.journal.code)().fields
        context["pending_reviewers_list"] = ", ".join([review.reviewer.full_name() for review in self.pending_reviews])
        context["not_metadata_change"] = (
            self.request.GET.get("decision", None) != ArticleWorkflow.Decisions.TECHNICAL_REVISION
        )
        return context


class ArticleMessages(HtmxMixin, BaseRelatedViewsMixin, FilterView):
    """
    All messages of a certain user that are related to an article.
    """

    title = _("Messages and notes")
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
            BreadcrumbItem(url=self.request.path, title=_("Messages and notes"), current=True),
        ]

    def get_queryset(self):
        """Return the list of messages that the user is entitled to see for this article."""
        return get_messages_related_to_me(user=self.request.user, article=self.article)

    def get_filterset_kwargs(self, filterset_class):
        kwargs = super().get_filterset_kwargs(filterset_class)
        kwargs["workflow"] = self.workflow
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        """Add the article to the context."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.article.articleworkflow
        context["article"] = self.article
        context["messagetype_note"] = Message.MessageTypes.NOTE
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


class MessagesOverview(HtmxMixin, BaseRelatedViewsMixin, PaginatedViewMixin, ListView, FilterMixin):
    """
    A tool used by EO to have an overview on every message in the system.
    """

    title = _("Activity")
    model = Message
    template_name = "wjs_review/messages_overview/messages_overview.html"
    context_object_name = "messages_list"
    filterset_class = MessagesOverviewFilter
    filterset = MessagesOverviewFilter
    paginate_by = 40

    def load_initial(self, request, *args, **kwargs):
        self.paginate_by = get_setting("wjs_review", "review_lists_page_size", self.request.journal).processed_value
        super().load_initial(request, *args, **kwargs)

    def test_func(self):
        """Allow access only to EO (or staff)."""
        return base_permissions.has_admin_role(
            self.request.journal, self.request.user
        ) or base_permissions.has_director_role(self.request.journal, self.request.user)

    def get_template_names(self):
        if self.htmx:
            return ["wjs_review/messages_overview/elements/messages_list.html"]
        return super().get_template_names()

    def get_queryset(self):
        """Return the the list of all messages related to an article."""
        if self.request.GET.get("article_id"):
            workflow = ArticleWorkflow.objects.get(article_id=self.request.GET.get("article_id"))
            return get_messages_related_to_me(self.request.user, workflow.article)
        else:
            return get_messages_related_to_me(self.request.user, journal=self.request.journal)

    def get(self, request, *args, **kwargs):
        # Calling the FilterView get() method and then the ListView get() method to both have filters and pagination
        filterset_class = self.get_filterset_class()
        self.filterset = self.get_filterset(filterset_class)

        if not self.filterset.is_bound or self.filterset.is_valid() or not self.get_strict():
            self.object_list = self.filterset.qs
        else:
            self.object_list = self.filterset.queryset.none()

        context = self.get_context_data(filter=self.filterset, object_list=self.object_list)
        response = super().get(request, *args, **kwargs)
        response.context_data.update(context)
        return response


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
                    title=_("Messages and notes"),
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
            return _("Write to author")
        if self.to_typesetter:
            return _("Write to typesetter")
        return _("Write a message")

    def get_default_recipients(self) -> list[int]:
        """Return the default recipients for the message."""
        if self.to_author:
            # If the message is directly to the author, the EO is the default recipient
            # (used, for instance, when typ writes to au with EO moderation)
            return [get_eo_user(self.workflow.article).pk]

        if self.to_typesetter:
            # If the message is to the typesetter, the typesetter is the default recipient
            typesetting_assignment = self.workflow.get_latest_typesetting_assignment(only_completed=False)

            return [typesetting_assignment.typesetter.pk] if typesetting_assignment else []

        if self.source_message:
            # The recipients of a reply are:
            # - the recipients of the original message
            # - add the sender of the original message
            # - remove the current user
            # - all people with EO group must be replaced by (only one) EO system-user
            # - remove non-allowed recipients (e.g., if the editor writes to reviewer and author, and the author
            #   replies, we must remove the reviewer from the author's recipients); do this _after_ replacing the EO
            #   people
            #
            # I'm using dictionaries because they behave a bit like sets but preserve ordering

            recipients = {self.source_message.actor.pk: None}

            recipients.update(
                dict.fromkeys(self.source_message.messagerecipients_set.all().values_list("recipient", flat=True)),
            )

            # Usually, when u1 writes to u2 and u3, our reply recipients list, at this point, contains [u1, u2, u3]. If
            # u2 is replying, we should pop it from the list (no one replies to herself, except maybe my wife).
            #
            # However, when someone writes to EO, the EO system user is added to the recipients, e.g. [u1, eo, u3], but
            # no real person access the system with that user, so we cannot blindly pop the user's id
            eo_user = get_eo_user(self.article)
            if self.request.user.pk in recipients:
                recipients.pop(self.request.user.pk)
            # It is also possible that EO clicks "reply" on a system message, in this case no EO user appears either as
            # actor or recipient.
            elif eo_user.pk in recipients:
                recipients.pop(eo_user.pk)

            eos = Account.objects.filter(
                id__in=recipients,
                groups__name__in=[constants.EO_GROUP],
            ).values_list("id", flat=True)
            if eos:
                # remove all users with EO group and replace with EO system-user
                # put the EO system-user in the place of the first EO person
                done = False
                new_dict = {}
                for k in recipients.keys():
                    if k in eos:
                        if not done:
                            new_dict[eo_user.pk] = None
                            done = True
                            continue
                        else:
                            continue
                    else:
                        new_dict[k] = None
                recipients = new_dict

            allowed_recipients = set(
                HandleMessage.allowed_recipients_for_actor(
                    actor=self.request.user,
                    article=self.article,
                ).values_list("id", flat=True)
            )
            recipients_ids = [k for k in recipients.keys() if k in allowed_recipients]
            return recipients_ids

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
            if self.to_author:
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

    def get_initial_body(self):
        """Retun a default user's signature to prefill the message body."""
        if permissions.has_eo_role_by_article(self.article.articleworkflow, self.request.user):
            return f"""
<p>
Thank you and best regards,<br>
{self.request.user}<br>
{self.request.journal.code} Editorial Office
</p>
"""
        # NB: ...has_SECTION_editor_role...
        # Remember that we generally use the "section-editor" role, not Janway's all-mighty "editor" role.
        if permissions.is_article_editor(self.article.articleworkflow, self.request.user):
            return f"""
<p>
Thank you and best regards,<br>
{self.request.journal.code} Editor-in-charge
</p>
"""
        if permissions.has_main_director_role_by_article(self.article.articleworkflow, self.request.user):
            return f"""
<p>
Thank you and best regards,<br>
{self.request.journal.code} Editor-in-chief
</p>
"""
        if permissions.has_director_role_by_article(self.article.articleworkflow, self.request.user):
            return f"""
<p>
Thank you and best regards,<br>
{self.request.journal.code} Deputy Editor
</p>
"""
        return ""

    def get_initial(self):
        """Populate the hidden fields.

        Some of these (actor, content_type, object_id, message_type) will be overriden in the form's clean() method but
        we include the correct values here also for good practice.

        """
        if self.source_message:
            default_subject = f"{_('Re:')} {self.source_message.subject}"
        elif self.note:
            default_subject = ""
        else:
            default_subject = f"{_('Message from')} {self.get_sender_label()}"
        to_be_forwarded_to = self.get_to_be_forwarded_to()
        return {
            "actor": self.request.user.pk,
            "recipient": self.recipient.pk if self.recipient else None,
            "content_type": ContentType.objects.get_for_model(self.article).pk,
            "object_id": self.article.pk,
            "message_type": Message.MessageTypes.USER,
            "recipients": self.get_default_recipients(),
            "subject": default_subject,
            "body": self.get_initial_body(),
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
        context["source_message"] = self.source_message
        return context

    def form_valid(self, form):
        """If the form is valid, save the message and return a response."""
        response = super().form_valid(form)
        if self.note:
            messages.success(self.request, _("Note saved."))
        else:
            messages.success(self.request, _("The message has been sent."))
        return response


class MessageNoteUpdateView(BaseRelatedViewsMixin, UpdateView):
    """A view to let the user update a personal note message."""

    model = Message
    form_class = MessageForm
    template_name = "wjs_review/write_message/write_messages.html"
    pk_url_kwarg = "original_message_pk"
    title = _("Edit personal note")

    def load_initial(self, request, *args, **kwargs):
        super().load_initial(request, *args, **kwargs)
        self.workflow = get_object_or_404(ArticleWorkflow, pk=self.kwargs["pk"])

    def test_func(self):
        """User must be the recipient and the message must be a NOTE."""
        self.object = get_object_or_404(self.model, pk=self.kwargs[self.pk_url_kwarg])
        return (
            permissions.can_edit_note(user=self.request.user, message=self.object)
            and self.object.message_type == Message.MessageTypes.NOTE
        )

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.workflow.pk}),
                title=str(self.workflow),
            ),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def get_success_url(self):
        """Redirect to the article messages' list."""
        return reverse("wjs_article_messages", kwargs={"pk": self.workflow.pk})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["actor"] = self.request.user
        kwargs["target"] = self.workflow.article
        kwargs["current_note"] = self.object
        kwargs["note"] = True
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        context["note"] = True
        return context


class MessageNoteDeleteView(AuthenticatedUserPassesTest, DeleteView):
    """A view to let the user delete a personal note message."""

    model = Message
    pk_url_kwarg = "original_message_pk"

    def test_func(self):
        """User must be the recipient and the message must be a NOTE."""
        self.object = get_object_or_404(self.model, pk=self.kwargs[self.pk_url_kwarg])
        return (
            permissions.can_edit_note(user=self.request.user, message=self.object)
            and self.object.message_type == Message.MessageTypes.NOTE
        )

    def get_success_url(self):
        """Redirect to the article messages' list."""
        return reverse("wjs_article_messages", kwargs={"pk": self.kwargs["pk"]})

    def post(self, request, *args, **kwargs):
        if file := self.object.attachments.all().first():
            file.delete()
        super().post(request, *args, **kwargs)
        response = HttpResponse("ok")
        response["HX-Redirect"] = self.get_success_url()
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
    """A view to let the EO toggle read-by-eo flag on a message."""

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

    def form_valid(self, form: ToggleMessageReadByEOForm):
        """
        If the form is valid, save the associate model.

        Then, just return a response with the flag template rendered.
        I.e. do not redirect anywhere.
        """
        self.object = form.save()
        return self.render_to_response(self.get_context_data(form=form, message=self.object))


class UploadRevisionFile(HtmxMixin, AuthenticatedUserPassesTest, FormView):
    """A view to allow an author to upload files during the submission of a revision.

    Uploaded files can be manuscript, data-figure files and cover letter file.

    This view is intended to be used from inside a small modal.
    """

    model = EditorRevisionRequest
    pk_url_kwarg = "revision_id"
    template_name = "wjs_review/revision/upload_file.html"
    form_class = UploadArticleForm
    original_file = None

    @property
    def title(self):
        if self.file_type == "manuscript":
            if self.original_file:
                return _("Replace manuscript")
            else:
                return _("Upload manuscript")
        elif self.file_type == "data":
            if self.original_file:
                return _("Replace data and figure file")  # FIXME! do we really replace or simply add?
            else:
                return _("Upload data and figure file")
        elif self.file_type == "cover_letter":
            if self.object.cover_letter_file:
                return _("Replace cover letter file")
            else:
                return _("Upload cover letter file")

    def test_func(self):
        """User must be corresponding author of the article."""
        # FIXME: Refactor to use load_initial after merging !568
        self.object = get_object_or_404(self.model, pk=self.kwargs[self.pk_url_kwarg])
        if self.kwargs.get("file_id"):
            self.original_file = get_object_or_404(core_models.File, pk=self.kwargs.get("file_id"))
        self.file_type = self.kwargs["file_type"]
        return self.model.objects.filter(
            pk=self.kwargs[self.pk_url_kwarg],
            article__correspondence_author=self.request.user,
        ).exists()

    def get_initial(self):
        initial = super().get_initial()
        # We could be called either during a normal submission or during a "confirm previous version" submission. So we
        # allow for the caller to specify where we should redirect once we have uploaded the file. If we receive
        # something via the query-string, we store it in one of the form fields and use it after the POST.
        initial["next_location"] = self.request.GET.get(
            "next_location",
            self.request.POST.get("next_location", None),
        )

        if self.file_type == "manuscript":
            initial["label"] = self.request.journal.submissionconfiguration.submission_file_text
        elif self.file_type == "data":
            initial["label"] = get_setting(
                "styling", "submission_figures_data_title", self.request.journal
            ).process_value()
        elif self.file_type == "cover_letter":
            initial["label"] = "Cover letter file"
            # don't add the anchor       # if initial["next_location"]:
            # or HX-Redirect won't work! #    initial["next_location"] = f"{initial['next_location']}#cover_letter"
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["file_type"] = self.file_type
        kwargs["instance"] = self.object
        kwargs["original_file"] = self.original_file
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        """If the form is valid, save the file and return a response."""
        form.save()
        new_file = form.new_file
        if new_file:
            if self.original_file:
                review_logic.log_revision_event(
                    "File {} ({}) replaced with {} ({})".format(
                        self.original_file.label,
                        self.original_file.original_filename,
                        new_file.label,
                        new_file.original_filename,
                    ),
                    self.request.user,
                    self.object,
                )
            else:
                review_logic.log_revision_event(
                    "New file {} ({}) uploaded".format(new_file.label, new_file.original_filename),
                    self.request.user,
                    self.object,
                )
            if self.file_type == "manuscript":
                event_logic.Events.raise_event(
                    event_logic.Events.ON_ARTICLE_FILE_UPLOAD,
                    **{
                        "request": self.request,
                        "file_id": new_file,
                        "original_filename": new_file.original_filename,
                        "file_type": "manuscript",
                        "article": self.object.article,
                    },
                )

        response = HttpResponse("ok")
        response.headers["HX-Redirect"] = self.get_success_url()
        return response

    def get_success_url(self):
        if next_location := self.request.POST.get("next_location"):
            success_url = next_location
        else:
            success_url = reverse(
                "do_revisions",
                kwargs={
                    "article_id": self.object.article.pk,
                    "revision_id": self.object.pk,
                },
            )
        return success_url

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["revision_request"] = self.object
        context["article"] = self.object.article
        return context


class DeleteRevisionFile(AuthenticatedUserPassesTest, DeleteView):
    """A view to let the user delete a file from the revision request."""

    title = _("Delete file")
    model = core_models.File
    pk_url_kwarg = "file_id"
    article_pk_url_kwarg = "article_id"
    revision_pk_url_kwarg = "revision_id"
    template_name = "wjs_review/revision/delete_file.html"

    def test_func(self):
        """User must be corresponding author of the article."""
        return Article.objects.filter(
            pk=self.kwargs[self.article_pk_url_kwarg],
            correspondence_author=self.request.user,
        ).exists()

    def get_success_url(self):
        if self.request.POST.get("next"):
            return self.request.POST.get("next")

        return reverse(
            "do_revisions",
            kwargs={
                "article_id": self.kwargs[self.article_pk_url_kwarg],
                "revision_id": self.kwargs[self.revision_pk_url_kwarg],
            },
        )

    def form_valid(self, form):
        """Delete the file and return a response."""
        self.object = self.get_object()
        article = get_object_or_404(Article, pk=self.kwargs[self.article_pk_url_kwarg])
        revision_request = get_object_or_404(EditorRevisionRequest, pk=self.kwargs[self.revision_pk_url_kwarg])
        core_files.delete_file(article, self.object)
        review_logic.log_revision_event(
            "File {} ({}) deleted.".format(self.object.id, self.object.original_filename),
            self.request.user,
            revision_request,
        )
        event_logic.Events.raise_event(
            event_logic.Events.ON_ARTICLE_FILE_DELETE,
            **{
                "request": self.request,
                "file_id": self.object.pk,
                "original_filename": self.object.original_filename,
                "article": article,
            },
        )
        return HttpResponseRedirect(self.get_success_url())


class ArticleRevisionUpdate(BaseRelatedViewsMixin, UpdateView):
    model = EditorRevisionRequest
    pk_url_kwarg = "revision_id"
    template_name = "wjs_review/revision/revision_form.html"
    context_object_name = "revision_request"
    meta_data_fields = ["title", "abstract"]
    confirm_version = False

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
    def title(self):
        if self.confirm_version:
            return _("Confirm previous version")
        if self.object.type == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
            return _("Update Metadata")
        return _("Submit Revision")

    @property
    def page_title(self):
        return f"{self.title} for {self.object.article.title}"

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.object.article.articleworkflow.pk}),
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

    def get_form_class(self):
        """
        Select form class based on the revision request type.

        Each form handle saving author note and confirm_previous_version flag, and it provides different checklist
        fields based on the revision request type.
        """
        if self.confirm_version:
            return ConfirmVersionForm
        if self.object.type == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
            return EditMetadataForm
        return EditorRevisionRequestEditForm

    def get_form_kwargs(self) -> Dict[str, Any]:
        save_metadata = bool(self.request.POST.get("save_metadata"))
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["save_cover_letter"] = bool(self.request.POST.get("save_cover_letter"))
        # when saving metadata form main view form must not be instantiated as submitted
        # so we remove data / files to skip form instantiation and validation
        if save_metadata:
            del kwargs["data"]
            del kwargs["files"]
        if kwargs.get("data"):
            d = kwargs["data"].copy()
            # when finishing the revision, we need to set author note data because
            # author note and finishing form
            # are in the same django form but split in two different HTML forms due to the page layout
            if not kwargs["save_cover_letter"]:
                d["author_note"] = self.object.author_note
            kwargs["data"] = d
        return kwargs

    def _get_metadata_form_class(self) -> Type[EditorRevisionRequestForm]:
        """
        Generate a MetadataForm class for the article.

        Form stores data in :py:class:`EditorRevisionRequest`.
        """
        return EditorRevisionRequestForm

    def _get_metadata_form(self) -> Optional[model_forms.BaseModelForm]:
        """
        Return the MetadataForm instance for the article.

        Form might be None if the article is not in a state where metadata can be edited.
        """
        form_class = self._get_metadata_form_class()

        if self.request.POST.get("save_metadata"):
            meta_data_form = form_class(self.request.POST, instance=self.object)
            meta_data_form.is_valid()
            return meta_data_form
        else:
            initial = {}
            if self.object.title:
                initial["title"] = self.object.title
            if self.object.abstract:
                initial["abstract"] = self.object.abstract
            return form_class(instance=self.object.article, initial=initial)

    def form_invalid(self, form):
        if form.is_bound:
            return super().form_invalid(form)
        else:
            return self.form_valid(form)

    def form_valid(self, form):
        """
        Executed when either EditorRevisionRequestEditForm or MetadataForm is valid.

        Depending on the form and the submit button, different actions are taken:
        - if the submit button is "confirmed", it means the user has confirmed the revision, the control is passed to
          ```AuthorHandleRevision``` logic class to complete the revision submission process and redirect to article
          status page;
        - if the submit button is "save_metadata", it means the user has updated the metadata, we can just save the
          form, update article object associated with the revision request and redirect back to revision request page;
        - in all the other cases we just save the form and redirect back to revision request page.
        """
        if self.request.POST.get(self.get_form_class().CONFIRMED_BUTTON_NAME):
            self.object = form.finish()
            return HttpResponseRedirect(self.get_success_url())
        meta_data_form = self._get_metadata_form()
        meta_data_save = False
        if meta_data_form and meta_data_form.is_valid():
            meta_data_form.save()
            meta_data_save = True
        if not meta_data_save:
            self.object = form.save()
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        """
        Redirect to the article details page if the revision confirmation is submitted or to the revision request page.
        """
        if self.request.POST.get(self.get_form_class().CONFIRMED_BUTTON_NAME):
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
        context["revision"] = self._get_revisions()[0]
        context["meta_data_form"] = self._get_metadata_form()
        context["save_metadata"] = bool(self.request.POST.get("save_metadata"))
        context["technical_revision"] = self.object.type == ArticleWorkflow.Decisions.TECHNICAL_REVISION
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
        if self.request.resolver_match.url_name == "wjs_article_reminders":
            self.workflow = get_object_or_404(ArticleWorkflow, pk=self.kwargs["pk"])
            self.assignment = None
        elif self.request.resolver_match.url_name == "wjs_reminders_per_assignment":
            self.assignment = get_object_or_404(WorkflowReviewAssignment, pk=self.kwargs["pk"])
            self.workflow = self.assignment.article.articleworkflow

    def test_func(self):
        """Let's show reminders only to EO or director or editor."""
        is_eo_or_director = base_permissions.has_admin_role(
            self.request.journal, self.request.user
        ) or base_permissions.has_director_role(self.request.journal, self.request.user)
        if self.assignment:
            return is_eo_or_director or permissions.is_article_editor(self.workflow, self.request.user)
        return is_eo_or_director

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
        """Get reminders related to an article via WorkflowReviewAssignment or WjsEditorAssignment or similar."""
        qs = super().get_queryset()
        if self.assignment:
            return qs.filter(
                content_type=ContentType.objects.get_for_model(WorkflowReviewAssignment),
                object_id=self.assignment.id,
            ).order_by("-date_due")
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
        revision_requests = EditorRevisionRequest.objects.filter(article=self.workflow.article).values_list("pk")
        author_reminders = Q(
            content_type=ContentType.objects.get_for_model(EditorRevisionRequest),
            object_id__in=revision_requests,
        )
        result = qs.filter(editor_reminders | reviewer_reminders | author_reminders)
        return result.order_by("-date_due")

    def get_context_data(self, **kwargs):
        """Add the article to the context."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        context["article"] = self.workflow.article
        reminders = Reminder.objects.filter(id__in=self.get_queryset())
        toggle_reminder_forms = {
            reminder.id: ToggleDisableRemindersForm(instance=reminder, prefix=f"toggle-reminder-{reminder.pk}")
            for reminder in reminders
        }
        context["toggle_reminder_forms"] = toggle_reminder_forms
        return context


class ToggleDisableReminders(AuthenticatedUserPassesTest, UpdateView):
    """A view to let the EO enable/disable reminders."""

    model = Reminder
    form_class = ToggleDisableRemindersForm
    template_name = "wjs_review/reminders/elements/eo_toggle_reminder.html"
    context_object_name = "reminder"

    def test_func(self):
        """User must EO or director or editor (of the RA)."""
        self.object = self.get_object()
        is_eo_or_director = base_permissions.has_eo_or_director_role(self.request.journal, self.request.user)
        is_article_editor = (
            permissions.is_article_editor(self.object.target.article.articleworkflow, self.request.user)
            if self.object.content_type.model_class() == WorkflowReviewAssignment
            else False
        )
        return is_eo_or_director or is_article_editor

    def get_object(self, queryset=None):
        return get_object_or_404(
            self.model,
            pk=self.kwargs["reminder_id"],
        )

    def form_valid(self, form):
        """If the form is valid, save the associate model (the flag on the Reminder disabled).

        Then, just return a response with the flag template rendered. I.e. do not redirect anywhere.
        """
        self.object = form.save()
        return self.render_to_response(self.get_context_data(form=form, message=self.object))


class UpdateReviewerDueDate(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """
    View to allow the Editor to postpone Reviewer Report due date.
    """

    title = _("Change due date")
    model = WorkflowReviewAssignment
    form_class = UpdateReviewerDueDateForm
    template_name = "wjs_review/details/update_reviewer_due_date.html"
    context_object_name = "assignment"
    reviewer = False

    def load_initial(self, request, *args, **kwargs):
        """Fetch the WorkflowReviewAssignment instance for easier processing."""
        super().load_initial(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, pk=self.kwargs[self.pk_url_kwarg])
        self.is_user_editor = permissions.is_article_editor(self.object.article.articleworkflow, self.request.user)

    def test_func(self):
        """User must be the article's editor"""
        articleworkflow = self.object.article.articleworkflow
        if self.object.is_complete:
            raise Http404(_("This review has already been completed."))
        if self.reviewer:
            return permissions.is_article_reviewer(articleworkflow, self.request.user)
        return self.is_user_editor or base_permissions.has_eo_role(self.request.user)

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
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_withdrawl",
            journal=self.object.article.journal,
            context={"assignment": self.object},
            request=self.request,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_withdrawl",
            journal=self.object.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        initial["notification_subject"] = message_subject
        initial["notification_body"] = message_body
        return initial


class SupervisorAssignEditor(BaseRelatedViewsMixin, HtmxMixin, UpdateView):
    """
    If the user is an editor of a special issue, they will be able to assign the paper to a different editor
    """

    model = ArticleWorkflow
    form_class = SupervisorAssignEditorForm
    template_name = "wjs_review/assign_editor/select_editor.html"
    title = _("Select new Editor")
    context_object_name = "workflow"
    edit_permissions: bool = False
    selected_editor: Account = None

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
        if self.edit_permissions:
            return reverse(
                "wjs_assign_permission_redirect", kwargs={"pk": self.object.pk, "user_id": self.selected_editor.pk}
            )
        else:
            return reverse("wjs_article_details", args=(self.object.pk,))

    def _get_current_editor(self) -> Account | None:
        """Get the current editor of the article."""
        try:
            return WjsEditorAssignment.objects.get_current(self.object).editor
        except WjsEditorAssignment.DoesNotExist:
            return None

    def _editors_with_keywords(self, search_text) -> QuerySet[Account]:
        """
        Provides a list of available editors annotated with related keywords.

        The list is filtered by removing current editor, if any.
        """
        article_authors = self.object.article.authors.all()
        try:
            current_editor = WjsEditorAssignment.objects.get_current(self.object).editor
        except WjsEditorAssignment.DoesNotExist:
            current_editor = None
        qs = Account.objects.get_editors_with_keywords(self.object.article, current_editor).exclude(
            pk__in=article_authors
        )
        if search_text:
            search_filters = Q(Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text))
            qs = qs.filter(search_filters)
        return qs

    def get_initial(self):
        initial = super().get_initial()
        initial["search"] = self.request.GET.get("search", None)
        return initial

    def get_context_data(self, **kwargs) -> Context:
        context = super().get_context_data(**kwargs)
        search_text = self.request.GET.get("search", None)
        context["editors_with_keywords"] = self._editors_with_keywords(search_text)
        context["current_editor"] = self._get_current_editor()
        return context

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        search_text = self.request.GET.get("search", None)
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["instance"] = self.object
        kwargs["selectable_editors"] = self._editors_with_keywords(search_text)
        if selected_editor_pk := self.request.GET.get("selected_editor", None):
            kwargs["selected_editor"] = Account.objects.get(pk=selected_editor_pk)
        return kwargs

    def get_template_names(self) -> List[str]:
        """Select the template based on the request type."""
        if self.htmx:
            if self.request.headers.get("Hx-Trigger-Name") == "search-editor-form":
                return ["wjs_review/assign_editor/elements/editor_table.html"]
            elif self.request.headers.get("Hx-Trigger-Name") == "selected_editor":
                return ["wjs_review/assign_editor/elements/new_editor_form.html"]
        return ["wjs_review/assign_editor/select_editor.html"]

    def form_valid(self, form):
        """If the form is valid, save the assignment and return a response."""
        self.edit_permissions = form.assign_permissions
        self.selected_editor = form.cleaned_data["selected_editor"]
        return super().form_valid(form)


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
                title=_("Messages and notes"),
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


class ArticleExtraInformationUpdateView(BaseRelatedViewsMixin, UpdateView):
    title = _("Send short description and image for social media")
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        appeal_revision_days = get_setting(
            setting_group_name="wjs_review",
            setting_name="default_author_appeal_revision_days",
            journal=self.object.article.journal,
        ).process_value()
        date_due = timezone.now().date() + datetime.timedelta(days=appeal_revision_days)
        context["date_due"] = date_due
        return context

    def form_valid(self, form):
        """If the form is valid, save the message and return a response."""
        super().form_valid(form)
        response = HttpResponse("ok")
        response.headers["HX-Redirect"] = self.get_success_url()
        messages.success(self.request, _("The paper has been moved to under appeal state."))
        return response


class AuthorWithdrawPreprint(BaseRelatedViewsMixin, UpdateView):
    """View for author to withdraw his manuscript."""

    title = _("Withdraw manuscript")
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
        try:
            current_editor = WjsEditorAssignment.objects.get_current(self.object.article).editor
        except WjsEditorAssignment.DoesNotExist:
            current_editor = None
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


class ToggleIssueBatch(HtmxMixin, AuthenticatedUserPassesTest, UpdateView):
    """A view to toggle the issue batch state."""

    model = Issue
    context_object_name = "issue"
    fields = ["batch_publish"]
    success_url = reverse_lazy("wjs_review_eo_issues_list")

    def test_func(self):
        """User must be the journal's EO."""
        return base_permissions.has_eo_role(self.request.user)

    def post(self, request, *args, **kwargs):
        """Toggle value of IssueParameters.batch_publish and reload the page."""
        self.object = self.get_object()
        self.object.issueparameters.batch_publish = not self.object.issueparameters.batch_publish
        self.object.issueparameters.save()
        response = HttpResponse("ok")
        response["HX-Redirect"] = self.success_url
        return response


class DownloadSingleFile(AuthenticatedUserPassesTest, View):
    """
    View to allow any user to download single files across all templates. Also checking if the user has permission
    """

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.attachment = core_models.File.objects.get(pk=self.kwargs["file_id"])
        self.article = Article.objects.get(pk=self.kwargs["article_id"])

    def test_func(self):
        """Check if the user can see(download) the file. Both full permission and NO_NAME grant access to files."""
        related_instances = self._get_related_instances()
        for instance, primary in related_instances:
            if primary:
                if PermissionChecker()(
                    self.article.articleworkflow,
                    self.request.user,
                    instance,
                    permission_type=PermissionAssignment.PermissionType.NO_NAMES,
                ):
                    return True
            else:
                if PermissionChecker()(
                    self.article.articleworkflow,
                    self.request.user,
                    instance,
                    secondary_permission=True,
                    permission_type=PermissionAssignment.BinaryPermissionType.ALL,
                ):
                    return True
        return False

    def _get_related_instances(self):
        """Get the instance's relations to check permissions."""
        related_instances = []
        # Article's fields
        if (
            self.article.large_image_file == self.attachment
            or self.article.thumbnail_image_file == self.attachment
            or (self.article.render_galley and self.article.render_galley.file == self.attachment)
            # TBV: check also article.meta_image (ImageField)
        ):
            related_instances.append((self.article, True))
        for related_field in [
            self.article.manuscript_files,
            self.article.data_figure_files,
            self.article.source_files,
        ]:
            # Reminder: in a m2m field, pk=pk or pk__in=[pk] are equivalent
            if related_field.filter(pk=self.attachment.pk).exists():
                related_instances.append((self.article, True))
        if self.article.supplementary_files.filter(file__pk=self.attachment.pk).exists():
            related_instances.append((self.article, True))

        # ArticleWorkflow's files
        for aw in [
            self.article.articleworkflow.supplementary_files_at_acceptance.filter(file__pk=self.attachment.pk).first(),
            self.article.articleworkflow.publication_galleys_source_file,
        ]:
            if aw:
                related_instances.append((self.article, True))
        # Add a tuple to related_instances with the second item being if use the primary  permission (True) or the
        # Secondary permission (False)

        # EditorRevisionRequest's files
        for err in [
            EditorRevisionRequest.objects.filter(manuscript_files__pk=self.attachment.pk).first(),
            EditorRevisionRequest.objects.filter(data_figure_files__pk=self.attachment.pk).first(),
            EditorRevisionRequest.objects.filter(source_files__pk=self.attachment.pk).first(),
            EditorRevisionRequest.objects.filter(supplementary_files__file__pk=self.attachment.pk).first(),
        ]:
            if err:
                related_instances.append((err, True))
        for err in [
            EditorRevisionRequest.objects.filter(cover_letter_file=self.attachment).first(),
        ]:
            if err:
                related_instances.append((err, False))

        # WorkflowReviewAssignment's files (reviewers' report files)
        if wra := WorkflowReviewAssignment.objects.filter(review_file__pk=self.attachment.pk).first():
            related_instances.append((wra, False))

        # TypesettingAssignment's files
        for ta in [
            TypesettingAssignment.objects.filter(files_to_typeset__pk=self.attachment.pk).first(),
            TypesettingAssignment.objects.filter(galleys_created__file__pk=self.attachment.pk).first(),
            TypesettingAssignment.objects.filter(galleys_created__images__pk=self.attachment.pk).first(),
        ]:
            if ta:
                related_instances.append((ta, True))

        # GalleyProofing's files
        for gp in [
            GalleyProofing.objects.filter(proofed_files__file__pk=self.attachment.pk).first(),
            GalleyProofing.objects.filter(annotated_files__pk=self.attachment.pk).first(),
        ]:
            if gp:
                related_instances.append((gp, True))

        return related_instances

    def get(self, request, *args, **kwargs):
        """Serve an article file."""
        return core_files.serve_file(request, self.attachment, self.article)


class DownloadSingleFileImage(DownloadSingleFile):
    """
    View to allow any user to download single images in typesetter preview.
    """

    def setup(self, request, *args, **kwargs):
        super(DownloadSingleFile, self).setup(request, *args, **kwargs)

        self.article = ArticleWorkflow.objects.get(pk=self.kwargs["aw_id"]).article

        # the filename is not a unique key and can be the same in the different typesetting uploads
        ta = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)

        # the filename image must be present in the last created typesetter galley
        for galley in ta.galleys_created.all():
            if galley and galley.label == "HTML":
                # an article galley could have more images with the same filename
                image = (
                    galley.images.filter(original_filename=self.kwargs["file_name"]).order_by("-last_modified").first()
                )
                if image:
                    self.attachment = image
                else:
                    raise Http404()


class DraftArticlePageView(AuthenticatedUserPassesTest, TemplateView):
    template_name = "journal/article.html"

    def test_func(self):
        """Only the typesetter should be able to access this view."""
        self.workflow = get_object_or_404(ArticleWorkflow, pk=self.kwargs["pk"])
        return permissions.is_article_typesetter(self.workflow, self.request.user)

    def get_context_data(self, **kwargs):
        """Add context data for the template."""
        # logic from journal.views.article
        context = super().get_context_data(**kwargs)
        content = ""
        tables_in_galley = []

        galleys = self.workflow.get_latest_typesetting_assignment(only_completed=False).galleys_created.all()
        if galleys:
            # The "production" galleys are detached from the article and are not "public",
            # so we cannot use Janeway's journal.logic.get_best_galley()
            # that looks only for "public" galleys.
            # Also, we have a simpler situation, because we don't care about XML or image galleys.
            # Here we find the HTML galley ourselves.
            try:
                galley = galleys.get(
                    file__mime_type__in=core_files.HTML_MIMETYPES,
                    public=False,  # keeping as a sort of "sanity check"
                )
            except (core_models.Galley.DoesNotExist, core_models.Galley.MultipleObjectsReturned):
                pass
            else:
                # Temporarily set the galley's article to our workflow.article because the following methods need an
                # article attached to the Galley. This won't be saved anyway.
                galley.article = self.workflow.article
                content = galley.file_content(recover=True)
                tables_in_galley = get_all_tables_from_html(content)

        if self.workflow.article.journal.disable_html_downloads:
            galleys = galleys.exclude(
                file__mime_type="text/html",
            )

        context.update(
            {
                "article": self.workflow.article,
                "galleys": galleys,
                "identifier_type": "id",
                "identifier": self.workflow.article.id,
                "article_content": content,
                "tables_in_galley": tables_in_galley,
            },
        )

        # Freeze authors: the template expects to see frozen-authors
        FrozenAuthor.objects.filter(article=self.workflow.article).delete()
        self.workflow.article.snapshot_authors()

        return context
