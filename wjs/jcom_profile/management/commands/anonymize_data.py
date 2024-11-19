"""Anonymize users.

- update emails/username (mechanically)
- update name w/ Faker
  - first_name
  - last_name
  - middle_name
- "secure" crossref
  - ensure "use_crossref" is false for all journals
  - remove crossref credentials from all journals

See specs#585
"""

import random

import faker
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import F, Value
from django.db.models.functions import Concat
from journal.models import Journal
from plugins.wjs_review.models import Message
from submission.models import Article
from utils import setting_handler
from utils.logger import get_logger

logger = get_logger(__name__)
Account = get_user_model()


class Command(BaseCommand):
    help = "Anoymize data"  # NOQA

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "-t",
            "--titles",
            action="store_true",
            help="Also anonimize titles of non-published papers.",
        )

    def handle(self, *args, **options):
        """Command entry point."""
        # Safety net: refuse to run on production
        if not hasattr(settings, "DEBUG"):
            # This cannot happen, because janeway_global_settings defines DEBUG, but...
            self.stdout.write(self.style.ERROR("Refusing to run when DEBUG is not explicitly set to True."))
            return
        if settings.DEBUG is False:
            self.stdout.write(self.style.ERROR("Refusing to run when DEBUG is False."))
            return

        assert settings.DEBUG is True

        accounts = Account.objects.exclude(
            email__in=(
                "elia@medialab.sissa.it",
                "gamboz@medialab.sissa.it",
                "i.spalletti@nephila.digital",
                "leo@medialab.sissa.it",
                "m.caglienzi@nephila.digital",
                "mmizzaro@medialab.sissa.it",
                "s.petronici@nephila.digital",
                "wjs-support@medialab.sissa.it",
                "fracarossi@medialab.sissa.it",
            ),
        )
        anonymize_users(accounts)
        anonymize_message_subjects()
        if options["titles"]:
            anonymize_titles()
        disable_crossref()
        delete_crossref_credentials()


def anonymize_users(accounts):
    """Anonymize emails and names."""
    anonymize_emails(accounts)
    anonymize_names(accounts)
    # TODO: what about ORCIDIDs, github username, bio, profile image,...


def anonymize_names(accounts):
    """Set random names onto users."""
    fake = faker.Faker()
    for account in accounts:
        if account.first_name:
            account.first_name = fake.first_name()
        if account.last_name:
            account.last_name = fake.last_name()
        if account.middle_name:
            account.middle_name = fake.first_name()
        account.save()


def anonymize_emails(accounts):
    """Set email and username to id@invalid.com."""
    accounts.update(
        email=Concat(F("id"), Value("@invalid.com")),
        username=Concat(F("id"), Value("@invalid.com")),
    )


def anonymize_titles():
    """Set a random title on non-published papers."""
    fake = faker.Faker()
    for article in Article.objects.filter(date_published__isnull=True):
        article.title = fake.sentence(nb_words=random.randint(5, 13)).title()
        article.save()


def anonymize_message_subjects():
    """Anonymize some message subjects."""
    Message.objects.filter(subject__endswith="invited to review").update(subject="⋆⋆⋆ invited to review")

    Message.objects.filter(
        subject__startswith="Editor",
        subject__endswith="declined assignment",
    ).update(subject="Editor ⋆⋆⋆ declined assignment")

    Message.objects.filter(
        subject__startswith="Reviewer",
        subject__endswith="declined invite",
    ).update(subject="Reviewer ⋆⋆⋆ declined invite")
    Message.objects.filter(
        subject__startswith="Reviewer",
        subject__endswith="accepted invite",
    ).update(subject="Reviewer ⋆⋆⋆ accepted invite")


def disable_crossref():
    """Disable crossref."""
    for journal in Journal.objects.all():
        setting_handler.save_setting(
            "Identifiers",
            "use_crossref",
            journal,
            False,
        )


def delete_crossref_credentials():
    """Drop crossref registrant and password."""
    for journal in Journal.objects.all():
        setting_handler.save_setting(
            "Identifiers",
            "crossref_username",
            journal,
            "",
        )
        setting_handler.save_setting(
            "Identifiers",
            "crossref_password",
            journal,
            "",
        )
