"""Views related to typesetting/production."""

from core.models import File, SupplementaryFile
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import FormView, ListView, UpdateView, View
from journal.models import Journal
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from plugins.wjs_review.states import BaseState

from wjs.jcom_profile import permissions as base_permissions
from wjs.jcom_profile.mixins import HtmxMixin

from .communication_utils import get_eo_user
from .forms__production import (
    FileForm,
    TypesetterUploadFilesForm,
    UploadAnnotatedFilesForm,
    WriteToTypMessageForm,
)
from .logic__production import (
    AuthorSendsCorrections,
    HandleCreateSupplementaryFile,
    HandleDeleteSupplementaryFile,
    HandleDownloadRevisionFiles,
    RequestProofs,
    TogglePublishableFlag,
)
from .models import ArticleWorkflow
from .permissions import is_article_author, is_article_typesetter

Account = get_user_model()


class TypesetterPending(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all paper that a typesetter could take in charge.

    AKA "codone" :)
    """

    model = ArticleWorkflow
    template_name = "wjs_review/typesetter_pending.html"
    context_object_name = "workflows"

    def test_func(self):
        """Allow access to typesetters and EO."""
        return base_permissions.has_typesetter_role_on_any_journal(self.request.user) or base_permissions.has_eo_role(
            self.request.user,
        )

    def get_queryset(self):
        """List articles ready for typesetter for each journal that the user is typesetter of.

        List all articles ready for typesetter if the user is EO.
        """
        base_qs = ArticleWorkflow.objects.filter(
            state__in=[ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER],
        ).order_by("-article__date_accepted")

        if base_permissions.has_eo_role(self.request.user):
            return base_qs
        else:
            typesetter_role_slug = "typesetter"
            journals_for_which_user_is_typesetter = Journal.objects.filter(
                accountrole__role__slug=typesetter_role_slug,
                accountrole__user__id=self.request.user.id,
            ).values_list("id", flat=True)
            return base_qs.filter(article__journal__in=journals_for_which_user_is_typesetter)


class TypesetterWorkingOn(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all papers that a certain typesetter is working on."""

    model = ArticleWorkflow
    template_name = "wjs_review/typesetter_pending.html"
    context_object_name = "workflows"

    def test_func(self):
        """Allow access to typesetters and EO."""
        return base_permissions.has_typesetter_role_on_any_journal(self.request.user)

    def get_queryset(self):
        """List articles assigned to the user and still open."""
        qs = ArticleWorkflow.objects.filter(
            state__in=[
                ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
                ArticleWorkflow.ReviewStates.PROOFREADING,
            ],
            article__typesettinground__isnull=False,
            article__typesettinground__typesettingassignment__typesetter__pk=self.request.user.pk,
            article__typesettinground__typesettingassignment__completed__isnull=True,
        ).order_by("-article__date_accepted")

        return qs


class TypesetterArchived(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all past papers of a typesetter."""


class TypesetterUploadFiles(UserPassesTestMixin, LoginRequiredMixin, UpdateView):
    """View allowing the typesetter to upload files."""

    model = TypesettingAssignment
    form_class = TypesetterUploadFilesForm
    template_name = "wjs_review/typesetter_upload_files.html"

    def test_func(self):
        self.article = self.model.objects.get(pk=self.kwargs[self.pk_url_kwarg]).round.article.articleworkflow
        return is_article_typesetter(self.article, self.request.user)

    def get_success_url(self):
        """Point back to the article's detail page."""
        return reverse("wjs_article_details", kwargs={"pk": self.article.pk})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs


class DownloadRevisionFiles(UserPassesTestMixin, LoginRequiredMixin, View):
    """
    View to allow the Typesetter to download the last-revision files for an article.
    """

    model = ArticleWorkflow

    def setup(self, request, *args, **kwargs):
        """Store a reference to the article for easier processing."""
        super().setup(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, id=self.kwargs["pk"])

    def test_func(self):
        """User must be the article's typesetter"""
        return is_article_typesetter(self.object, self.request.user) or base_permissions.has_eo_role(self.request.user)

    def get_logic_instance(self):
        """Instantiate :py:class:`HandleDownloadRevisionFiles` class."""
        service = HandleDownloadRevisionFiles(
            workflow=self.object,
            request=self.request,
        )
        return service

    def get(self, *args, **kwargs):
        """Serve the archive for download using HttpResponse."""
        service = self.get_logic_instance()
        try:
            archive_bytes = service.run()
            response = HttpResponse(archive_bytes, content_type="application/zip")
            response["Content-Disposition"] = 'attachment; filename="revision_files.zip"'
            return response
        except ValidationError:
            # FIXME: how do we want to handle this error?
            return Http404


class ReadyForProofreadingView(UserPassesTestMixin, LoginRequiredMixin, UpdateView):
    """View to allow the Typesetter to mark an article as ready for proofreading."""

    model = TypesettingAssignment
    template_name = "wjs_review/elements/typesetter_marks_ready_to_proofread.html"

    def setup(self, request, *args, **kwargs):
        """Store a reference to the article and object for easier processing."""
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs[self.pk_url_kwarg])
        self.article = self.object.round.article

    def test_func(self):
        """User must be the article's typesetter"""
        return is_article_typesetter(self.article.articleworkflow, self.request.user)

    def get_logic_instance(self):
        """Instantiate :py:class:`RequestProofs` class."""
        service = RequestProofs(
            workflow=self.article.articleworkflow,
            request=self.request,
            assignment=self.object,
            typesetter=self.request.user,
        )
        return service

    def get(self, request, *args, **kwargs):
        """Make the article's state as Ready for Typesetting."""
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            context = {"error": str(e)}
        else:
            context = {"article": self.article}
        return render(request, self.template_name, context)


