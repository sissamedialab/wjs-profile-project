"""Data migration POC."""

from collections import namedtuple
from datetime import datetime, timedelta
from io import BytesIO

import pytz
import requests
from core import models as core_models
from django.core.files import File
from django.core.management.base import BaseCommand
from django.utils import timezone
from identifiers import models as identifiers_models
from journal import models as journal_models
from production.logic import save_galley
from requests.auth import HTTPBasicAuth
from submission import models as submission_models
from utils.logger import get_logger

from wjs.jcom_profile import models as wjs_models

logger = get_logger(__name__)
FakeRequest = namedtuple("FakeRequest", ["user"])
rome_timezone = pytz.timezone("Europe/Rome")


class Command(BaseCommand):
    help = "Import an article."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        url = options["base_url"]
        url += "node.json"

        self.basic_auth = None
        if options["auth"]:
            self.basic_auth = HTTPBasicAuth(*(options["auth"].split(":")))

        # cannot use "path aliases", so we go through the "/node"
        # path, but we can _filter_ any document by giving the name of
        # the filtering field as first parameter in the query
        # string. E.g. https://staging.jcom.sissamedialab.it/node.json?field_id=JCOM_2106_2022_A01
        params = {
            "field_id": options["id"],
        }
        response = requests.get(url, params, auth=self.basic_auth)
        assert response.status_code == 200, f"Got {response.status_code}!"
        # Note that, by calling .../node?xxx=yyy, we are doing a query and getting a list
        # Internal methods don't care, so we pass along only the interesting piece.
        self.process(response.json()["list"][0])

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--id",
            help='Pubication ID of the article to process. Defaults to "%(default)s".',
            default="JCOM_2106_2022_A01",
        )
        parser.add_argument(
            "--base-url",
            help='Base URL. Defaults to "%(default)s)".',
            default="https://staging.jcom.sissamedialab.it/",
        )
        parser.add_argument(
            "--auth",
            help='HTTP Basic Auth in the form "user:passwd" (should be useful only for test sites).',
        )

    def process(self, raw_data):
        """Process an article's raw json data."""
        logger.debug("Processing %s", raw_data["nid"])
        article = self.create_article(raw_data)
        self.set_identifiers(article, raw_data)
        self.set_history(article, raw_data)
        self.set_body_and_abstract(article, raw_data)
        self.set_files(article, raw_data)
        self.set_keywords(article, raw_data)
        self.set_sections(article, raw_data)
        self.set_issue(article, raw_data)
        self.set_authors(article, raw_data)
        self.publish_article(article, raw_data)

    def create_article(self, raw_data):
        """Create a stub for an article with basics metadata.

        - [ ] All the rest (author, kwds, etc.) will be added by someone else.

        - [ ] If article already exists in Janeway, update it.

        - [ ] Empty fields set the value to NULL, but undefined field do nothing (the old value is preserverd).
        """
        journal = journal_models.Journal.objects.get(code="JCOM")
        article = submission_models.Article.get_article(
            journal=journal,
            identifier_type="doi",
            identifier=raw_data["field_doi"],
        )
        assert article.articlewrapper.nid == raw_data["nid"]
        if not article:
            logger.debug("Cannot find article with DOI=%s. Creating a new one.", raw_data["field_doi"])
            article = submission_models.Article.objects.create(
                journal=journal,
                title=raw_data["title"],
                is_import=True,
            )
            article.save()
            article.articlewrapper.nid = raw_data["nid"]
            article.articlewrapper.save()
        return article

    def set_identifiers(self, article, raw_data):
        """Set DOI and publication ID onto the article."""
        # TODO: HHAAAAAAAAAA non unique (identifier, identifier_type, article) at DB level!?!
        doi = raw_data["field_doi"]
        assert doi.startswith("10.22323")
        identifiers_models.Identifier.objects.create(
            identifier=doi,
            article=article,
            id_type="doi",  # should be a member of the set identifiers_models.IDENTIFIER_TYPES
            enabled=True,
        )
        pubid = raw_data["field_id"]
        identifiers_models.Identifier.objects.create(
            identifier=pubid,
            article=article,
            id_type="pubid",
            enabled=True,
        )
        # Drupal's node id "nid"
        nid = raw_data["nid"]
        identifiers_models.Identifier.objects.create(
            identifier=nid,
            article=article,
            id_type="id",
            enabled=True,
        )
        article.save()

    def set_history(self, article, raw_data):
        """Set the review history date: received, accepted, published dates."""
        # received / submitted
        article.date_submitted = rome_timezone.localize(datetime.fromtimestamp(int(raw_data["field_received_date"])))
        article.date_accepted = rome_timezone.localize(datetime.fromtimestamp(int(raw_data["field_accepted_date"])))
        article.date_published = rome_timezone.localize(datetime.fromtimestamp(int(raw_data["field_published_date"])))
        article.save()
        logger.debug("  %s - history", raw_data["nid"])

    def set_body_and_abstract(self, article, raw_data):
        """Set body and abstract.

        Take care of escaping & co.
        Take care of images included in body.
        """

    def set_files(self, article, raw_data):
        """Find info about the article "attachments", download them and import them as galleys."""
        attachments = raw_data["field_attachments"]
        # TODO: who whould this user be???
        admin = core_models.Account.objects.filter(is_admin=True).first()
        fake_request = FakeRequest(user=admin)

        for file_node in attachments:
            file_uri = file_node["file"]["uri"]
            file_uri += ".json"
            response = requests.get(file_uri, auth=self.basic_auth)
            assert response.status_code == 200
            file_dict = response.json()
            file_download_url = file_dict["url"]
            uploaded_file = self.uploaded_file(file_download_url, file_dict["name"])
            # TODO: check if galley with same ?? name ?? exists and overwrite
            save_galley(
                article,
                request=fake_request,
                uploaded_file=uploaded_file,  # how does this compare with `save_to_disk`???
                is_galley=True,
                label=file_node["description"],
                save_to_disk=True,
                public=True,
            )
        logger.debug("  %s - attachments (as galleys)", raw_data["nid"])

    def set_keywords(self, article, raw_data):
        """Create and set keywords."""

    def set_sections(self, article, raw_data):
        """Create and set article types / sections."""

    def set_issue(self, article, raw_data):
        """Create and set issue / collection and volume."""

    def set_authors(self, article, raw_data):
        """Find and set the article's authors, creating them if necessary."""
        # TODO: article.owner = user
        # TODO: article.authors = [user]
        # article.correspondence_author = ???  # This info is missing / lost

    def publish_article(self, article, raw_data):
        """Publish an article."""
        # see src/journal/views.py:1078
        article.stage = submission_models.STAGE_PUBLISHED
        article.snapshot_authors()
        article.close_core_workflow_objects()
        article.date_published = timezone.now() - timedelta(days=1)
        article.save()
        logger.debug("  %s - Janeway publication process", raw_data["nid"])

    def uploaded_file(self, url, name):
        """Download a file from the given url and upload it into Janeway."""
        response = requests.get(url, auth=self.basic_auth)
        return File(BytesIO(response.content), name)
