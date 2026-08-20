"""Move the parent/children relations from Genealogy to the hydra plugin.

Genealogy stored one row per "parent" article, with an ordered m2m to its children.
Hydra stores one row per relation, qualified by a "relationship" and ordered by an
explicit `order` field.

The relationship is derived from the section of the *child*:

- children in section "Erratum" or "Addendum" become editorially significant relations
  (they end up in the Crossref deposit as `<update type="erratum">` & co., see
  submission.models.Article.update_of);
- everything else becomes a "commentary": an introductory paper gathering a number of
  invited contributions. This covers the JCOM commentary sets and also the two "funny"
  container articles of issue 13(03) 2014 (JCOM_1303_2014_RCR "Conference Review" and
  JCOM_1303_2014_RBR "Book Review"), which are empty articles whose only purpose is to
  group the reviews of that issue. They are migrated as-is; what to do with them is a
  separate, editorial decision.
"""

from django.db import migrations

# Values of plugins.hydra.models.LinkType.
# Spelled out (instead of imported) as usual for migrations, so that this file keeps
# working even if the plugin renames things.
COMMENTARY = "commentary"
ERRATUM = "erratum"
ADDENDUM = "addendum"

#: Section name (lowercased) of the child ➞ relationship to store in hydra
SECTION_TO_RELATIONSHIP = {
    "erratum": ERRATUM,
    "addendum": ADDENDUM,
}

#: Relationships that this migration knows how to convert back into a Genealogy
SUBORDINATE = (COMMENTARY, ERRATUM, ADDENDUM)


def genealogy_to_hydra(apps, schema_editor):
    """Create one hydra LinkedArticle per Genealogy parent/child couple."""
    Genealogy = apps.get_model("jcom_profile", "Genealogy")
    LinkedArticle = apps.get_model("hydra", "LinkedArticle")

    # The sortedm2m "through" model; `sort_value` is the position of the child
    Children = Genealogy.children.through

    links = []
    for row in Children.objects.select_related("article__section").order_by("genealogy_id", "sort_value"):
        section_name = row.article.section.name if row.article.section else ""
        links.append(
            LinkedArticle(
                from_article_id=row.genealogy.parent_id,
                to_article_id=row.article_id,
                relationship=SECTION_TO_RELATIONSHIP.get(section_name.lower(), COMMENTARY),
                order=row.sort_value,
            ),
        )
    LinkedArticle.objects.bulk_create(links, ignore_conflicts=True)


def hydra_to_genealogy(apps, schema_editor):
    """Rebuild the Genealogy rows from the hydra relations."""
    Genealogy = apps.get_model("jcom_profile", "Genealogy")
    LinkedArticle = apps.get_model("hydra", "LinkedArticle")
    Children = Genealogy.children.through

    genealogies = {}
    children = []
    for link in LinkedArticle.objects.filter(relationship__in=SUBORDINATE).order_by("from_article_id", "order", "id"):
        if link.from_article_id not in genealogies:
            genealogies[link.from_article_id] = Genealogy.objects.create(parent_id=link.from_article_id)
        children.append(
            Children(
                genealogy=genealogies[link.from_article_id],
                article_id=link.to_article_id,
                sort_value=link.order,
            ),
        )
    Children.objects.bulk_create(children, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("jcom_profile", "0041_jcomprofile_career_stage"),
        # Provides LinkedArticle.order and the "commentary" link type
        ("hydra", "0002_commentary_link_type_and_order"),
    ]

    operations = [
        migrations.RunPython(genealogy_to_hydra, reverse_code=hydra_to_genealogy),
    ]
