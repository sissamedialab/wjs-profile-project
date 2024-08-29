"""Views related to typesetting/production."""

from typing import TYPE_CHECKING, List

from core.models import File, SupplementaryFile
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template import RequestContext
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, TemplateView, UpdateView, View
from django_q.tasks import async_task
from journal.models import Issue, Journal
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from plugins.wjs_review.states import BaseState
from utils.management.commands.test_fire_event import create_fake_request

from wjs.jcom_profile import permissions as base_permissions
from wjs.jcom_profile.mixins import HtmxMixin

from .communication_utils import get_eo_user
from .forms__production import (
    EOSendBackToTypesetterForm,
    EsmFileForm,
    SectionOrderForm,
    TypesetterUploadFilesForm,
    UploadAnnotatedFilesForm,
    WriteToTypMessageForm,
)
from .logic import (
    BeginPublication,
    states_when_article_is_considered_production_archived,
    states_when_article_is_considered_typesetter_pending,
    states_when_article_is_considered_typesetter_working_on,
)
from .logic__production import (
    AssignTypesetter,
    AuthorSendsCorrections,
    HandleDeleteSupplementaryFile,
    HandleDownloadRevisionFiles,
    ReadyForPublication,
    RequestProofs,
    TogglePublishableFlag,
    TypesetterTestsGalleyGeneration,
    finishpublication_wrapper,
)
from .models import ArticleWorkflow
from .permissions import (
    has_typesetter_role_by_article,
    is_article_author,
    is_article_supervisor,
    is_article_typesetter,
)
from .views import ArticleWorkflowBaseMixin, BaseRelatedViewsMixin

if TYPE_CHECKING:
    from .custom_types import BreadcrumbItem

Account = get_user_model()


class TypesetterPending(ArticleWorkflowBaseMixin):
    """A view showing all paper that a typesetter could take in charge.

    AKA "codone" :)
    """

    title = _("Pending papers")
    role = "typesetter"
    template_name = "wjs_review/lists/articleworkflow_list.html"
    template_table = "wjs_review/lists/elements/typesetter/table.html"
    model = ArticleWorkflow

    def test_func(self):
        """Allow access to typesetters and EO."""
        return base_permissions.has_typesetter_role_on_any_journal(self.request.user) or base_permissions.has_eo_role(
            self.request.user,
        )

    def _get_typesetter_journals(self):
        """Get journals for which the user is typesetter."""
        typesetter_role_slug = "typesetter"
        return Journal.objects.filter(
            accountrole__role__slug=typesetter_role_slug,
            accountrole__user__id=self.request.user.id,
        ).values_list("id", flat=True)

    def _filter_by_journal(self, base_qs):
        """Get journals for which the user is typesetter."""
        if base_permissions.has_eo_role(self.request.user):
            return base_qs
        else:
            return base_qs.filter(article__journal__in=self._get_typesetter_journals())

    def _apply_base_filters(self, qs):
        """List articles ready for typesetter for each journal that the user is typesetter of.

        List all articles ready for typesetter if the user is EO.
        """
        base_qs = self._filter_by_journal(qs)
        return base_qs.filter(
            state__in=states_when_article_is_considered_typesetter_pending,
        ).order_by("-article__date_accepted")


class TypesetterWorkingOn(TypesetterPending):
    """A view showing all papers that a certain typesetter is working on."""

    title = _("Papers Working on")

    def _apply_base_filters(self, qs):
        """List articles assigned to the user and still open."""
        return qs.filter(
            state__in=states_when_article_is_considered_typesetter_working_on,
            article__typesettinground__isnull=False,
            article__typesettinground__typesettingassignment__typesetter__pk=self.request.user.pk,
        ).order_by("-article__date_accepted")


class TypesetterArchived(TypesetterPending):
    """A view showing all past papers of a typesetter."""

    title = _("Typesetter Papers")

    def _apply_base_filters(self, qs):
        """List articles assigned to the user and still open."""
        base_qs = self._filter_by_journal(qs)
        return base_qs.filter(
            state__in=states_when_article_is_considered_production_archived,
        ).order_by("-article__date_accepted")


