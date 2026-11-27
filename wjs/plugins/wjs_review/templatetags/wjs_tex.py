import html
import re

from core.models import SupplementaryFile
from django import template
from django.utils.safestring import SafeString, mark_safe
from plugins.wjs_submission.models import CollaborationRelation
from pylatexenc.latexencode import UnicodeToLatexEncoder
from submission.models import Article

register = template.Library()

# Only non-ASCII chars are converted: the titles often contain real LaTeX (e.g. `$H_0$`) that must survive.
# "replace" turns chars unknown to pylatexenc into a marker instead of raising.
_UNICODE_TO_LATEX = UnicodeToLatexEncoder(non_ascii_only=True, unknown_char_policy="replace")
# A %, & or # not already escaped with a backslash.
_UNESCAPED_SPECIALS = re.compile(r"(?<!\\)([%&#])")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


@register.filter
def space_to_tilde(string: str) -> str:
    """Replace spaces with tildes; useful for names and similar."""
    return string.replace(" ", "~")


@register.filter
def latex_safe(string: str | None) -> SafeString:
    r"""
    Make a string with LaTeX markup (e.g. an article title) compile with pdflatex.

    Titles are a mix of plain text, HTML entities and real LaTeX (`$\Lambda$CDM`, `$H_0$`), so they cannot be
    escaped wholesale (that would break the math). Instead:
    - HTML entities are decoded (`&lt;` → `<`);
    - `%`, `&` and `#` not already escaped get a backslash (a bare `%` would comment out the rest of the line);
    - every non-ASCII char becomes its LaTeX macro, wrapped in braces so that it works as a single argument,
      e.g. as a subscript: `$b_φ$` → `$b_{\ensuremath{\varphi}}$`.

    The result is marked safe, otherwise Django's autoescape would turn `'` into `&#x27;` and break the compilation.
    So this filter must only be used in LaTeX templates, never in HTML ones.
    """
    if not string:
        return mark_safe("")
    string = html.unescape(string)
    string = _UNESCAPED_SPECIALS.sub(r"\\\1", string)
    string = _NON_ASCII.sub(lambda match: "{" + _UNICODE_TO_LATEX.unicode_to_latex(match.group()) + "}", string)
    return mark_safe(string)


@register.filter
def collaborations(article: Article) -> list[str]:
    """
    Return the article's list of collaborations names.

    Only consider "by" collaborations because the copyright line should change only for these.
    """
    return article.collaborations.filter(
        relation=CollaborationRelation.BY,
    ).values_list(
        "collaboration__name",
        flat=True,
    )


@register.filter
def size(esm: SupplementaryFile) -> int:
    """Return the file size in bytes of the given supplementary file."""
    return esm.file.get_file_size(Article.objects.get(pk=esm.file.article_id))


@register.filter
def identifier(article: Article, id_type: str) -> str:
    """
    Return the value of the article's identifier of the given type (e.g. "doi", "protonid").

    Return an empty string if the article has no such identifier, so that nothing (not "None") ends up in the
    rendered LaTeX.
    """
    return article.get_identifier(id_type) or ""
