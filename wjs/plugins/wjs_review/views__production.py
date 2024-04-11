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
from plugins.typesetting.models import TypesettingAssignment

from wjs.jcom_profile import permissions as base_permissions
from wjs.jcom_profile.mixins import HtmxMixin

from .forms__production import FileForm, TypesetterUploadFilesForm
from .logic__production import (
    HandleCreateSupplementaryFile,
    HandleDeleteSupplementaryFile,
    HandleDownloadRevisionFiles,
    RequestProofs,
)
from .models import ArticleWorkflow
from .permissions import is_article_typesetter

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
