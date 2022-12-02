"""Populate a journal with random demostrative data."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from journal.models import Journal
from submission.models import STAGE_PUBLISHED

from wjs.jcom_profile.factories import ArticleFactory, UserFactory


class Command(BaseCommand):
    help = "Create a user and an article for the first journal in the press."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        user = self._create_user(**options)
        self._create_article(user, **options)

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument("--roles", help='Roles, as a comma-separated string (e.g. "editor,reader").')
        parser.add_argument(
            "--journal",
            help="Id (pk) of the journal onto which create the article (default=%(default)s).",
            default=1,
            type=int,
        )

    def _create_user(self, **options):
        """Create a user, honoring roles."""
        user = UserFactory.create()
        self.stdout.write(self.style.SUCCESS(f"Creating {user}..."))
        user.save()

        # Always add the role "author" because we'll use this user as
        # author of an article:
        journal = Journal.objects.get(pk=1)
        user.add_account_role("author", journal)

        roles = options["roles"]
        if roles:
            for role_slug in roles.split(","):
                self.stdout.write(self.style.SUCCESS(f"  adding role {role_slug}"))
                user.add_account_role(role_slug, journal)
        user.save()
        self.stdout.write(self.style.SUCCESS("  ...done"))
        return user

    def _create_article(self, user, **options):
        """Create an article on the first journal, set the user as author."""
        article = ArticleFactory.create(journal=Journal.objects.get(pk=options["journal"]))
        self.stdout.write(self.style.SUCCESS(f"Creating {article}"))
        article.save()
        article.owner = user
        article.authors = [user]
        article.correspondence_author = user
        # publish article
        # see src/journal/views.py:1078
        article.stage = STAGE_PUBLISHED
        article.snapshot_authors()
        article.close_core_workflow_objects()
        article.date_published = timezone.now() - timedelta(days=1)
        article.save()
        self.stdout.write(self.style.SUCCESS("  ...done"))