class CreateSupplementaryFileView(HtmxMixin, UserPassesTestMixin, LoginRequiredMixin, FormView):
    """View to allow the typesetter to upload supplementary files."""

    model = File
    form_class = FileForm
    template_name = "wjs_review/elements/article_files_listing.html"

    def setup(self, request, *args, **kwargs):
        """Fetch the Article instance for easier processing."""
        super().setup(request, *args, **kwargs)
        self.articleworkflow = get_object_or_404(ArticleWorkflow, article_id=self.kwargs["article_id"])

    def test_func(self):
        """Typesetter can upload files."""
        return is_article_typesetter(self.articleworkflow, self.request.user)

    def get_logic_instance(self) -> HandleCreateSupplementaryFile:
        """Instantiate :py:class:`HandleCreateSupplementaryFile` class."""
        return HandleCreateSupplementaryFile(
            request=self.request,
            article=self.articleworkflow.article,
        )

    def post(self, request, *args, **kwargs):
        try:
            service = self.get_logic_instance()
            self.article = service.run()
        except ValidationError as e:
            context = {"error": str(e)}
        else:
            self.article.refresh_from_db()
            context = {"article": self.article}
        return render(request, self.template_name, context)


class DeleteSupplementaryFileView(HtmxMixin, UserPassesTestMixin, LoginRequiredMixin, View):
    """View to allow the typesetter to delete supplementary files."""

    model = SupplementaryFile
    template_name = "wjs_review/elements/article_files_listing.html"

    def setup(self, request, *args, **kwargs):
        """Fetch the Article instance for easier processing."""
        super().setup(request, *args, **kwargs)
        self.supplementary_file = get_object_or_404(self.model, pk=self.kwargs["file_id"])
        self.article = self.supplementary_file.file.article

    def test_func(self):
        """Ensure only typesetters can delete files."""
        return is_article_typesetter(self.article.articleworkflow, self.request.user)

    def get_logic_instance(self) -> HandleDeleteSupplementaryFile:
        """Instantiate :py:class:`HandleDeleteSupplementaryFile` class."""
        return HandleDeleteSupplementaryFile(
            request=self.request,
            supplementary_file=self.supplementary_file,
            article=self.article,
        )

    def post(self, request, *args, **kwargs):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            context = {"error": str(e)}
        else:
            context = {"article": self.article}
        return render(request, self.template_name, context)


