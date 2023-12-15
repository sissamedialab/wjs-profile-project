from typing import Any, Dict, List, Optional, Union

from core import files as core_files
from core import models as core_models
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet
from django.http import HttpResponse, HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404
from django.template import Context
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from review import logic as review_logic
from review.models import ReviewAssignment
from submission import models as submission_models
from utils.setting_handler import get_setting

from wjs.jcom_profile.mixins import HtmxMixin
from wjs.jcom_profile.permissions import is_eo

from .communication_utils import get_messages_related_to_me
from .forms import (
    ArticleReviewStateForm,
    DecisionForm,
    EvaluateReviewForm,
    InviteUserForm,
    MessageForm,
    ReportForm,
    ReviewerSearchForm,
    SelectReviewerForm,
    ToggleMessageReadForm,
    UploadRevisionAuthorCoverLetterFileForm,
)
from .mixins import EditorRequiredMixin
from .models import ArticleWorkflow, EditorRevisionRequest, Message, MessageRecipients

Account = get_user_model()

states_when_article_is_considered_archived = [
    ArticleWorkflow.ReviewStates.WITHDRAWN,
    ArticleWorkflow.ReviewStates.REJECTED,
    ArticleWorkflow.ReviewStates.NOT_SUITABLE,
]

# "In review" means articles that are
# - not archived,
# - not in states such as SUBMITTED, INCOMPLETE_SUBMISSION, PAPER_MIGHT_HAVE_ISSUES
# - not in "production" (not yet defined)
states_when_article_is_considered_in_review = [
    ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
    ArticleWorkflow.ReviewStates.PAPER_HAS_EDITOR_REPORT,
    ArticleWorkflow.ReviewStates.TO_BE_REVISED,
]

# TODO: write me!
states_when_article_is_considered_in_production = [
    ArticleWorkflow.ReviewStates.ACCEPTED,
]

states_when_article_is_considered_missing_editor = [
    ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
    ArticleWorkflow.ReviewStates.SUBMITTED,
    ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
]


class ListArticles(LoginRequiredMixin, ListView):
    """Editor's main page."""

    model = ArticleWorkflow
    ordering = "id"
    template_name = "wjs_review/reviews.html"
    context_object_name = "workflows"

    def get_queryset(self):
        """Keep only articles (workflows) for which the user is editor."""
        # TODO: what happens to EditorAssignments when the editor is changed?
        #       - we want to track the info about past assignments
        #       - we want to have only one "live" editor an any given moment
        return ArticleWorkflow.objects.filter(
            article__editorassignment__editor__in=[self.request.user],
            state__in=states_when_article_is_considered_in_review,
        )


class ListArchivedArticles(LoginRequiredMixin, ListView):
    model = ArticleWorkflow
    ordering = "id"
    template_name = "wjs_review/reviews.html"
    context_object_name = "workflows"

    def get_queryset(self):
        """Keep only articles (workflows) for which the user is editor and a "final" decision has been made."""
        return ArticleWorkflow.objects.filter(
            article__editorassignment__editor__in=[self.request.user],
            state__in=states_when_article_is_considered_archived,
        )


