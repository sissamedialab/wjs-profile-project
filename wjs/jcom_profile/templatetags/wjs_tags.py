"""WJS tags."""
from django import template
from django.db.models import Count
from django.utils import timezone
from django.utils.html import strip_tags

from journal import logic as journal_logic
from journal.models import Issue
from submission.models import Article, Section, Keyword, STAGE_PUBLISHED

from wjs.jcom_profile.models import SpecialIssue
from wjs.jcom_profile.utils import citation_name

register = template.Library()


@register.simple_tag
def journal_has_open_si(journal):
    """Return true if this journal has any special issue open for submission."""
    # The timeline.html template should show/hide the SI step as
    # necessary.
    has_open_si = SpecialIssue.objects.current_journal().open_for_submission().current_user().exists()
    return has_open_si


@register.filter
def keyvalue(dictionary, key):
    """Return the value of dict[key]."""
    return dictionary[key]


@register.filter
def article(article_wrapper):
    """Return the article wrapped by the given article_wrapper."""
    # I don't know why, but simply calling
    # `article_wrapper.janeway_article` results in an error
    # `'ArticleWrapper' object has no attribute 'id'`
    return Article.objects.get(pk=article_wrapper.janeway_article_id)


@register.filter
def has_attr(obj, attr):
    """Return True is the given object has the given attribute.

    Example usage: {% if article|has_attr:"genealogy" %}
    """
    return hasattr(obj, attr)


@register.filter
def how_to_cite(article):
    """Return APA-style how-to-cite for JCOM."""
    # Warning: there exist two `citation_name()`: the original from FrozenAuthor and ours from utils
    author_names = [citation_name(a) for a in article.frozenauthor_set.all()]
    # inelegant...
    if len(author_names) == 1:
        author_str = author_names[0]
    elif len(author_names) == 2:
        author_str = " and ".join(author_names)
    else:
        author_str = ", ".join(author_names[:-1])
        author_str += f" and {author_names[-1]}"
    htc = (
        f"{author_str} ({article.date_published.year})."
        f" {article.title} <i>{article.journal.code}</i>"
        f" {article.issue.volume}({article.issue.issue}), {article.page_numbers}."
        f" https://doi.org/{article.get_doi()}"
    )
    return htc


@register.filter
def news_part(news_item, part):
    """Return the requested part of the new item body by splitting on the first <hr> occurence"""
    parts = news_item.body.partition('<hr>')

    if part == 'abstract':
        if parts[1]:
            return parts[0]
        else:
            return ''
    else:
        if parts[1]:
            return parts[2]
        else:
            return parts[0]


@register.simple_tag(takes_context=True)
def all_issues(context):
    request = context["request"]
    return Issue.objects.filter(
        journal=request.journal,
        date__lte=timezone.now(),
    )


@register.filter
def citation_id(article):
    """Given an Article, returns the meta tag "citation_id" value"""
    return f"{article.issue.volume}/{int(article.issue.issue)}/{article.page_range}"


@register.filter
def description(article):
    """Given an Article, returns the meta tag "description" value"""
    # Strip HTML tags and get at most 320 characters
    shorter_abstract = strip_tags(article.abstract)[:320]
    # To avoid truncated words at the end of the string, drop the characters after the last space
    # This splits shorter_abstract on spaces into words, takes all but the last word, and rejoins them with spaces.
    return " ".join(shorter_abstract.split(" ")[:-1])


@register.simple_tag(takes_context=True)
def sections(context):
    request = context["request"]
    return Section.objects.filter(journal=request.journal, is_filterable=True)


@register.simple_tag(takes_context=True)
def search_form(context):
    request = context["request"]

    keyword_limit = 20
    popular_keywords = Keyword.objects.filter(
        article__journal=request.journal,
        article__stage=STAGE_PUBLISHED,
        article__date_published__lte=timezone.now(),
    ).annotate(articles_count=Count('article')).order_by("-articles_count")[:keyword_limit]

    search_term, keyword, sort, form, redir = journal_logic.handle_search_controls(request)
    return {"form": form, "all_keywords": popular_keywords}