class WriteToTyp(UserPassesTestMixin, LoginRequiredMixin, FormView):
    """Let the author write to the typesetter of a certain article."""

    model = ArticleWorkflow
    template_name = "wjs_review/write_message_to_typ.html"
    context_object_name = "workflow"
    form_class = WriteToTypMessageForm

    def setup(self, request, *args, **kwargs):
        """Store a reference to the article for easier processing."""
        super().setup(request, *args, **kwargs)
        self.workflow = get_object_or_404(self.model, id=self.kwargs["pk"])

    def test_func(self):
        """User must be the article's author."""
        return is_article_author(self.workflow, self.request.user) or base_permissions.has_eo_role(self.request.user)

    def get_recipient(self):
        """Get the typesetter of the most recent TypesettingAssignment for this Article.

        He will be the recipient of the message.
        """
        return (
            TypesettingAssignment.objects.filter(
                round__article=self.workflow.article,
            )
            .values(
                "typesetter__pk",
            )
            .order_by("round__number")
            .last()["typesetter__pk"]
        )

    def get_success_url(self):
        """Point back to the article's detail page."""
        return reverse("wjs_article_details", kwargs={"pk": self.workflow.pk})

    def get_context_data(self, **kwargs):
        """Add the workflow."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        return context

    def get_form_kwargs(self):
        """Pass along user and article."""
        kwargs = super().get_form_kwargs()
        kwargs.update(
            {
                "actor": self.request.user,
                "article": self.workflow.article,
                "recipients": self.get_recipient(),
            },
        )
        return kwargs

    def form_valid(self, form):
        """Add a Message for the typesetter and send the notification."""
        form.create_message()
        return super().form_valid(form)


# TODO: refactor with WriteToTyp
# (derive from it and override test func and get_recipient and form_valid)
class WriteToAuWithModeration(UserPassesTestMixin, LoginRequiredMixin, FormView):
    """Let the typesetter write to the author of a certain article.

    The typesetter message will not go directly to the author, but it will go to the EO, who can then forward it.

    """

    model = ArticleWorkflow
    template_name = "wjs_review/write_message_to_typ.html"
    context_object_name = "workflow"
    form_class = WriteToTypMessageForm

    def setup(self, request, *args, **kwargs):
        """Store a reference to the article for easier processing."""
        super().setup(request, *args, **kwargs)
        self.workflow = get_object_or_404(self.model, id=self.kwargs["pk"])

    def test_func(self):
        """User must be the article's author."""
        return is_article_typesetter(
            self.workflow,
            self.request.user,
        ) or base_permissions.has_eo_role(
            self.request.user,
        )

    def get_recipient(self):
        """Get the EO user.

        He will be the recipient of the message.
        """
        return get_eo_user(self.workflow.article)

    def get_to_be_forwarded_to(self):
        """Get the author.

        He will be added as the person to which EO should forward this message.
        """
        return self.workflow.article.correspondence_author

    def get_success_url(self):
        """Point back to the article's detail page."""
        return reverse("wjs_article_details", kwargs={"pk": self.workflow.pk})

    def get_context_data(self, **kwargs):
        """Add the workflow."""
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        return context

    def get_form_kwargs(self):
        """Pass along recipient, user and article."""
        kwargs = super().get_form_kwargs()
        kwargs.update(
            {
                "actor": self.request.user,
                "article": self.workflow.article,
                "recipients": self.get_recipient(),
            },
        )
        return kwargs

    def form_valid(self, form):
        """Add a Message for the typesetter and send the notification."""
        form.create_message(to_be_forwarded_to=self.get_to_be_forwarded_to())
        return super().form_valid(form)


class ListAnnotatedFilesView(HtmxMixin, UserPassesTestMixin, LoginRequiredMixin, UpdateView):
    """View to allow the author to list, upload and delete annotated files."""

    model = GalleyProofing
    form_class = UploadAnnotatedFilesForm
    context_object_name = "galleyproofing"

    def get_template_names(self):
        if self.htmx:
            return ["wjs_review/elements/typesetting_annotate_files.html"]
        return ["wjs_review/annotated_files_listing.html"]

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = get_object_or_404(GalleyProofing, pk=kwargs["pk"])
        self.article = self.object.round.article

    def test_func(self):
        """Author can make actions on annotated files."""
        return is_article_author(self.article.articleworkflow, self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["article"] = self.article
        kwargs["galleyproofing"] = self.object
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        """If the form is valid, save the associate model (the flag on the MessageRecipient).

        Then, just return a response with the flag template rendered. I.e. do not redirect anywhere.

        """
        form.save()
        return self.render_to_response(self.get_context_data(form=form, pk=self.object.pk))


class AuthorSendsCorrectionsView(UserPassesTestMixin, LoginRequiredMixin, UpdateView):
    """Author sends corrections to Typesetter."""

    model = TypesettingAssignment
    template_name = "wjs_review/author_sends_to_typesetter.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs[self.pk_url_kwarg])
        self.article = self.object.round.article

    def test_func(self):
        """Author can upload files."""
        return is_article_author(self.article.articleworkflow, self.request.user)

    def get_logic_instance(self) -> AuthorSendsCorrections:
        """Instantiate :py:class:`AuthorSendsCorrections` class."""
        return AuthorSendsCorrections(
            user=self.request.user,
            old_assignment=self.object,
            request=self.request,
        )

    def get(self, request, *args, **kwargs):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValueError as e:
            context = {"error": str(e)}
        else:
            context = {"article": self.article}
        return render(request, self.template_name, context)


class TogglePublishableFlagView(HtmxMixin, UserPassesTestMixin, LoginRequiredMixin, View):
    """Typesetter toggles `production_flag_no_checks_needed` flag."""

    model = ArticleWorkflow
    template_name = "wjs_review/elements/article_actions_button.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])

    def test_func(self):
        """Only typesetter can mark publishable/unpublishable."""
        return is_article_typesetter(self.object, self.request.user)

    def get_context_data(self, **kwargs):
        context = {"request": self.request, "article": self.object.article, **kwargs}
        state_class = BaseState.get_state_class(self.object)
        action = state_class.get_action_by_name("toggle paper non-publishable flag")
        context["action"] = action.as_dict(self.object, self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        try:
            self.object = TogglePublishableFlag(workflow=self.object).run()
        except ValueError as e:
            kwargs["error"] = str(e)
        context = self.get_context_data(**kwargs)
        return render(request, self.template_name, context)
