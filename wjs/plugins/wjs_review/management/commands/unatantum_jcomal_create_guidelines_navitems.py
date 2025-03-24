"""
Create the Guidelines nav-tree.

See specs#1313.

Expected result:
       link_name        |      link       | seq |     link_name_en      |      link_name_es       |   link_name_pt
------------------------+-----------------+-----+-----------------------+-------------------------+--------------------
Directrices             |                 |  20 | Guidelines            | Directrices             | Instruções
Para todos los usuarios | /site/all-roles |   1 | For all roles         | Para todos los usuarios | Para todos
Para autores            | /site/authors   |  10 | For Authors           | Para autores            | Para os autores
Para editores           | /site/editors   |  20 | For Editors in charge | Para editores           | Para os editores
Para revisores          | /site/reviewers |  30 | For Reviewers         | Para revisores          | Para os revisores

"""

from cms.models import NavigationItem
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from journal.models import Journal


class Command(BaseCommand):
    help = __file__.__doc__  # noqa A003

    def handle(self, *args, **options):
        jcomal = Journal.objects.get(code="JCOMAL")
        journal_ct = ContentType.objects.get_for_model(Journal)
        guidelines = NavigationItem.objects.create(
            link_name="Guidelines",
            link_name_en="Guidelines",
            link_name_es="Directrices",
            link_name_pt="Instruções",
            sequence=20,
            content_type=journal_ct,
            object_id=jcomal.id,
            has_sub_nav=True,
            is_external=False,
            for_footer=False,
            extend_to_journals=False,
        )
        NavigationItem.objects.create(
            link="/site/all-roles",
            link_name="For all roles",
            link_name_en="For all roles",
            link_name_es="Para todos los usuarios",
            link_name_pt="Para todos",
            sequence=20,
            content_type=journal_ct,
            object_id=jcomal.id,
            has_sub_nav=False,
            top_level_nav=guidelines,
            is_external=False,
            for_footer=False,
            extend_to_journals=False,
        )
        NavigationItem.objects.create(
            link="/site/authors",
            link_name="For Authors",
            link_name_en="For Authors",
            link_name_es="Para autores",
            link_name_pt="Para os autores",
            sequence=10,
            content_type=journal_ct,
            object_id=jcomal.id,
            has_sub_nav=False,
            top_level_nav=guidelines,
            is_external=False,
            for_footer=False,
            extend_to_journals=False,
        )
        NavigationItem.objects.create(
            link="/site/editors",
            link_name="For Editors in charge",
            link_name_en="For Editors in charge",
            link_name_es="Para editores",
            link_name_pt="Para os editores",
            sequence=20,
            content_type=journal_ct,
            object_id=jcomal.id,
            has_sub_nav=False,
            top_level_nav=guidelines,
            is_external=False,
            for_footer=False,
            extend_to_journals=False,
        )
        NavigationItem.objects.create(
            link="/site/reviewers",
            link_name="For Reviewers",
            link_name_en="For Reviewers",
            link_name_es="Para revisores",
            link_name_pt="Para os revisores",
            sequence=30,
            content_type=journal_ct,
            object_id=jcomal.id,
            has_sub_nav=False,
            top_level_nav=guidelines,
            is_external=False,
            for_footer=False,
            extend_to_journals=False,
        )

        # Guidelines have sense only if submissions are enabled:
        # also create the "submission" item:
        NavigationItem.objects.create(
            link="/submissions",
            link_name="Submit a paper",
            link_name_en="Submit a paper",
            link_name_es="Enviar um artigo",
            link_name_pt="Submissão de manuscritos",
            sequence=99,
            content_type=journal_ct,
            object_id=jcomal.id,
            has_sub_nav=False,
            is_external=False,
            for_footer=False,
            extend_to_journals=False,
        )
