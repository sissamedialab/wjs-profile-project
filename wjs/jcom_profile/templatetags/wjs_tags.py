"""WJS tags."""
from django import template
from submission.models import Article

from wjs.jcom_profile.models import SpecialIssue

register = template.Library()


@register.simple_tag
def journal_has_open_si(journal):
    """Return true if this journal has any special issue open for submission."""
    # The timeline.html template should show/hide the SI step as
    # necessary.
    has_open_si = SpecialIssue.objects.current_journal().open_for_submission().exists()
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
