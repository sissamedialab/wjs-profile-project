"""Populate a journal with random demostrative data."""

from datetime import timedelta

import factory
from django.core.management.base import BaseCommand
from django.utils import timezone
from faker.providers import lorem
from journal.models import Journal
from submission.models import STAGE_PUBLISHED, Article

from wjs.jcom_profile.models import JCOMProfile

factory.Faker.add_provider(lorem)


class UserFactory(factory.Factory):
    """User factory."""

    class Meta:
        model = JCOMProfile

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Faker("email")
    username = email
    is_admin = False
    is_active = True


class ArticleFactory(factory.Factory):
    """Article factory."""

    class Meta:
        model = Article

    title = factory.Faker("sentence", nb_words=7)
    abstract = factory.Faker("paragraph", nb_sentences=5)
    # First journal
    journal_id = 1
    # Set section to "Article" (usually)
    section_id = 1
    # TODO: use dall.e (https://labs.openai.com) to fill `thumbnail_image_file`


class Command(BaseCommand):
    help = "Create a user and an article for the first journal in the press."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        user = self._create_user(**options)
        self._create_article(user, **options)

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument("--roles", help='Roles, as a comma-separated string (e.g. "editor,reader").')

    def _create_user(self, **options):
        """Create a user, honoring roles."""
        user = UserFactory.create()
        self.stdout.write(f"Creating {user}...")
        user.save()

        # Always add the role "author" because we'll use this user as
        # author of an article:
        journal = Journal.objects.get(pk=1)
        user.add_account_role("author", journal)

        roles = options["roles"]
        for role_slug in roles.split(","):
            self.stdout.write(f"  adding role {role_slug}")
            user.add_account_role(role_slug, journal)
        user.save()
        self.stdout.write("  ...done")
        return user

    def _create_article(self, user, **options):
        """Create an article on the first journal, set the user as author."""
        article = ArticleFactory.create()
        self.stdout.write(f"Creating {article}")
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
        self.stdout.write("  ...done")
