from django.core.management.base import BaseCommand
from journal.models import Journal
from plugins.wjs_submission.models import AccessMode, AccessModeJournal
from submission.models import Licence


class Command(BaseCommand):
    help = "Setup Access Mode Data"  # noqa

    def add_arguments(self, parser):
        """
        Add command-line arguments to the parser.

        :param parser: ArgumentParser
            The argument parser to which the arguments will be added.
        :return: None
        :raises: None
        """
        parser.add_argument(
            "--clear-all", action="store_true", help="Clear all Access modes and journal configuration."
        )
        parser.add_argument(
            "--clear-journal-access-modes", action="store_true", help="Clear only journal access mode configuration."
        )

    def handle(self, *args, **options):
        """
        Perform initialization and configuration for journal access modes and their related
        settings such as licenses and copyrights.

        This function handles the setup of various access modes and licenses for journals
        based on predefined configurations. It supports clearing existing AccessMode and
        AccessModeJournal data for clean initialization when specified in the options.

        :param args: Additional positional arguments.
        :param options: A dictionary containing options for the operation.
            - "clear-all" (bool): If True, clears all existing AccessMode and AccessModeJournal
              records before processing.
            - "clear-journal-access-modes" (bool): If True, clears only AccessModeJournal
              records before processing.
        :raises Journal.DoesNotExist: If a journal code in the configuration does not exist
            in the database.
        :raises Licence.DoesNotExist: If there is an issue retrieving license information
            from the database.
        :return: None
        """
        access_modes = {
            "subscription": "Available upon subscription – Not OA",
            "open-access-paid": "Open Access (paid by author)",
            "oa-cern": "Open Access – CERN Collaborations",
            "oa-cern-affiliated": "Open Access – CERN-affiliated co-author(s)",
            "oa-transformative-agreement": "Open Access - Transformative agreements",
            "open-access": "Open Access",
        }
        journals = {
            "JHEP": {
                "oa-cern": {
                    "licence": "CC BY 4.0",
                    "copyright": "CERN for the benefit of the collaboration",
                },
                "open-access": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
            },
            "JQUANT": {
                "oa-cern": {
                    "licence": "CC BY 4.0",
                    "copyright": "CERN for the benefit of the collaboration",
                },
                "open-access": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
            },
            "JCAP": {
                "subscription": {
                    "licence": "most-rights",
                    "copyright": "Publisher",
                },
                "open-access-paid": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
                "oa-transformative-agreement": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
            },
            "JSTAT": {
                "subscription": {
                    "licence": "most-rights",
                    "copyright": "Publisher",
                },
                "open-access-paid": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
                "oa-transformative-agreement": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
            },
            "JINST": {
                "subscription": {
                    "licence": "most-rights",
                    "copyright": "Publisher",
                },
                "open-access-paid": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
                "oa-transformative-agreement": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
                "oa-cern": {
                    "licence": "CC BY 4.0",
                    "copyright": "CERN for the benefit of the collaboration",
                },
                "oa-cern-affiliated": {
                    "licence": "CC BY 4.0",
                    "copyright": "CERN",
                },
            },
            "JCOM": {
                "open-access": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
            },
            "JCOMAL": {
                "open-access": {
                    "licence": "CC BY 4.0",
                    "copyright": "Authors",
                },
            },
        }
        if options["clear_all"]:
            AccessMode.objects.all().delete()
        if options["clear_all"] or options["clear_journal_access_modes"]:
            AccessModeJournal.objects.all().delete()
        for journal_code, configuration in journals.items():
            journal = Journal.objects.get(code=journal_code)
            for access_mode_code, data in configuration.items():
                licence, __ = Licence.objects.get_or_create(
                    journal=journal, short_name=data["licence"], defaults={"name": data["licence"]}
                )
                user_selectable = access_mode_code in ["open-access", "subscription"]
                access_mode, __ = AccessMode.objects.get_or_create(
                    code=access_mode_code,
                    defaults={"name": access_modes[access_mode_code], "user_selectable": user_selectable},
                )
                AccessModeJournal.objects.update_or_create(
                    access_mode=access_mode, journal=journal, licence=licence, copyright=data["copyright"]
                )
