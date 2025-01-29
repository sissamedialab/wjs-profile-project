"""Reset editors and EO assignment paramenters for JCOM and JCOMAL.

See also specs#1270.
"""

from core.models import Account
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from journal.models import Journal

from wjs.jcom_profile.constants import DIRECTOR_ROLE, EO_GROUP, SECTION_EDITOR_ROLE
from wjs.jcom_profile.models import StaffWorkloadParameters


class Command(BaseCommand):
    help = __doc__  # noqa: A003

    def add_arguments(self, parser):
        """Handle command arguments."""
        parser.add_argument(
            "--noinput",
            "--no-input",
            dest="interactive",
            action="store_false",
            help="Do NOT prompt the user for input of any kind.",
        )

    def handle(self, *args, **options):
        """Command entry point."""
        if settings.DEBUG is False and options["interactive"]:
            message = "Are you sure you want to do this? ('yes' to continue): "
            if input(message) != "yes":
                raise CommandError("Resetting assignment parameters cancelled.")

        # Assume that the situation is messy: some editors don't have SAPs and some have, but for those who have, the
        # workload is probably wrong.
        for journal in Journal.objects.all():
            # EO: reset all to 0, except for Giulia and Bea
            # Please note that EO group is cross-journal
            default_workload = 100
            for user in Account.objects.filter(groups__name__in=[EO_GROUP]):
                swp, created = StaffWorkloadParameters.objects.get_or_create(
                    journal=journal,
                    user=user,
                    defaults={"workload": 0},  # this is the default WL on the model, we just make it explicit here
                )
                if user.last_name in ["Cassano", "Biggio"]:
                    swp.workload = default_workload
                    swp.save()
                elif not created:
                    # reset to 0 all the others (just in case)
                    swp.workload = 0
                    swp.save()

            # Editors: reset all to 100
            # Please note that editor and director roles are per-journal
            default_workload = 100
            for user in Account.objects.filter(
                accountrole__journal=journal,
                accountrole__role__name=SECTION_EDITOR_ROLE,
            ):
                swp, created = StaffWorkloadParameters.objects.get_or_create(
                    journal=journal,
                    user=user,
                    defaults={"workload": default_workload},
                )
                if not created:
                    swp.workload = default_workload
                    swp.save()

            # Directors: similar to editors
            default_workload = 1000
            for user in Account.objects.filter(accountrole__journal=journal, accountrole__role__name=DIRECTOR_ROLE):
                swp, created = StaffWorkloadParameters.objects.get_or_create(
                    journal=journal,
                    user=user,
                    defaults={"workload": default_workload},
                )
                if not created:
                    swp.workload = default_workload
                    swp.save()

            # keywords ???