class EOPending(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """EO's main page."""

    model = ArticleWorkflow
    ordering = "id"
    template_name = "wjs_review/eo_pending.html"
    context_object_name = "workflows"

    def test_func(self):
        """Allow access only to EO (or staff)."""
        return self.request.user.is_staff or is_eo(self.request.user)

    def get_queryset(self):
        """Keep only pending (no final decision) articles."""
        return ArticleWorkflow.objects.filter(
            article__journal=self.request.journal,
            state__in=states_when_article_is_considered_in_review,
        )


class EOArchived(EOPending):
    def get_queryset(self):
        """Get all published / withdrawn / rejected / not suitable articles."""
        return ArticleWorkflow.objects.filter(
            article__journal=self.request.journal,
            state__in=states_when_article_is_considered_archived,
        )

    def get_context_data(self, **kwargs):
        """Add a "title" to the context for the header."""
        context = super().get_context_data(**kwargs)
        context["title"] = "Archived papers"
        return context


class EOProduction(EOPending):
    def get_queryset(self):
        """Get all articles in production."""
        return ArticleWorkflow.objects.filter(
            article__journal=self.request.journal,
            state__in=states_when_article_is_considered_in_production,
        )

    def get_context_data(self, **kwargs):
        """Add a "title" to the context for the header."""
        context = super().get_context_data(**kwargs)
        context["title"] = "Papers in production"
        return context


class EOMissingEditor(EOPending):
    def get_queryset(self):
        """Get all articles that should be assigned to some editor to be reviewed."""
        return ArticleWorkflow.objects.filter(
            article__journal=self.request.journal,
            state__in=states_when_article_is_considered_missing_editor,
        )

    def get_context_data(self, **kwargs):
        """Add a "title" to the context for the header."""
        context = super().get_context_data(**kwargs)
        context["title"] = "Papers without an editor"
        return context


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


class ArticleMessages(LoginRequiredMixin, ListView):
    """All messages of a certain user that are related to an article.

    Probably only write-to-eo / write-to-directore message.
    """

    model = Message
    template_name = "wjs_review/article_messages.html"

    def setup(self, request, *args, **kwargs):
        """Filter only messages related to a certain article and that the current user can see."""
        super().setup(request, *args, **kwargs)
        self.article = get_object_or_404(submission_models.Article, id=self.kwargs["article_id"])

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
        forms = {mr.message.id: ToggleMessageReadForm(instance=mr) for mr in messagerecipients_records}
        context["forms"] = forms
        return context


class MessageAttachmentDownloadView(UserPassesTestMixin, DetailView):
    """Let the recipients of a message with attachment download the attachment."""

    model = Message
    pk_url_kwarg = "message_id"

    def test_func(self):
        """The recipients and the actor of the message can download the file."""
        user = self.request.user
        message = self.get_object()
        return user == message.actor or user in message.recipients.all() or user.is_staff or is_eo(self.request.user)

    def get(self, request, *args, **kwargs):
        """Serve the attachment file."""
        attachment = core_models.File.objects.get(id=self.kwargs["attachment_id"])
        article = self.get_object().target
        # Here, public=True means that the downloaded file will have a human-readable name, not the uuid
        return core_files.serve_file(request, attachment, article, public=True)


class WriteMessage(LoginRequiredMixin, CreateView):
    """A view to let the user write a new message.

    The view also lists all messages of a certain article that the user can see.
    """

    model = Message
    template_name = "wjs_review/write_message.html"
    form_class = MessageForm

    def get_form_kwargs(self) -> Dict[str, Any]:
        """Add article (target) to the form's kwargs.

        Actor will be evinced by the form directly from the request.
        """
        kwargs = super().get_form_kwargs()
        kwargs["actor"] = self.request.user
        kwargs["target"] = self.article
        kwargs["initial_recipient"] = self.recipient
        return kwargs

    def post(self, request, *args, **kwargs):
        """Complete the message form.

        Bind the recipients formset to POST data and use the recipients_formset's cleaned_data to populate the
        "recipients" field of the main form.

        """
        form = self.get_form()
        recipients_formset = form.MessageRecipientsFormSet(
            prefix="recipientsFS",
            form_kwargs={
                "actor": request.user,
                "article": self.article,
            },
            data=request.POST,
        )
        if recipients_formset.is_valid():
            request_post_copy = request.POST.copy()
            # It is possible that the user leaves some formset uncompleted.
            # This is not a problem as long as there is at least one recipient.
            request_post_copy["recipients"] = [
                f.cleaned_data["recipient"].id for f in recipients_formset if "recipient" in f.cleaned_data
            ]
            if len(request_post_copy["recipients"]) < 1:
                raise ValidationError(_("At least one recipient is necessary"), code="missing_recipient")
            request.POST = request_post_copy
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        """Point back to the article's detail page."""
        return reverse("wjs_article_details", kwargs={"pk": self.article.articleworkflow.pk})

    def setup(self, request, *args, **kwargs):
        """Filter only messages related to a certain article and that the current user can see."""
        super().setup(request, *args, **kwargs)
        self.article = get_object_or_404(submission_models.Article, id=self.kwargs["article_id"])
        self.recipient = get_object_or_404(Account, id=self.kwargs["recipient_id"])
        messages = get_messages_related_to_me(user=self.request.user, article=self.article)
        self.messages = messages.filter(Q(recipients__in=[self.recipient]) | Q(actor=self.recipient))

    def get_initial(self):
        """Populate the hidden fields.

        Some of these (actor, content_type, object_id, message_type) will be overriden in the form's clean() method but
        we include the correct values here also for good practice.

        """
        return {
            "actor": self.request.user.id,
            "recipient": self.recipient.id,
            "content_type": ContentType.objects.get_for_model(self.article).id,
            "object_id": self.article.id,
            "message_type": Message.MessageTypes.VERBOSE,
        }

    def get_context_data(self, **kwargs):
        """Add the article and the recipient to the context."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.article.articleworkflow
        context["article"] = self.article
        context["recipient"] = self.recipient
        context["message_list"] = self.messages
        return context


class ToggleMessageReadView(UserPassesTestMixin, UpdateView):
    """A view to let the user toggle read/unread flag on a message."""

    model = MessageRecipients
    form_class = ToggleMessageReadForm
    template_name = "wjs_review/elements/toggle_message_read.html"

    def test_func(self):
        """User must be the recipient (or staff or EO)."""
        return (
            self.request.user.id == self.kwargs["recipient_id"]
            or self.request.user.is_staff
            or is_eo(self.request.user)
        )

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


class UploadRevisionAuthorCoverLetterFile(UserPassesTestMixin, LoginRequiredMixin, UpdateView):
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
        return self.model.objects.filter(pk=self.kwargs[self.pk_url_kwarg], article__owner=self.request.user).exists()

    def get_success_url(self):
        """Redirect to the article details page."""
        return reverse("do_revisions", kwargs={"article_id": self.object.article.pk, "revision_id": self.object.pk})
