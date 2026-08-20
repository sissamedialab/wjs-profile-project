"""Read the article-to-article relations maintained by the hydra plugin.

Hydra stores relations as ``hydra.LinkedArticle`` rows, to be read as "the
*to-article* is a ``relationship`` of the *from-article*". For instance, an erratum
is the *to-article* of an ``erratum`` relation whose *from-article* is the paper
being corrected.

WJS treats some of these relations as "subordinate": the *to-article* still is a
paper of its own, with its own DOI and landing page, but it is not listed among the
papers of the journal or of the issue; it is shown inside the landing page of its
*from-article* instead. This is the case of commentary sets (an introductory paper
and the invited contributions that comment on it) and of errata & co.

Translations are *not* subordinate: there, the two articles are peers.

This module replaces the old ``wjs.jcom_profile.models.Genealogy`` model.
"""

from django.db.models import QuerySet
from submission.models import Article


def subordinate_link_types() -> set[str]:
    """Return the hydra link types that make the "to-article" a child of the "from-article"."""
    # Imported lazily: hydra is a Janeway plugin, and plugins are not importable
    # while Django is still setting up the app registry.
    from plugins.hydra.models import CROSSREF_UPDATES, LinkType

    return {LinkType.COMMENTARY, *CROSSREF_UPDATES}


def children(article: Article) -> QuerySet[Article]:
    """Return the articles that "hang" from the given one, in their editorial order.

    These are the invited contributions of a commentary set, the errata and addenda
    of a paper, etc.
    """
    return Article.objects.filter(
        linked_to__from_article=article,
        linked_to__relationship__in=subordinate_link_types(),
    ).order_by("linked_to__order", "linked_to__id")


def parent(article: Article) -> Article | None:
    """Return the article that the given one "hangs" from, if any.

    This is the introductory paper of a commentary set, the paper that an erratum
    corrects, etc. An article can have only one parent; if the data says otherwise,
    the first relation wins.
    """
    return (
        Article.objects.filter(
            linked_from__to_article=article,
            linked_from__relationship__in=subordinate_link_types(),
        )
        .order_by("linked_from__order", "linked_from__id")
        .first()
    )


def exclude_children(articles: QuerySet[Article]) -> QuerySet[Article]:
    """Drop from the given queryset the articles that "hang" from another one."""
    return articles.exclude(linked_to__relationship__in=subordinate_link_types())
