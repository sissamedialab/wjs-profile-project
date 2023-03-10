"""WJS tags."""
from django import template
from django.utils import timezone

from journal.models import Issue
from submission.models import Article, Section

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


@register.simple_tag(takes_context=True)
def sections(context):
    request = context["request"]
    return Section.objects.filter(journal=request.journal, is_filterable=True)
