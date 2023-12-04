"""Views."""
from io import BytesIO

import mariadb
import requests
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import FileResponse
from django.utils import timezone
from django.views.generic import TemplateView, View
from requests.auth import HTTPBasicAuth
from utils.logger import get_logger

# TODO: add specific permission to plugin and use PermissionRequiredMixin?
logger = get_logger(__name__)


class Manager(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Just an index."""

    template_name = "wjs_stats/index.html"

    def test_func(self):
        """Verify that only staff can see statistics."""
        return self.request.user.is_staff


class StatsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Prova."""

    template_name = "wjs_stats/wjs_stats.html"

    def test_func(self):
        """Verify that only staff can see statistics."""
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        """Collect things that you want to display in the template."""
        context = super().get_context_data(**kwargs)

        # NB: we now store credentials to other DBs as non-django variables, but in future we might want to add to the
        # DATABASES dictionary.

        # An entry for a journal should look like:
        # ... WJAPP_JCOM_CONNECTION_PARAMS = {
        # ...     "user": "ro-user",
        # ...     "password": "***",
        # ...     "host": "kisman",
        # ...     "database": "wjJcomDb",
        # ... }
        setting = "WJAPP_JCOM_CONNECTION_PARAMS"
        connection_parameters = getattr(settings, setting, None)
        if connection_parameters is None:
            logger.error(f"Missing connection parameters {setting}. Please check core.settings.")
            return context
        connection = mariadb.connect(**connection_parameters)
        cursor = connection.cursor(dictionary=True)

        # JCOM submitted papers this year
        this_year = timezone.now().year
        cursor.execute(
            "select count(*) as count from Document where year(submissionDate) = ?",
            (this_year,),
        )
        row = cursor.fetchone()
        # NB: the keys of the "context" dictionary are directly accessible from the view template!
        context["jcom_papers_submitted_this_year"] = row["count"]

        return context


class MuninProxy(LoginRequiredMixin, UserPassesTestMixin, View):
    """Proxy to (internal) machine to retrieve images of munin graphs."""

    def test_func(self):
        """Verify that only staff can request a proxy to munin."""
        return self.request.user.is_staff

    def get(self, request, **kwargs):
        """Serve the requested image."""
        server = kwargs["server"]
        image = kwargs["image"]
        img_url = (
            "https://medialab.sissa.it/munin-cgi/munin-cgi-graph/ud.sissamedialab.it/"
            f"{server}.ud.sissamedialab.it/{image}.png"
        )

        if auth := settings.WJS_MUNIN_AUTH:
            basic_auth = HTTPBasicAuth(*auth)
        else:
            basic_auth = None

        munin_response = requests.get(
            url=img_url,
            verify=True,
            auth=basic_auth,
        )
        response = FileResponse(BytesIO(munin_response.content))
        return response


class RecipientsCount(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Show a monthly and yearly count of newsletter recipients."""

    template_name = "wjs_stats/recipients_count.html"

    def test_func(self):
        """Verify that only staff can see recipients count."""
        return self.request.user.is_staff
