from core.models import SupplementaryFile
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


@register.filter
def size(esm: SupplementaryFile) -> int:
    """Return the file size in bytes of the given supplementary file."""
    return esm.file.get_file_size(Article.objects.get(pk=esm.file.article_id))
