"""Create Erratum and Addendum sections with WjsSection records.

This management command creates the Section and WjsSection records needed
for the erratum/addendum submission workflow (issue #2873).

Usage:
    python manage.py populate_correction_sections

It creates sections for all journals, or for a specific journal if --journal is given.
"""

from django.core.management.base import BaseCommand
from journal.models import Journal
from plugins.wjs_review.models import WjsSection
from submission.models import Section

from wjs.jcom_profile.constants import JCOM_SECTION_TO_PUBIDSECTIONCODE

# Section definitions for corrections.
CORRECTION_SECTIONS = [
    {
        "name": "Erratum",
        "pubid_code": JCOM_SECTION_TO_PUBIDSECTIONCODE.get("erratum", "X"),
        "description": "Errata correct errors in previously published articles.",
    },
    {
        "name": "Addendum",
        "pubid_code": JCOM_SECTION_TO_PUBIDSECTIONCODE.get("addendum", "Z"),
        "description": "Addenda add additional information to previously published articles.",
    },
]


class Command(BaseCommand):
    """Create Erratum and Addendum sections with WjsSection pubid_and_tex_sectioncode records."""

    help = "Create Erratum and Addendum sections with WjsSection pubid_and_tex_sectioncode records."  # noqa

    def add_arguments(self, parser):
        """Add --journal argument to limit creation to a single journal."""
        parser.add_argument(
            "--journal",
            type=str,
            default=None,
            help="Journal code to create sections for (default: all journals).",
        )

    def handle(self, *args, **options):
        """Create Section and WjsSection records for corrections in each journal."""
        journal_code = options.get("journal")
        if journal_code:
            journals = Journal.objects.filter(code=journal_code)
        else:
            journals = Journal.objects.all()

        for journal in journals:
            for section_def in CORRECTION_SECTIONS:
                section, created = Section.objects.get_or_create(
                    journal=journal,
                    name=section_def["name"],
                    defaults={"public_submissions": False},
                )
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Created Section '{section_def['name']}' for journal {journal.code}.",
                        ),
                    )
                else:
                    self.stdout.write(
                        f"Section '{section_def['name']}' already exists for journal {journal.code}.",
                    )

                # Use save_base(raw=True) to avoid re-saving the parent Section row
                # with empty inherited fields (MTI-safe pattern, same as
                # populate_wjs_section command).
                wjs_section = WjsSection(
                    pubid_and_tex_sectioncode=section_def["pubid_code"],
                    section=section,
                )
                wjs_section.save_base(raw=True)

                # Set description via update to avoid triggering another save.
                WjsSection.objects.filter(section=section).update(
                    description=section_def["description"],
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created/updated WjsSection for '{section_def['name']}' "
                        f"(pubid_and_tex_sectioncode='{section_def['pubid_code']}').",
                    ),
                )
