from typing import Any, Dict, List

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Page, Paginator
from django.db.models import QuerySet
from django.http import HttpResponse, QueryDict
from django.template import Context
from django.urls import reverse, reverse_lazy
from django.views.generic import DetailView, ListView, UpdateView
from review.models import ReviewAssignment
from submission import models as submission_models
from utils.setting_handler import get_setting

from wjs.jcom_profile.mixins import HtmxMixin

from .forms import (
    ArticleReviewStateForm,
    EvaluateReviewForm,
    ReviewerSearchForm,
    SelectReviewerForm,
)
from .mixins import EditorRequiredMixin
from .models import ArticleWorkflow

Account = get_user_model()


class ListArticles(LoginRequiredMixin, ListView):
    model = ArticleWorkflow
    ordering = "id"
    template_name = "wjs_review/reviews.html"
    context_object_name = "workflows"


class UpdateState(LoginRequiredMixin, UpdateView):
    model = ArticleWorkflow
    form_class = ArticleReviewStateForm
    template_name = "wjs_review/update_state.html"
    success_url = reverse_lazy("wjs_review_list")

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs


class SelectReviewer(HtmxMixin, EditorRequiredMixin, UpdateView):
    """
    View only checks the login status at view level because the permissions are checked by the queryset by using
    :py:class:`EditorAssignment` relation with the current user.
    """

    model = ArticleWorkflow
    form_class = SelectReviewerForm
    success_url = reverse_lazy("wjs_review_list")
    context_object_name = "workflow"

    def get_queryset(self) -> QuerySet[ArticleWorkflow]:
        return super().get_queryset().filter(article__editorassignment__editor=self.request.user)

    def post(self, request, *args, **kwargs) -> HttpResponse:
        """
        Handle POST requests: instantiate a form instance with the passed POST variables and then check if it's valid.
        """
        self.object = self.get_object()
        if self.htmx:
            return self.get(request, *args, **kwargs)
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

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
            return ["wjs_review/elements/select_reviewer.html"]
        else:
            return ["wjs_review/select_reviewer.html"]

    def paginate(self, queryset: QuerySet) -> Page:
        """
        Paginate the reviewers queryset.

        It's managed explicitly as the view is an UpdateView not a ListView.
        """
        try:
            page_number = int(self.request.GET.get("page", default=1))
        except ValueError:
            page_number = 1
        review_lists_page_size = get_setting("wjs_review", "review_lists_page_size", self.object.article.journal)
        paginator = Paginator(queryset, review_lists_page_size.process_value())
        return paginator.get_page(page_number)

    def get_context_data(self, **kwargs) -> Context:
        context = super().get_context_data(**kwargs)
        context["htmx"] = self.htmx
        context["search_form"] = self.get_search_form()
        context["reviewers"] = self.paginate(Account.objects.filter_reviewers(self.object, self.search_data))
        return context

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["htmx"] = self.htmx
        return kwargs

    def get_search_form(self) -> ReviewerSearchForm:
        return ReviewerSearchForm(self.search_data if self.search_data else None)

    def form_valid(self, form: SelectReviewerForm) -> HttpResponse:
        """
        Executed when SelectReviewerForm is valid

        Even if the form is valid, checks in logic.AssignToReviewer -called by form.save- may fail as well.
        """
        try:
            return super().form_valid(form)
        except ValueError as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class OpenReviewMixin(DetailView):
    """
    Mixin to be used to load a single review by using either the access code or the current user to check permission.

    View is open to both logged in users and anonymous users, because the permissions are checked at the queryset level
    by either using the access code or the current user.
    """

    model = ReviewAssignment
    pk_url_kwarg = "assignment_id"
    context_object_name = "assignment"
    incomplete_review_only = True
    "Filter queryset to exclude completed reviews."

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
        if self.access_code:
            queryset = queryset.filter(access_code=self.access_code)
        elif self.request.user.is_staff:
            pass  # staff can see all reviews
        elif self.request.user.check_role(self.request.journal, "section-editor"):
            queryset = queryset.filter(editor=self.request.user)
        else:
            queryset = queryset.filter(reviewer=self.request.user)
        return queryset

    def get_context_data(self, **kwargs) -> Context:
        context = super().get_context_data(**kwargs)
        context["access_code"] = self.access_code or self.object.access_code
        return context


class EvaluateReviewRequest(OpenReviewMixin, UpdateView):
    form_class = EvaluateReviewForm
    template_name = "wjs_review/review_evaluate.html"
    success_url = reverse_lazy("wjs_review_list")

    def get_success_url(self) -> str:
        """Redirect to a different URL according to the decision."""
        if self.object.date_accepted:
            return reverse("wjs_review_review", args=(self.object.pk,))
        elif self.object.date_declined:
            return reverse("wjs_declined_review", args=(self.object.pk,))
        return str(self.success_url)

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form: EvaluateReviewForm) -> HttpResponse:
        """
        Executed when :py:class:`EvaluateReviewForm` is valid.

        Even if the form is valid, checks in :py:class:`logic.EvaluateReview` -called by form.save- may fail as well.
        """
        try:
            return super().form_valid(form)
        except ValueError as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class ReviewDeclined(OpenReviewMixin):
    template_name = "wjs_review/review_declined.html"
    incomplete_review_only = False


class ReviewSubmit(EvaluateReviewRequest):
    template_name = "wjs_review/review_submit.html"