class TypesetterUploadFiles(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """View allowing the typesetter to upload files."""

    title = _("Files to typeset")
    model = TypesettingAssignment
    form_class = TypesetterUploadFilesForm
    template_name = "wjs_review/details/typesetter_upload_files.html"
    context_object_name = "assignment"

    def test_func(self):
        self.object = self.get_object()
        self.workflow = self.object.round.article.articleworkflow
        return is_article_typesetter(self.workflow, self.request.user)

    def get_success_url(self):
        """Point back to the article's detail page."""
        return reverse("wjs_article_details", kwargs={"pk": self.workflow.pk})

    def form_valid(self, form):
        """Save the form and return a response."""
        try:
            form.save()
        except ValidationError:
            return self.form_invalid(form)
        response = HttpResponse("ok")
        response["HX-Redirect"] = self.get_success_url()
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        return context


class DownloadRevisionFiles(LoginRequiredMixin, UserPassesTestMixin, View):
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


class ReadyForProofreadingView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Typesetter sends the paper to the author for proofreading."""

    model = TypesettingAssignment

    def setup(self, request, *args, **kwargs):
        """Store a reference to the article and object for easier processing."""
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])
        self.article = self.object.round.article

    def test_func(self):
        """User must be the article's typesetter"""
        return is_article_typesetter(self.article.articleworkflow, self.request.user)

    def post(self, request, *args, **kwargs):
        """Make the article's state as Ready for Typesetting."""
        try:
            RequestProofs(
                workflow=self.article.articleworkflow,
                request=self.request,
                assignment=self.object,
                typesetter=self.request.user,
            ).run()
        except ValidationError as e:
            messages.error(request=self.request, message=e)
        else:
            messages.success(request=self.request, message="The paper has been sent to the author for proofs.")
        return HttpResponseRedirect(
            reverse(
                "wjs_article_details",
                kwargs={"pk": self.object.round.article.articleworkflow.pk},
            ),
        )


class ListSupplementaryFileView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    """View to allow the typesetter to upload supplementary files."""

    model = ArticleWorkflow
    template_name = "wjs_review/details/esm_files_list.html"
    form_class = EsmFileForm
    context_object_name = "workflow"

    def setup(self, request, *args, **kwargs):
        """Store a reference to the article and object for easier processing."""
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])

    def test_func(self):
        """User must be the article's typesetter"""
        return is_article_typesetter(self.object, self.request.user) or is_article_supervisor(
            self.object, self.request.user
        )

    @property
    def extra_links(self):
        preprintid = self.object.article.get_identifier("preprintid")
        if not preprintid:
            preprintid = self.object.article_id
        return {
            "javascript:history.back()": _("Back to list"),
            reverse("wjs_article_details", kwargs={"pk": self.kwargs["pk"]}): preprintid,
            reverse("wjs_article_esm_files", kwargs={"pk": self.kwargs["pk"]}): preprintid,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["extra_links"] = self.extra_links
        context["article"] = self.object.article
        context["form"] = self.form_class(
            user=self.request.user,
            instance=self.object,
        )
        return context


class CreateSupplementaryFileView(HtmxMixin, LoginRequiredMixin, UserPassesTestMixin, FormView):
    """View to allow the typesetter to upload supplementary files."""

    model = File
    form_class = EsmFileForm
    template_name = "wjs_review/details/esm_files_list.html"

    def setup(self, request, *args, **kwargs):
        """Fetch the Article instance for easier processing."""
        super().setup(request, *args, **kwargs)
        self.articleworkflow = get_object_or_404(ArticleWorkflow, article_id=self.kwargs["article_id"])

    def test_func(self) -> bool:
        """Typesetter can upload files."""
        return is_article_typesetter(self.articleworkflow, self.request.user) or is_article_supervisor(
            self.object, self.request.user
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.articleworkflow
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form) -> HttpResponse:
        self.articleworkflow = form.save()
        return self.get(self.request, *self.args, **self.kwargs)

    def get_context_data(self, **kwargs) -> RequestContext:
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.articleworkflow
        context["article"] = self.articleworkflow.article
        return context


class DeleteSupplementaryFileView(HtmxMixin, LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """View to allow the typesetter to delete supplementary files."""

    model = SupplementaryFile
    form_class = EsmFileForm
    template_name = "wjs_review/details/esm_files_list.html"

    def setup(self, request, *args, **kwargs):
        """Fetch the Article instance for easier processing."""
        super().setup(request, *args, **kwargs)
        self.supplementary_file = get_object_or_404(self.model, pk=self.kwargs["file_id"])
        self.article = self.supplementary_file.file.article

    def test_func(self):
        """Ensure only typesetters can delete files."""
        return is_article_typesetter(self.article.articleworkflow, self.request.user) or is_article_supervisor(
            self.article.articleworkflow, self.request.user
        )

    def get_logic_instance(self) -> HandleDeleteSupplementaryFile:
        """Instantiate :py:class:`HandleDeleteSupplementaryFile` class."""
        return HandleDeleteSupplementaryFile(
            user=self.request.user,
            supplementary_file=self.supplementary_file,
            article=self.article,
        )

    def post(self, request, *args, **kwargs):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            kwargs["error"] = e
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> RequestContext:
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.article.articleworkflow
        context["article"] = self.article
        context["form"] = self.form_class(
            user=self.request.user,
            instance=self.article.articleworkflow,
        )
        return context


class WriteToTyp(LoginRequiredMixin, UserPassesTestMixin, FormView):
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
            .order_by("round__round_number")
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
class WriteToAuWithModeration(LoginRequiredMixin, UserPassesTestMixin, FormView):
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


class ListAnnotatedFilesView(HtmxMixin, BaseRelatedViewsMixin, UpdateView):
    """View to allow the author to list, upload and delete annotated files."""

    title = _("Send corrections")
    model = GalleyProofing
    form_class = UploadAnnotatedFilesForm
    context_object_name = "galleyproofing"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = get_object_or_404(GalleyProofing, pk=kwargs["pk"])
        self.article = self.object.round.article

    def test_func(self):
        """Author can make actions on annotated files."""
        return is_article_author(self.article.articleworkflow, self.request.user)

    def get_template_names(self):
        if self.htmx:
            return ["wjs_review/details/elements/typesetting_annotate_files.html"]
        return ["wjs_review/details/annotated_files_listing.html"]

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.article.articleworkflow.pk}),
                title=self.article.articleworkflow,
            ),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["article"] = self.article
        kwargs["galleyproofing"] = self.object
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["disable_send_corrections"] = not self.object.notes and not self.object.annotated_files.exists()
        return context

    def get_send_logic_instance(self):
        return AuthorSendsCorrections(
            user=self.request.user,
            old_assignment=self.object.round.typesettingassignment,
            request=self.request,
        )

    def get_success_url(self):
        return reverse("wjs_article_details", kwargs={"pk": self.object.round.article.articleworkflow.pk})

    def _send_corrections(self):
        try:
            service = self.get_send_logic_instance()
            service.run()
        except ValueError as e:
            return False, {"errors": [e]}
        return True, {}

    def form_valid(self, form):
        """If the form is valid, save the associate model (the flag on the MessageRecipient).

        Then, just return a response with the flag template rendered. I.e. do not redirect anywhere.
        """
        kwargs = {}
        try:
            form.save()
            redirect = False
        except ValidationError as e:
            form.add_error(None, e)
            redirect = False
        if form.cleaned_data["action"] == "send_corrections":
            redirect, kwargs = self._send_corrections()
        if redirect:
            messages.success(request=self.request, message=_("Corrections have been dispatched to the typesetter."))
            return HttpResponseRedirect(self.get_success_url())
        else:
            self.kwargs.update(kwargs)
            return self.get(self.request, *self.args, **self.kwargs)


