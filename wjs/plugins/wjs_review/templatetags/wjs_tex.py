from django import template
from submission.models import Article

register = template.Library()


@register.filter
def space_to_tilde(string: str) -> str:
    """Replace spaces with tildes; useful for names and similar."""
    return string.replace(" ", "~")


@register.filter
def collaborations(article: Article) -> list[str]:
    """Return the article's list of collaborations names."""
    return article.collaborations.all().values_list("collaboration__name", flat=True)
