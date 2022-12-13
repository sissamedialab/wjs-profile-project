"""Data migration POC."""
from datetime import timedelta

import lxml
import requests
from django.core.management.base import BaseCommand
from django.utils import timezone
from journal.models import Journal
from submission.models import STAGE_PUBLISHED

from wjs.jcom_profile.factories import ArticleFactory, UserFactory


class Command(BaseCommand):
    help = "Import an article."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        url = options["base_url"]
        url += "node.json"
        # cannot use "path aliases", so we go through the "/node"
        # path, but we can _filter_ any document by giving the name of
        # the filtering field as first parameter in the query
        # string. E.g. https://staging.jcom.sissamedialab.it/node.json?field_id=JCOM_2106_2022_A01
        params = {
            "field_id": "JCOM_2106_2022_A01",
        }
        response = requests.get(url, params)
        self.process(response.json())

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

    def process(self, raw_data):
        """Process an article's raw json data."""
        self.import_files(raw_data)

    def import_files(self, raw_data):
        """Find info about the article "attacchments", download them and import them."""
        attachments = raw_data["list"][0]["field_attachments"]
        for file_dict in attachments:
            file_uri = file_dict["file"]["uri"]
            file_uri += ".json"
            response = requests.get(file_uri)
            file_download_url = response.json()["url"]
            self.stdout.write(file_download_url)

    def _create_user(self, **options):
        """Create a user for the author."""
        # E.g.: user = UserFactory.create(...

    def _create_article(self, user, **options):
        """Create the article.

        If article already exists in Janeway, update it.
        Empty fields set the value to NULL, but undefined field do nothing (the old value is preserverd).
        """
        # E.g. article = ArticleFactory.create(...
