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
from django.db.models import Case, CharField, F, Value, When
from django.db.models.functions import Concat
from journal.models import Journal
from plugins.wjs_review.models import EditorDecision, Message, WorkflowReviewAssignment
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

        accounts = Account.objects.exclude(is_admin=True)
        anonymize_users(accounts)
        anonymize_message()
        anonymize_editor_decision()
        if options["titles"]:
            anonymize_titles()
        disable_crossref()
        delete_crossref_credentials()


def anonymize_users(accounts):
    """Anonymize emails, names and some other fields."""
    anonymize_emails(accounts)
    anonymize_accounts_data(accounts)


def anonymize_accounts_data(accounts):
    """Set random names and some other fields onto users."""
    fake = faker.Faker()
    for account in accounts:
        if account.first_name:
            account.first_name = fake.first_name()
        if account.last_name:
            account.last_name = fake.last_name()
        if account.middle_name:
            account.middle_name = fake.first_name()
        account.salutation = ""
        account.suffix = ""
        account.name_prefix = ""
        account.orcid = None
        account.biography = fake.sentence(nb_words=random.randint(5, 13)).title()
        account.twitter = None
        account.facebook = None
        account.linkedin = None
        account.github = None
        account.website = None
        account.profile_image = None
        account.signature = ""
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


def anonymize_subjects(qs):
    """Anonymize some subjects."""
    handled_pks = set()
    cases = [
        ("please select", "please select referee"),
        ("confirms assignment", "confirms assignment"),
        ("declines assignment", "declines assignment"),
        ("review by", "review received"),
        ("revision", "revision update"),
        ("your report", "your report"),
        ("accept/decline", "accept/decline assignment"),
        ("coauthor", "selects coauthor"),
        ("information request", "information request to author"),
        ("review request", "review request"),
        ("your decision", "your decision"),
        ("no reply from referee", "no reply from referee"),
        ("final check", "final check"),
        ("declined assignment", "declined assignment"),
        ("declined invite", "declined invite"),
        ("accepted invite", "accepted invite"),
    ]

    roles = ("Referee", "Editor", "Reviewer", "Author")
    for phrase, label in cases:
        pks = list(qs.filter(subject__icontains=phrase).exclude(pk__in=handled_pks).values_list("pk", flat=True))
        if pks:
            whens = [When(subject__startswith=role, then=Value(f"{role} ⋆⋆⋆ {label}")) for role in roles]
            qs.filter(pk__in=pks).update(subject=Case(*whens, default=Value(label), output_field=CharField()))
            handled_pks.update(pks)

    qs.exclude(pk__in=handled_pks).update(subject="*** anonymized content ***")


def anonymize_message_subjects():
    """Anonymize some message subjects."""
    Message.objects.exclude(message_type="System log message").update(subject="*** anonymized subject ***")
    Message.objects.filter(subject__endswith="invited to review").update(subject="⋆⋆⋆ invited to review")

    qs = Message.objects.filter(message_type="System log message").exclude(subject__endswith="invited to review")
    anonymize_subjects(qs)


def anonymize_message():
    """Anonymize message subjects and body"""
    anonymize_message_subjects()
    Message.objects.all().update(body="*** anonymized content ***")


def anonymize_editor_decision():
    """Anonymize decision_editor_report"""
    EditorDecision.objects.all().update(decision_editor_report="*** anonymized content ***")


def anonymize_workflow_review_assignment():
    """Anonymize report_form_answers"""
    WorkflowReviewAssignment.objects.all().update(report_form_answers="{}")


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
