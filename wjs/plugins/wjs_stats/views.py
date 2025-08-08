"""Views."""

import calendar
from collections import namedtuple
from datetime import timedelta
from io import BytesIO

import mariadb
import requests
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Min, Q
from django.http import FileResponse
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.timezone import now
from django.views.generic import ListView, TemplateView, View
from django.views.generic.edit import FormView
from identifiers.models import CrossrefStatus
from journal.models import Issue, Journal
from plugins.typesetting.models import TypesettingAssignment
from requests.auth import HTTPBasicAuth
from submission.models import Article, Section
from utils.logger import get_logger

from wjs.jcom_profile import constants

from .forms import FilterForm
from .plugin_settings import GROUP_ACCOUNTING

Account = get_user_model()

# TODO: add specific permission to plugin and use PermissionRequiredMixin?
logger = get_logger(__name__)

try:
    from plugins.wjs_review.models import ArticleWorkflow
except ImportError:
    logger.warning("Plugin wjs-review not installed. Some stats not available; some links might break!")


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


class BaseStatsFormView(FormView):
    """Base for stats views."""

    template_name = "wjs_stats/articles.html"
    form_class = FilterForm
    success_url = reverse_lazy("wjs_stats_articles")

    def get_form(self, form_class=None):
        if form_class is None:
            form_class = self.get_form_class()
        form = super().get_form(form_class)

        # Select only issues from this journal
        form.fields["issues"].queryset = Issue.objects.filter(journal=self.request.journal)
        return form

    def get_initial(self):
        # Set initial data for the form
        initial = super().get_initial()
        initial["from_date"] = now().date() - timedelta(days=30)
        initial["to_date"] = now().date()
        try:
            initial["issues"] = Issue.objects.latest("id")
        except Issue.DoesNotExist:
            initial["issues"] = None
        return initial


class TypesettersStatsView(BaseStatsFormView):
    """Stats about typesetters."""

    template_name = "wjs_stats/typesetters.html"

    def form_valid(self, form):
        """Get stats for typesetters."""
        from_date = form.cleaned_data["from_date"]
        to_date = form.cleaned_data["to_date"]
        issues = form.cleaned_data["issues"]

        Result = namedtuple("Result", ["typesetter", "tic", "uploaded", "published"])
        results = []

        typesetters = Account.objects.filter(
            accountrole__journal=self.request.journal,
            accountrole__role__slug=constants.TYPESETTER_ROLE,
        )
        for typesetter in typesetters:
            # ### Taken in charge
            query = TypesettingAssignment.objects.filter(
                typesetter=typesetter,
                assigned__gte=from_date,
                assigned__lt=to_date,
                round__round_number=1,
            )
            if issues:
                query = query.filter(round__article__primary_issue__in=issues)
            tic = query.count()

            # ### Uploaded
            query = TypesettingAssignment.objects.filter(
                typesetter=typesetter,
                completed__gte=from_date,
                completed__lt=to_date,
            )
            if issues:
                query = query.filter(round__article__primary_issue__in=issues)
            uploaded = query.count()

            # ### Published
            query = TypesettingAssignment.objects.filter(
                typesetter=typesetter,
                round__article__date_published__gte=from_date,
                round__article__date_published__lt=to_date,
            ).distinct()
            if issues:
                query = query.filter(round__article__primary_issue__in=issues)
            published = query.count()

            results.append(Result(typesetter.full_name, tic, uploaded, published))

        context = self.get_context_data(form=form)
        context["results"] = results
        return self.render_to_response(context)


class BaseCounter:
    """Base class for article counters (submitted, accepted, ...)."""

    def __init__(self, form: forms.Form, journal: Journal):
        """Extract data from the form."""
        self.from_date = form.cleaned_data["from_date"]
        self.to_date = form.cleaned_data["to_date"]
        self.issues = form.cleaned_data["issues"]
        self.journal = journal

    def __call__():
        raise NotImplementedError()


class CounterSubmitted(BaseCounter):
    """Submitted articles.

    Use the date when the submission "finished" (i.e. date_submitted, not date_start).
    """

    def __call__(self):
        queryset = Section.objects.filter(journal=self.journal)
        filters = Q(article__date_submitted__gte=self.from_date) & Q(article__date_submitted__lt=self.to_date)
        # Warning: don't apply the filters to the queryset, because you'll loose sections that have no paper; i.e. your
        # results list will be shorter.

        if self.issues:
            filters &= Q(article__primary_issue__in=self.issues.values_list("pk", flat=True))

        # This does the "group-by"
        queryset = queryset.annotate(articles_count=Count("article", filter=filters))

        # keep the total at the beginning of our results list
        result = [
            0,
        ]
        for section in queryset.order_by("name"):
            result[0] = result[0] + section.articles_count
            result.append(section.articles_count)

        return result


class CounterAccepted(BaseCounter):
    """Accepted papers."""

    def __call__(self):
        queryset = Section.objects.filter(journal=self.journal)
        filters = (
            Q(article__articleworkflow__decisions__decision=ArticleWorkflow.Decisions.ACCEPT)
            # TBV: is is possible that a "decision" is created one day and modified another day?
            & Q(article__articleworkflow__decisions__created__gte=self.from_date)
            & Q(article__articleworkflow__decisions__created__lt=self.to_date)
        )
        if self.issues:
            filters &= Q(article__primary_issue__in=self.issues)

        queryset = queryset.annotate(articles_count=Count("article", filter=filters))

        result = [0]
        for section in queryset.order_by("name"):
            result[0] = result[0] + section.articles_count
            result.append(section.articles_count)

        return result


