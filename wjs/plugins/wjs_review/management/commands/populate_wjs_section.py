from django.core.management.base import BaseCommand
from plugins.wjs_review.models import WjsSection
from submission.models import Section

from wjs.jcom_profile.constants import (
    JCOM_SECTION_TO_DOISECTIONCODE,
    JCOM_SECTION_TO_PUBIDSECTIONCODE,
)


class Command(BaseCommand):
    help = "Populare wjssection model."  # noqa

    def handle(self, *args, **options):
        sections = Section.objects.all()

        for section in sections:
            WjsSection(
                doi_sectioncode=JCOM_SECTION_TO_DOISECTIONCODE.get(section.name.lower(), None),
                pubid_and_tex_sectioncode=JCOM_SECTION_TO_PUBIDSECTIONCODE.get(section.name.lower(), None),
                section=section,
            ).save_base(raw=True)

            self.stdout.write(self.style.SUCCESS(f"Successfully created wjs_section {section.name}."))
