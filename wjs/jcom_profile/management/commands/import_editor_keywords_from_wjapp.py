"""Import editors (role, workload, keyword associations) from wjapp.

Standalone replacement for the editor-import half of the legacy import_users_from_wjapp_with_role
command (JCOM-only, no keywords). Safe to run before, after or independently of
import_articles_from_wjapp.py - see import_wjapp/import_editor_keywords.py for why this needs to
be its own pass rather than relying on the article importer's incidental editor-assignment
replay.
"""

import traceback

import mariadb
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from journal.models import Journal

from wjs.jcom_profile.import_wjapp.import_editor_keywords import (
    logger,
    read_editors_data,
    sync_editor,
)


class _DryRunRollback(Exception):
    """Raised inside a per-editor transaction to force a rollback in dry-run mode."""


class Command(BaseCommand):
    help = "Connect to a wjapp database and import editors' role, workload and keywords."  # noqa A003

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument("--journal", required=True, help="Journal code, e.g. JCAP, JCOM, JCOMAL, JHEP.")
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Overwrite existing workload and replace the whole keyword set from wjapp. "
            "Without this flag the command only adds missing editors/keywords, leaving anything "
            "already there untouched (e.g. a workload an EO corrected from the WJS web pages).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="Report what would be added/changed without writing anything to WJS.",
        )

    def handle(self, *args, **options):
        """Command entry point."""
        journal_code = options["journal"].upper()
        try:
            journal = Journal.objects.get(code=journal_code)
        except Journal.DoesNotExist as exc:
            raise CommandError(f"Journal {journal_code} not found.") from exc

        setting = f"WJAPP_{journal_code}_IMPORT_CONNECTION_PARAMS"
        connection_parameters = getattr(settings, setting, None)
        if connection_parameters is None:
            raise CommandError(f'Missing connection parameters. Please ensure "{setting}" exists in settings.')
        if connection_parameters.get("user", "") == "":
            raise CommandError(f'Empty connection parameters for "{setting}". Please check `user`, `host`, etc.')

        dry_run = options["dry_run"]
        prefix = "[DRY RUN] " if dry_run else ""

        connection = mariadb.connect(**connection_parameters)
        failed = []
        new_roles = 0
        new_keywords = 0
        try:
            editors = read_editors_data(connection)
            self.stdout.write(f"Found {len(editors)} editor(s) in wjapp for {journal_code}.")
            for row in editors:
                result = None
                try:
                    with transaction.atomic():
                        result = sync_editor(journal, connection, row, clear=options["clear"])
                        if dry_run:
                            # roll back everything sync_editor just wrote, so a dry-run truly
                            # writes nothing, while still reporting exactly what a real run
                            # would have done (same code path, not a hand-simulated guess)
                            raise _DryRunRollback
                except _DryRunRollback:
                    pass
                except Exception as exc:
                    traceback.print_exc()
                    logger.error(
                        f"An exception {type(exc).__name__}: {exc} occurred for editor "
                        f"userCod={row.get('userCod')} {row.get('firstname')} {row.get('lastname')} "
                        f"<{row.get('email')}>, this editor must be synced again"
                    )
                    failed.append(row.get("userCod"))
                    continue

                if result.changed:
                    self.stdout.write(
                        f"{prefix}  added for {result.editor}: "
                        f"{'role ' if result.role_added else ''}"
                        f"{'workload ' if result.workload_set else ''}"
                        f"{f'{result.keywords_added} keyword(s)' if result.keywords_added else ''}".rstrip()
                    )
                new_roles += result.role_added
                new_keywords += result.keywords_added
        finally:
            connection.close()

        synced = len(editors) - len(failed)
        summary = (
            f"{synced}/{len(editors)} editor(s) for {journal_code}: "
            f"{new_roles} new role(s), {new_keywords} new keyword association(s)."
        )
        if failed:
            self.stdout.write(
                self.style.WARNING(f"{prefix}Synced {summary} Failed userCod(s), see log for detail: {failed}")
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"{prefix}Done syncing {summary}"))