class TogglePublishableFlagView(HtmxMixin, LoginRequiredMixin, UserPassesTestMixin, TemplateView):
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

    def get_success_url(self):
        return reverse("wjs_article_details", kwargs={"pk": self.object.pk})

    def post(self, request, *args, **kwargs):
        try:
            self.object = TogglePublishableFlag(workflow=self.object).run()
        except ValueError as e:
            kwargs["message"] = str(e)
            return self.get(request, **kwargs)
        return HttpResponseRedirect(self.get_success_url())


class ReadyForPublicationView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """A view to move a paper to ready-for-publication.

    This passage can be triggered either
    - by the typesetter (most often)
    - by the author
    """

    model = ArticleWorkflow

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])

    def test_func(self):
        """Only typesetter and author can move the paper to ready-for-publication."""
        return is_article_author(
            self.object,
            self.request.user,
        ) or is_article_typesetter(
            self.object,
            self.request.user,
        )

    def post(self, request, *args, **kwargs):
        try:
            self.object = ReadyForPublication(
                workflow=self.object,
                user=self.request.user,
            ).run()
        except ValueError as e:
            messages.error(request=self.request, message=e)
            return HttpResponseRedirect(
                reverse(
                    "wjs_article_details",
                    kwargs={"pk": self.object.pk},
                ),
            )

        messages.success(request=self.request, message="Paper marked ready for publication.")
        return HttpResponseRedirect(
            reverse(
                "wjs_article_details",
                kwargs={"pk": self.object.pk},
            ),
        )


