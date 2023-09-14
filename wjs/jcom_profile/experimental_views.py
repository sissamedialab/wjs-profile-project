"""Experimental views."""
import dataclasses
from datetime import date
from itertools import combinations

from core.models import Account
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import now
from django.views.generic import TemplateView
from journal.models import Issue, IssueType
from submission.models import Article, Keyword

# ~Jaal~ Some library allows only 20 categories for the color. I'll keep only 18
# countries plus NA and Other...
COUNTRIES = {
    "US": "United States",
    "IE": "Ireland",
    "GB": "United Kingdom",
    "IT": "Italy",
    "PH": "Philippines",
    "DE": "Germany",
    "NL": "Netherlands",
    "CN": "China",
    "CA": "Canada",
    "IN": "India",
    "PT": "Portugal",
    "AT": "Australia",
    "ES": "Spain",
    "BR": "Brazil",
    "RU": "Russian Federation",
    None: "NA",
}


class IssuesForceGraph(TemplateView):
    """Display issues with DS3.js ForceGraph."""

    template_name = "experimental/journal/issues.html"

    # TODO: how do I apply Janeway's function decorators to class-based views?
    # @has_journal       from security.decorators
    # @frontend_enabled  from journal.decorators
    def get_context_data(self, **kwargs):
        """Get the list of issues.

        Same as journal.views.issues
        """
        context = super().get_context_data(**kwargs)
        issue_type = IssueType.objects.get(
            code="issue",
            journal=self.request.journal,
        )
        issue_objects = Issue.objects.filter(
            journal=self.request.journal,
            issue_type=issue_type,
            date__lte=timezone.now(),
        )
        context = {
            "issues": issue_objects,
            "issue_type": issue_type,
        }
        return context


@dataclasses.dataclass
class AuthorNode:
    author: Account
    num_papers: int = 0

    @property
    def pk(self):
        return self.author.id

    @property
    def name(self):
        return self.author.full_name().replace(",", "")

    @property
    def group(self):
        country = self.author.country
        if country is not None:
            country = COUNTRIES.get(country.code, "Others")
        else:
            country = "NA"
        return f"{self.author.full_name().replace(',','')},{country:.<15},{self.num_papers**2}"

    @property
    def url(self):
        return reverse("articles_by_author", kwargs={"author": self.author.id})


@dataclasses.dataclass
class ArticleNode:
    article: Article
    num_kwds: int = 0

    @property
    def pk(self):
        return self.article.id

    @property
    def name(self):
        return self.article.title[:29] + "…"

    @property
    def group(self):
        return self.article.keywords.first().word

    @property
    def url(self):
        return self.article.url


@dataclasses.dataclass
class Edge:
    source: Account  # must match with type of Node.id_
    target: Account  # must match with type of Node.id_
    weigth: int = 0


class AuthorsForceGraph(TemplateView):
    """Display authors with DS3.js ForceGraph."""

    template_name = "experimental/journal/authors_kg.html"

    def get_context_data(self, **kwargs):
        """Get the journal's authors.

        And structure them into nodes/links (edges) suitable for D3.
        """
        context = super().get_context_data(**kwargs)

        nodes = {}
        edges = {}
        articles = Article.objects.filter(
            journal=self.request.journal,
            date_published__lte=now(),
        )
        for article in articles:
            # Node - authors
            for author in article.authors.all():
                node = nodes.setdefault(
                    author.pk,
                    AuthorNode(
                        author=author,
                        num_papers=0,
                    ),
                )
                node.num_papers += 1

            # Edges / links
            for source, target in combinations(article.authors.all(), 2):
                edge = edges.setdefault(
                    f"{source.id}-{target.id}",
                    Edge(source, target, weigth=0),
                )
                edge.weigth += 1

        context = {
            "authors": nodes.values(),
            "links": edges.values(),
            "nodeStrength": -30,
            "linkStrength": 1,
        }
        return context


class AuthorsKeywordsForceGraph(TemplateView):
    """Display authors related to each other by kwds with DS3.js ForceGraph."""

    template_name = "experimental/journal/authors_kg.html"

    def get_context_data(self, **kwargs):
        """Get the journal's authors.

        And structure them into nodes/links (edges) suitable for D3.
        """
        context = super().get_context_data(**kwargs)

        nodes = {}
        edges = {}

        # TODO: drop the limit of only few kwds if you want to fry an egg on your CPU!
        for kwd in Keyword.objects.filter(journal=self.request.journal)[:3]:
            authors_of_kwd_x = Account.objects.filter(
                article__keywords__in=[kwd],
                article__journal=self.request.journal,
            )
            # Node - authors
            for author in authors_of_kwd_x:
                node = nodes.setdefault(
                    author.pk,
                    AuthorNode(
                        author=author,
                        num_papers=0,
                    ),
                )
                node.num_papers += 1
            # Edges / links
            for source, target in combinations(authors_of_kwd_x, 2):
                edge = edges.setdefault(
                    f"{source.id}-{target.id}",
                    Edge(source, target, weigth=0),
                )
                edge.weigth += 1

        context = {
            "authors": nodes.values(),
            "links": edges.values(),
            "nodeStrength": -100,
            "linkStrength": 0.01,
        }
        return context


class ArticlesByKeywordForceGraph(TemplateView):
    """Display articles related to each other by kwds with DS3.js ForceGraph."""

    template_name = "experimental/journal/authors_kg.html"

    def get_context_data(self, **kwargs):
        """Get the journal's articles.

        And structure them into nodes/links (edges) suitable for D3.
        """
        context = super().get_context_data(**kwargs)

        nodes = {}
        edges = {}
        start_year = 2022
        articles = Article.objects.filter(
            journal=self.request.journal,
            date_published__lte=now(),
            date_published__gte=date(start_year, 1, 1),
        )
        for article in articles:
            # Nodes - articles
            nodes.setdefault(
                article.pk,
                ArticleNode(
                    article=article,
                    num_kwds=article.keywords.count(),
                ),
            )

        for kwd in Keyword.objects.filter(journal=self.request.journal):
            # Edges / links
            # TODO: check ...annotate(year=TruncYear("date_published", output_field=DateField())...
            articles_of_kwd_x = Article.objects.filter(
                keywords__in=[kwd],
                journal=self.request.journal,
                date_published__gte=date(start_year, 1, 1),
            )
            for source, target in combinations(articles_of_kwd_x, 2):
                edge = edges.setdefault(
                    f"{source.id}-{target.id}",
                    Edge(source, target, weigth=0),
                )
                edge.weigth += 1

        context = {
            "authors": nodes.values(),
            "links": edges.values(),
            "nodeStrength": -500,
            "linkStrength": 0.1,
        }
        return context
