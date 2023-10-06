from typing import Any, Dict, List, Optional, Union

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet
from django.http import HttpResponse, HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404
from django.template import Context
from django.urls import reverse, reverse_lazy
from django.views.generic import DetailView, ListView, UpdateView
from review import logic as review_logic
from review.models import ReviewAssignment
from submission import models as submission_models
from utils.setting_handler import get_setting

from wjs.jcom_profile.mixins import HtmxMixin

from .communication_utils import get_messages_related_to_me
from .forms import (
    ArticleReviewStateForm,
    DecisionForm,
    EvaluateReviewForm,
    InviteUserForm,
    ReportForm,
    ReviewerSearchForm,
    SelectReviewerForm,
)
from .mixins import EditorRequiredMixin
from .models import ArticleWorkflow, Message

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


class ArticleAssignedEditorMixin:
    def get_queryset(self) -> QuerySet[ArticleWorkflow]:
        # TODO: We must check this once we have decided the flow for multiple review rounds
        #       it should work because if an editor is deassigned from one round to another we delete the assignment
        #       and this relation will cease to exist
        return super().get_queryset().filter(article__editorassignment__editor=self.request.user)


class SelectReviewer(HtmxMixin, ArticleAssignedEditorMixin, EditorRequiredMixin, UpdateView):
    """
    View only checks the login status at view level because the permissions are checked by the queryset by using
    :py:class:`EditorAssignment` relation with the current user.
    """

    model = ArticleWorkflow
    form_class = SelectReviewerForm
    context_object_name = "workflow"

    def get_success_url(self):
        # TBV:  reverse("wjs_review_list")?  wjs_review_review?
        return reverse("wjs_article_details", args=(self.object.id,))

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
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            # required to handle exception raised in the form save method (coming for janeway business logic)
            return super().form_invalid(form)


class InviteReviewer(LoginRequiredMixin, ArticleAssignedEditorMixin, UpdateView):
    """Invite external users as reviewers.

    The user is created as inactive and his/her account is marked
    without GDPR explicitly accepted, Invited user base
    information are encoded to generate a token to be appended to
    the url for GDPR acceptance.
    """

    model = ArticleWorkflow
    form_class = InviteUserForm
    success_url = reverse_lazy("wjs_review_list")
    template_name = "wjs_review/invite_external_reviewer.html"
    context_object_name = "workflow"

    def get_success_url(self):
        # TBV:  reverse("wjs_review_list")?  wjs_review_review?
        return reverse("wjs_article_details", args=(self.object.id,))

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        kwargs["instance"] = self.object
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


class ArticleDetails(LoginRequiredMixin, DetailView):
    model = ArticleWorkflow
    template_name = "wjs_review/details.html"
    context_object_name = "workflow"


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
        elif self.request.user.is_authenticated and self.request.user.check_role(
            self.request.journal,
            "section-editor",
        ):
            queryset = queryset.filter(editor=self.request.user)
        elif self.request.user.is_authenticated:
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


class ReviewDeclined(OpenReviewMixin):
    template_name = "wjs_review/review_declined.html"
    incomplete_review_only = False


class ReviewEnd(OpenReviewMixin):
    template_name = "wjs_review/review_end.html"
    incomplete_review_only = False


class ReviewSubmit(EvaluateReviewRequest):
    template_name = "wjs_review/review_submit.html"

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

    def _get_report_form(self) -> ReportForm:
        """Instantiate ReportForm (instantiated from ReviewAssigment.form object)."""
        form = ReportForm(
            review_assignment=self.object,
            fields_required=True,
            submit_final=self._submitting_report_final,
            request=self.request,
            **self._get_report_data(),
        )
        return form

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


class ArticleDecision(LoginRequiredMixin, ArticleAssignedEditorMixin, UpdateView):
    model = ArticleWorkflow
    form_class = DecisionForm
    template_name = "wjs_review/decision.html"
    context_object_name = "workflow"

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
        return kwargs

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
        return self.current_reviews.filter(date_complete__isnull=False, date_accepted__isnull=False)

    @property
    def declined_reviews(self) -> QuerySet[ReviewAssignment]:
        """Return the declined reviews for the current review round."""
        return self.current_reviews.filter(date_declined__isnull=False)

    @property
    def open_reviews(self) -> QuerySet[ReviewAssignment]:
        """Return not completed reviews for the current review round."""
        return self.current_reviews.filter(
            date_complete__isnull=True,
            date_accepted__isnull=False,
            date_declined__isnull=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["declined_reviews"] = self.declined_reviews
        context["submitted_reviews"] = self.submitted_reviews
        context["open_reviews"] = self.open_reviews
        return context


class MyMessages(LoginRequiredMixin, ListView):
    """All messages of a certain user that are not related to any article.

    Pprobably only write-to-eo / write-to-directore message.
    """

    model = Message
    template_name = "wjs_review/my_messages.html"


class Messages(LoginRequiredMixin, ListView):
    """Messages related to a certain article that the user can see."""

    model = Message
    template_name = "wjs_review/article_messages.html"

    def get_queryset(self):
        """Filter only messages related to a certain article and that the current user can see."""
        self.article = get_object_or_404(submission_models.Article, id=self.kwargs["article_id"])
        self.recipient = get_object_or_404(Account, id=self.kwargs["recipient_id"])
        messages = get_messages_related_to_me(user=self.request.user, article=self.article)
        return messages.filter(Q(recipients__in=[self.recipient]) | Q(actor=self.recipient))

    def get_context_data(self, **kwargs):
        """Add the article and the recipient to the context."""
        context = super().get_context_data(**kwargs)
        # These have been "collected" by the get_queryset method
        context["article"] = self.article
        context["recipient"] = self.recipient
        return context
