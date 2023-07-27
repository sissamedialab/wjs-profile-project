from typing import Any, Dict

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Page, Paginator
from django.db.models import QuerySet
from django.template import Context
from django.urls import reverse_lazy
from django.views.generic import ListView, UpdateView
from utils.setting_handler import get_setting

from wjs.jcom_profile.mixins import HtmxMixin

from .forms import ArticleReviewStateForm, ReviewerSearchForm, SelectReviewerForm
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


class SelectReviewer(HtmxMixin, LoginRequiredMixin, UpdateView):
    model = ArticleWorkflow
    form_class = SelectReviewerForm
    success_url = reverse_lazy("wjs_review_list")
    context_object_name = "workflow"

    def post(self, request, *args, **kwargs):
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
    def search_data(self):
        """
        Return the search data from the request.

        As the view can be called by either a GET or a POST request, we need to check both.
        """
        return self.request.GET or self.request.POST

    def get_template_names(self):
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

    def form_valid(self, form: SelectReviewerForm):
        """
        Executed when SelectReviewerForm is valid

        Even if the form is valid, checks in logic.AssignToReviewer -called in form save- may fail as well.
        ."""
        try:
            return super().form_valid(form)
        except Exception:
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)