def typesettertestsgalleygeneration_wrapper(
    assignment_id: int,
):
    """Wrap the call to :py:class:`TypesetterTestsGalleyGeneration` to allow for asyn processing."""
    # See also logic__production.finishpublication_wrapper().

    # TODO: review me wrt
    # - wjs.jcom_profile.tests.conftest.fake_request and
    # - utils.management.commands.test_fire_event.create_fake_request

    assignment = get_object_or_404(TypesettingAssignment, pk=assignment_id)
    request = create_fake_request(user=assignment.typesetter, journal=assignment.round.article.journal)

    logic_instance = TypesetterTestsGalleyGeneration(
        assignment=assignment,
        request=request,
    )

    logic_instance.run()


class GalleyGenerationView(BaseRelatedViewsMixin, TemplateView):
    """View to allow the typsetter to generate Galleys."""

    title = _("Generate Galleys")
    model = TypesettingAssignment
    template_name = "wjs_review/details/typesetter_generated_galleys.html"
    error = ""

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])
        self.articleworkflow = self.object.round.article.articleworkflow

    def test_func(self):
        return is_article_typesetter(self.articleworkflow, self.request.user)

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(
                url=reverse("wjs_article_details", kwargs={"pk": self.articleworkflow.pk}), title=self.articleworkflow
            ),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.articleworkflow
        context["error"] = self.error
        return context

    def post(self, request, *args, **kwargs):
        try:
            async_task(typesettertestsgalleygeneration_wrapper, self.kwargs["pk"])
        except Exception as e:
            self.error = e
            return super().get(request, *args, **kwargs)
        messages.success(request=self.request, message=_("Galley generation started."))
        return HttpResponseRedirect(reverse("wjs_article_details", kwargs={"pk": self.articleworkflow.pk}))


