"""WJS tags."""

import pycountry
from core.models import Account
from django import template
from django.template import Context, Template
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from journal.models import Issue

from wjs.jcom_profile.permissions import has_eo_or_director_role, has_eo_role
from wjs.jcom_profile.utils import citation_name

register = template.Library()


@register.filter
def keyvalue(dictionary, key):
    """Return the value of dict[key]."""
    return dictionary[key]


@register.filter
def concat(base_string, suffix):
    """Concatenate two strings (non string items will be casted to strings)."""
    return f"{base_string}{suffix}"


@register.filter
def article_has_children(article):
    """Return if article has children articles (commentary items)."""
    try:
        return article.genealogy.children.exists()
    except AttributeError:
        return False


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
    if not article.frozenauthor_set.exists():
        return ""
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
def authors_fullname_comma_and(article):
    """Return authors fullname separated by comma and and.

    E.g.:
    - case empty sting (incomplete article data)
    - by Mario Rossi
    - by Mario Rossi and Maria Rosa
    - by Mario Rossi, Maria Rosa and Paolo Verdi
    """
    tr_begin = _("by")
    author_str = f"{tr_begin} "
    author_names = [fz.full_name() for fz in article.frozen_authors().order_by("order")]
    tr_sep = _("and")
    if not author_names:
        return ""
    if len(author_names) == 1:
        author_str += author_names[0]
    elif len(author_names) == 2:
        author_str += f" {tr_sep} ".join(author_names)
    else:
        author_str += ", ".join(author_names[:-1])
        author_str += f" {tr_sep} {author_names[-1]}"
    return author_str


@register.filter
def news_part(news_item, part):
    """Return the requested part of the new item body by splitting on the first <hr> occurence"""
    parts = news_item.body.partition("<hr>")

    if part == "abstract":
        if parts[1]:
            return parts[0]
        else:
            return ""
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


@register.filter
def language_alpha2(alpha_3):
    """Transform alpha3 language codes to alpha2 language codes."""
    lang_obj = pycountry.languages.get(alpha_3=alpha_3.upper())
    return lang_obj.alpha_2


@register.filter
def language_alpha3(alpha_2):
    """Transform alpha2 language codes to alpha3 language codes."""
    lang_obj = pycountry.languages.get(alpha_2=alpha_2.upper())
    return lang_obj.alpha_3


@register.filter
def display_title(issue: Issue | None, use_short=False) -> str:
    """Return a translatable display_title for issues."""
    if not issue:
        return ""
    if issue.issue_type.code == "collection":
        volume, issue_number, year, issue_title, *__ = issue.issue_title_parts()
        if use_short and issue.short_name:
            title = issue.short_name
        elif issue.short_name:
            title = f"{issue_title} ({issue.short_name})"
        else:
            title = issue_title
        template = Template(
            " &bull; ".join((volume, issue_number, year, title)),
        )
        return mark_safe(template.render(Context()))
    return mark_safe(issue.update_display_title(save=False))


@register.filter
def internal_title(issue: Issue | None) -> str:
    """Return a translatable display_title for issues."""
    if not issue:
        return ""
    if issue.issue_type.code == "collection" and issue.short_name:
        return issue.short_name
    return mark_safe(issue.update_display_title(save=False))


@register.filter
def get_issue_meta_image_url(issue):
    """For issues, return the image to use for Facebook & co."""
    if issue.cover_image:
        return issue.cover_image.url
    elif issue.large_image:
        return issue.large_image.url
    else:
        return ""


@register.filter
def is_user_eo(user: Account) -> bool:
    """Returns if user is part of the EO."""
    return has_eo_role(user)


@register.simple_tag()
def user_has_eo_role(user: Account) -> bool:
    """Returns if user is part of the EO."""
    return has_eo_role(user)


@register.simple_tag(takes_context=True)
def user_has_eo_director_role(context, user: Account) -> bool:
    """Returns if user is part of the EO."""
    if not user.is_authenticated:
        return False
    return has_eo_or_director_role(context["request"].journal, user)


@register.filter
def preprintid(article):
    """Given an Article, returns the preprintid or empty string"""
    if preprintid := article.get_identifier("preprintid"):
        return preprintid
    else:
        return ""


@register.filter
def is_cfp(news):
    """Returns if the CFP is open for the journal."""
    return news.tags.filter(text="call").exists()


@register.filter
def is_archived_cfp(news):
    """Returns if the CFP is open for the journal."""
    return news.tags.filter(text="archived-calls").exists()
