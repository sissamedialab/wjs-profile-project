"""Views for the synchronization of article metadata between TeX sources and DB."""

from typing import TYPE_CHECKING

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from typesetting.models import TypesettingAssignment

from ..mixins import AuthenticatedUserPassesTest
from ..models import ArticleWorkflow
from ..permissions import is_article_typesetter_or_eo
from .forms import (
    SyncAuthorsForm,
    SyncKeywordsForm,
    SyncLanguageForm,
    SyncLicenseForm,
    SyncTitleAbstractForm,
)
from .logic import MetadataFromTeX

if TYPE_CHECKING:
    from ..custom_types import BreadcrumbItem


class SyncTeXDB(AuthenticatedUserPassesTest, DetailView):
    """View allowing sync of paper metadata between TeX and DB."""

    model = ArticleWorkflow
    title = _("Sync TeX and DB")
    template_name = "wjs_review/sync_texdb/sync_texdb.html"
    context_object_name = "workflow"

    def test_func(self):
        """Only typesetters and EO can do this."""
        self.object = self.get_object()
        return is_article_typesetter_or_eo(self.object, self.request.user)

    @property
    def breadcrumbs(self) -> list["BreadcrumbItem"]:
        from ..custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.object.pk}), title=self.object),
            BreadcrumbItem(url=self.request.path, title=self.title, current=True),
        ]

    def get(self, request, *args, **kwargs):
        """Redirect to article details if no suitable TypesettingAssignment is available."""
        if not self.verify_available_typesetting_assignment():
            return HttpResponseRedirect(reverse("wjs_article_details", kwargs={"pk": self.object.pk}))
        return super().get(request, *args, **kwargs)

    def verify_available_typesetting_assignment(self) -> bool:
        """
        Add a warning if the TeX sources are not taken from the latest production version.

        :return: True if the latest TypesettingAssignment is available, False otherwise
        :rtype: bool
        """
        latest_ta = self.object.get_latest_typesetting_assignment(only_completed=False)
        latest_ta_with_sources = (
            TypesettingAssignment.objects.filter(
                round__article=self.object.article,
                files_to_typeset__isnull=False,
            )
            .order_by("-round__round_number")
            .first()
        )
        if not latest_ta:
            messages.add_message(self.request, messages.ERROR, "Error: not current TypesettingAssignment is available")
            return False
        elif not latest_ta_with_sources:
            messages.add_message(
                self.request, messages.ERROR, "Error: not TypesettingAssignment with sources is available"
            )
            return False
        elif latest_ta != latest_ta_with_sources:
            messages.add_message(
                self.request,
                messages.WARNING,
                f"Warning: you are working on sources from v.{latest_ta_with_sources.round.round_number},"
                f" that is not the lastest version (v.{latest_ta.round.round_number})",
            )
        return True

    def get_texdata(self) -> MetadataFromTeX:
        """Build the TeX metadata holder used to initialize the sync forms."""
        texdata = MetadataFromTeX(self.object)
        # Use the raw data: we only need the extracted values here, not the heavier enriched data.
        texdata.data = texdata.get_raw_data()
        return texdata

    def get_context_data(self, **kwargs):
        """Prepare forms and context for the different blocks of metadata."""
        context = super().get_context_data(**kwargs)
        texdata = self.get_texdata()
        context.update(self._get_context_data_language(texdata))
        context.update(self._get_context_data_license(texdata))
        context.update(self._get_context_data_titleabstract(texdata))
        context.update(self._get_context_data_keywords(texdata))
        context.update(self._get_context_data_authors(texdata))
        return context

    def _get_context_data_language(self, texdata: MetadataFromTeX) -> dict:
        """Return context info related to the language."""
        form_language = SyncLanguageForm(texdata, instance=self.object.article, data={"action": "sync_language"})
        # Validate the form eagerly (the template only reads the cached form.errors):
        # ModelForm._post_clean() copies the (TeX) cleaned value onto the shared article
        # instance in memory, so refresh it here, before the other forms (e.g. the keywords
        # form, which reads article.language) are built with the real DB values.
        form_language.is_valid()
        self.object.article.refresh_from_db()
        return {"form_language": form_language}

    def _get_context_data_license(self, texdata: MetadataFromTeX) -> dict:
        """Return context info related to the license."""
        form_license = SyncLicenseForm(texdata, data={"action": "sync_license"})
        return {"form_license": form_license}

    def _get_context_data_titleabstract(self, texdata: MetadataFromTeX) -> dict:
        """Return context info related to title and abstract."""
        form_titleabstract = SyncTitleAbstractForm(texdata, data={"action": "sync_titleabstract"})
        return form_titleabstract.get_form_context_data()

    def _get_context_data_keywords(self, texdata: MetadataFromTeX) -> dict:
        """Return context info related to the keywords."""
        form_keywords = SyncKeywordsForm(texdata, data={"action": "sync_keywords"})
        return form_keywords.get_form_context_data()

    def _get_context_data_authors(self, texdata: MetadataFromTeX) -> dict:
        """Return context info related to the authors."""
        form_authors = SyncAuthorsForm(texdata, data={"action": "sync_authors"})
        return form_authors.get_form_context_data()

    def get_success_url(self):
        """Point back here."""
        return self.request.path

    def post(self, request, *args, **kwargs):
        """
        Perform the metadata update requested by the submitted form.

        The submitted ``action`` selects which block of metadata to synchronize. The actual
        validate/save logic lives in the form's ``sync()``, which raises ValueError on any
        failure; here we just turn those into user-facing messages.
        """
        self.object = self.get_object()
        action = request.POST.get("action")
        if action == "sync_language":
            self._post_language(request)
        elif action == "sync_license":
            self._post_license(request)
        elif action == "sync_titleabstract":
            self._post_titleabstract(request)
        elif action == "sync_keywords":
            self._post_keywords(request)
        elif action == "sync_authors":
            self._post_authors(request)
        else:
            messages.add_message(request, messages.ERROR, _("No se pol! Come te son rivà qua?!?"))
        return HttpResponseRedirect(self.get_success_url())

    def _post_language(self, request):
        """Synchronize the language."""
        form = SyncLanguageForm(self.get_texdata(), instance=self.object.article, data=request.POST)
        try:
            form.sync()
        except ValueError as e:
            messages.add_message(request, messages.ERROR, str(e))
        else:
            messages.add_message(request, messages.SUCCESS, _("Language synchronized."))

    def _post_license(self, request):
        """Synchronize the license."""
        form = SyncLicenseForm(self.get_texdata(), data=request.POST)
        try:
            form.sync()
        except ValueError as e:
            messages.add_message(request, messages.ERROR, str(e))
        else:
            messages.add_message(request, messages.SUCCESS, _("License synchronized."))

    def _post_titleabstract(self, request):
        """Synchronize title and abstract."""
        form = SyncTitleAbstractForm(self.get_texdata(), data=request.POST)
        try:
            form.sync()
        except ValueError as e:
            messages.add_message(request, messages.ERROR, str(e))
        else:
            messages.add_message(request, messages.SUCCESS, _("Title and abstract synchronized."))

    def _post_keywords(self, request):
        """Synchronize the keywords."""
        # NB: the form construction is inside the try because __init__ raises ValueError
        # when a kwd from the TeX does not exist in the DB.
        try:
            form = SyncKeywordsForm(self.get_texdata(), data=request.POST)
            form.sync()
        except ValueError as e:
            messages.add_message(request, messages.ERROR, str(e))
        else:
            messages.add_message(request, messages.SUCCESS, _("Keywords synchronized."))

    def _post_authors(self, request):
        """Synchronize the authors."""
        form = SyncAuthorsForm(self.get_texdata(), data=request.POST)
        try:
            form.sync()
        except ValueError as e:
            messages.add_message(request, messages.ERROR, str(e))
        else:
            messages.add_message(request, messages.SUCCESS, _("Authors synchronized."))