class EOSendBackToTypesetterView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    """View to allow the EO to send a paper back to typesetter."""

    form_class = EOSendBackToTypesetterForm
    template_name = "wjs_review/write_message_to_typ.html"
    success_url = reverse_lazy("wjs_review_eo_pending")

    def setup(self, request, *args, **kwargs):
        """Fetch the Article instance for easier processing."""
        super().setup(request, *args, **kwargs)
        self.articleworkflow = get_object_or_404(ArticleWorkflow, id=self.kwargs["pk"])

    def test_func(self):
        """Typesetter can upload files."""
        return base_permissions.has_eo_role(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["articleworkflow"] = self.articleworkflow
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.articleworkflow
        return context

    def form_valid(self, form):
        try:
            # NB: we are not using a ModelForm, so form.save() is not "special" and we must call it explicilty
            form.save()
            return super().form_valid(form)
        except (ValueError, ValidationError) as e:
            form.add_error(None, e)
            return super().form_invalid(form)


class TypesetterTakeInCharge(LoginRequiredMixin, UserPassesTestMixin, View):
    """View to allow the typsetter to take in charge a paper."""

    model = ArticleWorkflow

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, id=self.kwargs["pk"])

    def test_func(self):
        return has_typesetter_role_by_article(self.object, self.request.user)

    def post(self, request, *args, **kwargs):
        """Take the article in charge."""
        try:
            AssignTypesetter(
                article=self.object.article,
                typesetter=self.request.user,
                request=self.request,
            ).run()
        except ValueError as e:
            messages.error(request=self.request, message=e)
            return HttpResponseRedirect(
                reverse(
                    "wjs_review_typesetter_pending",
                ),
            )
        else:
            messages.success(request=self.request, message="Paper taken in charge.")
        return HttpResponseRedirect(
            reverse(
                "wjs_article_details",
                kwargs={"pk": self.object.pk},
            ),
        )


class UpdateSectionOrder(HtmxMixin, LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Issue
    form_class = SectionOrderForm
    template_name = "wjs_review/lists/elements/issue/issue_list.html"

    def test_func(self):
        return base_permissions.has_eo_role(self.request.user) or base_permissions.has_director_role(
            self.request.journal, self.request.user
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["journal"] = self.request.journal
        return kwargs

    def form_valid(self, form: SectionOrderForm) -> HttpResponse:
        """Move sections."""
        form.save()
        return render(self.request, self.template_name, {"issue": self.object, "form": form})

    def form_invalid(self, form):
        return render(self.request, self.template_name, {"issue": self.object, "form": form})


class BeginPublicationView(LoginRequiredMixin, UserPassesTestMixin, View):
    """EO publish a paper."""

    model = ArticleWorkflow

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])
        self.article = self.object.article

    def test_func(self):
        """Only EO can publish."""
        return base_permissions.has_eo_role(self.request.user)

    def post(self, request, *args, **kwargs):
        try:
            self.object = BeginPublication(
                workflow=self.object,
                user=self.request.user,
                request=self.request,
            ).run()
        except ValueError as e:
            messages.error(request=self.request, message=e)
            return HttpResponseRedirect(
                reverse(
                    "wjs_article_details",
                    kwargs={"pk": self.object.pk},
                ),
            )

        messages.success(request=self.request, message=_("Publication process started."))
        return HttpResponseRedirect(self.object.article.url)


class FinishPublicationView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """Finish (or retry) the publication process.

    The second stage might be long (galley generation can last for even a minute) and could crash (most probably for
    some infrastructure temporary issue).

    This view allows an operator to retry the finishing if something went wrong.

    """

    model = ArticleWorkflow

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])
        self.article = self.object.article

    def test_func(self):
        """Only EO can publish."""
        return base_permissions.has_eo_role(self.request.user)

    def post(self, request, *args, **kwargs):
        try:
            async_task(
                finishpublication_wrapper,
                workflow_pk=self.object.pk,
                user_pk=self.request.user.pk,
            )
        except ValueError as e:
            messages.error(request=self.request, message=e)
            return HttpResponseRedirect(
                reverse(
                    "wjs_article_details",
                    kwargs={"pk": self.object.pk},
                ),
            )

        messages.success(request=self.request, message=_("Galley generation started."))
        return HttpResponseRedirect(self.object.article.url)
