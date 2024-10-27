from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q, QuerySet
from django.template import Context
from django.views.generic import DetailView
from review.models import ReviewAssignment
from submission import models as submission_models

from wjs.jcom_profile import permissions as base_permissions

from . import permissions
from .models import ArticleWorkflow, WorkflowReviewAssignment


class EditorRequiredMixin(UserPassesTestMixin):
    """Mixin to check if the user is an editor for the current journal."""

    def test_func(self):
        if self.request.user.is_anonymous:
            return False
        is_section_editor = self.request.user.check_role(self.request.journal, "section-editor")
        return is_section_editor


class ReviewerRequiredMixin(UserPassesTestMixin):
    """Mixin to check if the user is the reviewer of the current article."""

    def test_func(self):
        return permissions.is_article_reviewer(self.workflow, self.request.user)


class AuthenticatedUserPassesTest(UserPassesTestMixin):
    """
    Mixin that combines the behavior of LoginRequiredMixin and UserPassesTestMixin.

    If the user is not authenticated, the mixin will redirect to the login page.
    If the user is authenticated, the mixin:
        - call :py:meth:`load_initial` to initialize data required for checking permissions and exeution
        - run :py:meth:`test_func` to check if the user passes the test
        - run :py:meth:`dispatch` to call the view method
    """

    allow_anonymous_access = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_anonymous and not self.allow_anonymous_access:
            return self.handle_no_permission()
        self.load_initial(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def load_initial(self, request, *args, **kwargs):
        pass


class OpenReviewMixin(DetailView):
    """
    Mixin to be used to load a single review by using either the access code or the current user to check permission.

    View is open to both logged in users and anonymous users, because the permissions are checked at the queryset level
    by either using the access code or the current user.
    """

    model = WorkflowReviewAssignment
    pk_url_kwarg = "assignment_id"
    context_object_name = "assignment"
    incomplete_review_only = True
    "Filter queryset to exclude completed reviews."
    use_access_code = False
    """
    Allow anonymous users accessing the views via query string to access the review.

    It's required in all the steps of the review process:

    - Evaluate to allow user to accept / reject the review
    - Submit to allow user to submit the review right after accepting the review
    - End to allow user to see the outcome of the review submission
    """
    allow_editor_access = False
    """
    Allow editors to access the review.

    Used to grant access to editors to the completed review data.
    """

    @property
    def access_code(self):
        return self.request.GET.get("access_code")

    def get_queryset(self) -> QuerySet[ReviewAssignment]:
        """
        Filter queryset to ensure only :py:class:`ReviewAssignment` suitable for review matching user / access_code.
        """
        queryset = super().get_queryset()
        if self.incomplete_review_only:
            queryset = queryset.filter(is_complete=False)
            queryset = queryset.filter(article__stage=submission_models.STAGE_UNDER_REVIEW)
        if self.access_code and self.use_access_code:
            queryset = queryset.filter(access_code=self.access_code)
        elif self.request.user.is_authenticated:
            if not base_permissions.has_eo_role(self.request.user):
                filters = Q(reviewer=self.request.user)
                if self.allow_editor_access:
                    filters |= Q(editor=self.request.user)
                queryset = queryset.filter(filters)
        else:
            queryset = queryset.none()
        return queryset

    def get_context_data(self, **kwargs) -> Context:
        context = super().get_context_data(**kwargs)
        context["access_code"] = self.access_code or self.object.access_code
        return context


class ArticleAssignedEditorMixin:
    def get_queryset(self) -> QuerySet[ArticleWorkflow]:
        return super().get_queryset().filter(article__editorassignment__editor=self.request.user)