class CounterPublished(BaseCounter):
    """Published papers."""

    def __call__(self):
        queryset = Section.objects.filter(journal=self.journal)
        filters = (
            Q(article__date_published__gte=self.from_date)
            & Q(article__date_published__lt=self.to_date)
            & Q(journal=self.journal)
        )
        if self.issues:
            filters &= Q(article__primary_issue__in=self.issues)

        queryset = queryset.annotate(
            articles_count=Count(
                "article",
                filter=filters,
            )
        )

        result = [0]
        for section in queryset.order_by("name"):
            result[0] = result[0] + section.articles_count
            result.append(section.articles_count)

        return result


class CounterRejected(BaseCounter):
    """Rejected papers."""

    def __call__(self):
        queryset = Section.objects.filter(journal=self.journal)
        filters = (
            Q(article__articleworkflow__decisions__decision=ArticleWorkflow.Decisions.REJECT)
            & Q(article__articleworkflow__decisions__created__gte=self.from_date)
            & Q(article__articleworkflow__decisions__created__lt=self.to_date)
        )
        if self.issues:
            filters &= Q(article__primary_issue__in=self.issues)

        queryset = queryset.annotate(articles_count=Count("article", filter=filters))

        result = [0]
        for section in queryset.order_by("name"):
            result[0] = result[0] + section.articles_count
            result.append(section.articles_count)

        return result


class CounterNotSuitable(BaseCounter):
    """Not-suitable papers."""

    def __call__(self):
        queryset = Section.objects.filter(journal=self.journal)
        filters = (
            Q(article__articleworkflow__decisions__decision=ArticleWorkflow.Decisions.NOT_SUITABLE)
            & Q(article__articleworkflow__decisions__created__gte=self.from_date)
            & Q(article__articleworkflow__decisions__created__lt=self.to_date)
        )
        if self.issues:
            filters &= Q(article__primary_issue__in=self.issues)

        queryset = queryset.annotate(articles_count=Count("article", filter=filters))

        result = [0]
        for section in queryset.order_by("name"):
            result[0] = result[0] + section.articles_count
            result.append(section.articles_count)

        return result


class CounterWithdrawn(BaseCounter):
    """Withdrawn papers."""

    def __call__(self):
        queryset = Section.objects.filter(journal=self.journal)
        filters = (
            Q(article__articleworkflow__state=ArticleWorkflow.ReviewStates.WITHDRAWN)
            & Q(article__articleworkflow__latest_state_change__gte=self.from_date)
            & Q(article__articleworkflow__latest_state_change__lt=self.to_date)
        )
        if self.issues:
            filters &= Q(article__primary_issue__in=self.issues)

        queryset = queryset.annotate(articles_count=Count("article", filter=filters))

        result = [0]
        for section in queryset.order_by("name"):
            result[0] = result[0] + section.articles_count
            result.append(section.articles_count)

        return result


class CounterPending(BaseCounter):
    """Pending papers."""

    def __call__(self):
        # TODO: postponed to specs#1106
        # - date_created >= from
        # - decision != revision-request && decisions__created < to
        # - not withdrawn ??? state == withdrawn && latest_state_change >= to
        return ["NA"] * (Section.objects.filter(journal=self.journal).count() + 1)


class ArticlesStatsView(BaseStatsFormView):
    """Stats about articles."""

    def form_valid(self, form):
        counts = {}
        # TODO: see tables in https://gitlab.sissamedialab.it/wjs/specs/-/issues/644#note_28419

        counts["submitted"] = CounterSubmitted(form, self.request.journal)()
        counts["accepted"] = CounterAccepted(form, self.request.journal)()
        counts["published"] = CounterPublished(form, self.request.journal)()
        counts["rejected"] = CounterRejected(form, self.request.journal)()
        counts["not suitable"] = CounterNotSuitable(form, self.request.journal)()
        counts["withdrawn"] = CounterWithdrawn(form, self.request.journal)()
        counts["pending"] = CounterPending(form, self.request.journal)()

        context = self.get_context_data(form=form)
        context["counts"] = counts
        # build a list of section names e.g. ["article", "book review", ...]
        # NB: the order is the same as that used in the queries
        sections = Section.objects.filter(journal=self.request.journal).order_by("name").values_list("name", flat=True)
        context["sections"] = ["tot", *sections]

        # include a reference to each issue selected, so that the web page can point to the issue
        context["issues"] = Issue.objects.filter(id__in=form.cleaned_data["issues"])
        return self.render_to_response(context)


class DoubleAccountsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Lists accounts with the same first and last name."""

    template_name = "wjs_stats/glitches.html"

    def test_func(self):
        """Verify that only staff can see this."""
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        """Find accounts with the same first and last name."""
        context = super().get_context_data(**kwargs)
        double_names = (
            Account.objects.values("first_name", "last_name")
            .order_by()
            .annotate(count=Count("pk"))
            .filter(count__gt=1)
            .exclude(first_name__in=["", "hidden"], last_name__in=["", "user"])
        )
        groups = []
        for name_group in double_names:
            accounts = Account.objects.filter(
                first_name=name_group["first_name"],
                last_name=name_group["last_name"],
            ).order_by("pk")
            groups.append(accounts)

        context["double_groups"] = groups
        return context
