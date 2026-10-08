"""
Diagnostic command comparing frozen-author affiliation snapshots against live Account data.

Read-only, not part of the metadata export feature itself -- written to answer a specific question
raised by IOP feedback on a real export sample (addresses/countries missing for every author,
including the corresponding author): is this a genuine data gap (the author's Account profile has
no affiliation at all) or a stale snapshot (the Account has it now, but the one-time
FrozenAuthor-linked ``ControlledAffiliation`` copy taken at submission time predates/misses it)?

Run against articles 5057/5061/5093/5095/5874/5955/5958/5965/5979/5987 and confirmed a genuine
upstream data gap (see ``docs/superpowers/specs/2026-09-02-iop-xml-export-design.md``'s *Resolved*
section) -- every source (frozen-author snapshot, live Account, Janeway's canonical
``primary_affiliation()``, and the legacy pre-``FrozenAuthor`` ``article.authors`` M2M) was empty
for every author on every article. Kept in the repo (not deleted) for reuse on future exports/
publisher-feedback debugging of the same kind.
"""

from django.core.management.base import BaseCommand
from submission.models import Article

from ...metadata_export.mappers import (
    _affiliation_dto_from_controlled_affiliation,
    _is_ta_agreed,
    corresponding_author_affiliation_is_incomplete,
    map_affiliation,
)


class Command(BaseCommand):
    help = "Compare exported affiliation to the linked Account's own data, per article id."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument("article_ids", nargs="+", type=int, help="IDs of the articles to inspect")

    def _print_primary_affiliation(self, label, account):
        # `Account.primary_affiliation()` is Janeway's own canonical "which affiliation is THE
        # affiliation" resolution (is_primary=True, else highest-pk, else None) -- the original
        # author's real primary affiliation, not just a raw list of rows.
        primary = account.primary_affiliation()
        dto = _affiliation_dto_from_controlled_affiliation(primary)
        self.stdout.write(
            f"        {label}: primary_affiliation()={primary!r} -> institution={dto.institution!r} "
            f"department={dto.department!r} person_title={dto.person_title!r} "
            f"city={dto.city!r} country={dto.country!r}",
        )

    def _print_affiliations(self, label, affiliations):
        rows = list(affiliations)
        if not rows:
            self.stdout.write(f"        {label}: (none)")
            return
        for row in rows:
            organization = row.organization
            location = organization.location if organization else None
            country = location.country if location else ""
            self.stdout.write(
                f"        {label}: pk={row.pk} is_primary={row.is_primary} str={str(row)!r} "
                f"organization={organization!r} location={location!r} country={country!r}",
            )

    def handle(self, *args, **options):
        for article_id in options["article_ids"]:
            self.stdout.write(self.style.MIGRATE_HEADING(f"=== article {article_id} ==="))
            try:
                article = Article.objects.get(pk=article_id)
            except Article.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"    article {article_id} does not exist"))
                continue

            self.stdout.write(f"    journal={article.journal.code!r} section={article.section!r}")

            submission_data = getattr(article, "submission_data", None)
            access_mode_code = (
                submission_data.access_mode.code if submission_data and submission_data.access_mode else None
            )
            self.stdout.write(f"    access_mode={access_mode_code!r} is_ta_agreed={_is_ta_agreed(article)}")
            if submission_data is not None:
                self.stdout.write(f"    submission_data.affiliation={submission_data.affiliation!r}")

            self.stdout.write(f"    correspondence_author={article.correspondence_author!r}")

            # `Article.authors` is the original, pre-FrozenAuthor M2M author list ("Historically it
            # was a shadow copy of some Account fields, with Account objects in Article.authors, but
            # FrozenAuthor has since superseded Article.authors" -- FrozenAuthor's own docstring).
            # Checked independently of the FrozenAuthor loop below: if a FrozenAuthor's own `author`
            # link is missing/wrong, the original Account here may still carry the real affiliation.
            self.stdout.write("    -- article.authors (original, pre-FrozenAuthor M2M) --")
            for account in article.authors.all():
                self.stdout.write(f"        account pk={account.pk} {account!r}")
                self._print_affiliations("account.affiliations (original author, direct)", account.affiliations)
                self._print_primary_affiliation("account.primary_affiliation() (original author)", account)

            for frozen_author in article.frozenauthor_set.order_by("order"):
                is_corresponding = bool(
                    frozen_author.author_id
                    and article.correspondence_author_id
                    and frozen_author.author_id == article.correspondence_author_id,
                )
                self.stdout.write(
                    f"    -- FrozenAuthor pk={frozen_author.pk} name={frozen_author!r} "
                    f"author_id={frozen_author.author_id} is_corresponding={is_corresponding}",
                )
                self._print_affiliations("frozen_author (what the export sees)", frozen_author.affiliations)
                if frozen_author.author_id:
                    self._print_affiliations(
                        "account (live, not snapshotted onto this article)",
                        frozen_author.author.affiliations,
                    )
                    self._print_primary_affiliation(
                        "frozen_author.author.primary_affiliation() (original author, canonical)",
                        frozen_author.author,
                    )
                else:
                    self.stdout.write("        account: (no linked Account)")

                dto = map_affiliation(frozen_author, article)
                self.stdout.write(
                    f"        map_affiliation() result: institution={dto.institution!r} "
                    f"department={dto.department!r} person_title={dto.person_title!r} "
                    f"city={dto.city!r} country={dto.country!r}",
                )

            incomplete = corresponding_author_affiliation_is_incomplete(article)
            self.stdout.write(f"    corresponding_author_affiliation_is_incomplete={incomplete}")
