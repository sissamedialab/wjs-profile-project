from typing import Any, Dict

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import ListView, UpdateView

from wjs.jcom_profile.mixins import HtmxMixin

from .forms import ArticleReviewStateForm, ReviewerSearchForm, SelectReviewerForm
from .models import ArticleWorkflow
from .users import get_available_users_by_role


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

    def get_template_names(self):
        if self.htmx:
            return ["wjs_review/elements/reviewers_list.html"]
        else:
            return ["wjs_review/select_reviewer.html"]

    def get_reviewers(self):
        q_filters = Q(pk__gt=0)  # Always true condition to and filters in the following loop
        for key, value in self.request.GET.items():
            if value:
                q_filters &= Q(**{key: value})
        return get_available_users_by_role(
            self.object.article.journal,
            "reviewer",
            exclude=self.object.article_authors.values_list("pk", flat=True),
            filters=q_filters,
        )

    def get_search_form(self):
        return ReviewerSearchForm(self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["htmx"] = self.htmx
        context["search_form"] = self.get_search_form()
        context["reviewers"] = self.get_reviewers()
        return context

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except Exception:
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)
