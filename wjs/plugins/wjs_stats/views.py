"""Views."""
import calendar
from io import BytesIO

import mariadb
import requests
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Min
from django.http import FileResponse
from django.utils import timezone
from django.views.generic import ListView, TemplateView, View
from identifiers.models import CrossrefStatus
from requests.auth import HTTPBasicAuth
from submission.models import Article
from utils.logger import get_logger

from .plugin_settings import GROUP_ACCOUNTING

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


class DOIsCount(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """Show number of registered DOIs by journal by month."""

    template_name = "wjs_stats/dois_count.html"
    model = CrossrefStatus

    def test_func(self):
        """Only staff or memebers of the "Accounting" group."""
        return self.request.user.is_staff or self.request.user.groups.filter(name=GROUP_ACCOUNTING).exists()

    def get_queryset(self):
        """Group DOIs by their first registration date, as extracted from Crossrefdeposit.document."""
        return CrossrefStatus.objects.filter(
            identifier__article__journal__code=self.request.journal.code,
            identifier__id_type="doi",
            deposits__success=True,
        ).annotate(min_d=Min("deposits__date_time"))

    def get_context_data(self, **kwargs):
        """Add a count of DOIs and published papers per year-month."""
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        result = []
        now = timezone.now()
        oldest_publication_date = (
            Article.objects.filter(
                date_published__isnull=False,
                journal=self.request.journal,
            )
            .order_by("date_published")
            .values_list("date_published", flat=True)[0]
        )

        # I was not able to find the appropriate recipe of filter/annotate/aggregate to extract these infor directly
        # from the DB (see experiments in last comments of specs#428).  Shame on me!
        for year in range(now.year, oldest_publication_date.year - 1, -1):
            for month in range(12, 0, -1):
                if year == now.year and month > now.month:
                    continue
                if year == oldest_publication_date.year and month < oldest_publication_date.month:
                    continue
                # Let's pass along also a "note" indicating when the journal moved to Janeway
                note = ""
                if year == 2023:
                    if month == 3:
                        if self.request.journal.code == "JCOM":
                            note = "JCOM to Janeway"
                    elif month == 5:
                        if self.request.journal.code == "JCOMAL":
                            note = "JCOMAL to Janeway"

                published_papers = Article.objects.filter(
                    journal=self.request.journal,
                    date_published__year=year,
                    date_published__month=month,
                ).count()

                registered_dois = qs.filter(
                    min_d__month=month,
                    min_d__year=year,
                ).count()
                date = timezone.datetime(year, month, 1)
                result.append(
                    {
                        "date": date,
                        "month_end": date.strftime(f"%Y-%m-{calendar.monthrange(date.year, date.month)[1]}"),
                        "year_month": date.strftime("%Y-%m"),
                        "papers": published_papers,
                        "dois": registered_dois,
                        "note": note,
                    },
                )

        context["counts"] = result
        return context
