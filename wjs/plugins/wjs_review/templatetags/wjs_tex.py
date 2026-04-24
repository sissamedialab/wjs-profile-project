from django import template
from plugins.wjs_submission.models import CollaborationRelation
from submission.models import Article

register = template.Library()


@register.filter
def space_to_tilde(string: str) -> str:
    """Replace spaces with tildes; useful for names and similar."""
    return string.replace(" ", "~")


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
