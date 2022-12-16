"""Management command to add a role."""

from core.models import Role
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a role specifying a role name."  # NOQA

    def add_arguments(self, parser):
        """Handle command arguments."""
        parser.add_argument("role_name", type=str, help="Indicates the name of the role to be created")

    def handle(self, *args, **options):
        """Command entry point."""
        role_name = options["role_name"]
        if not role_name:
            self.stdout.write(self.style.ERROR("Specify a role name."))
        else:
            try:
                _, created = Role.objects.get_or_create(name=role_name, slug=role_name.lower())
                if not created:
                    self.stdout.write(self.style.WARNING(f"A role named {role_name} already exists."))
                else:
                    self.stdout.write(self.style.SUCCESS(f"{role_name} role created successfully."))
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"An error occured trying to create a new role named {role_name}: {e}"),
                )
