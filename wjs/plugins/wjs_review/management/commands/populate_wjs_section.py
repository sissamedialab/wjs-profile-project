from django.core.management.base import BaseCommand
from plugins.wjs_review.models import WjsSection
from submission.models import Section

# In JCOM (and JCOMAL), the DOI depends on a code that depends on the section (article type).
# This is peculiar of JCOM and does not apply to other journals.
JCOM_SECTION_TO_DOISECTIONCODE = {
    "letter": "01",
    "article": "02",
    "commentary": "03",
    "essay": "04",
    "editorial": "05",
    "conference review": "06",
    "book review": "07",
    "practice insight": "08",
    "focus": "09",  # Warning: focus and review article have the same code!!!
    "review article": "09",  # Probably not important: no focus for many years (as of 2023)!
}
JCOM_SECTION_TO_PUBIDSECTIONCODE = {
    "letter": "L",
    "article": "A",
    "commentary": "C",
    "essay": "Y",
    "editorial": "E",
    "conference review": "R",  # Same code for conference and book review
    "book review": "R",  # Same code for conference and book review
    "practice insight": "N",
    "focus": "F",
    "review article": "V",
}


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
