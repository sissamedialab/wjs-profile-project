"""Data migration POC."""
from collections import namedtuple
from datetime import datetime, timedelta

import requests
from core import models as core_models
from django.core.management.base import BaseCommand
from django.utils import timezone
from journal import models as journal_models
from production.logic import save_galley
from submission import models as submission_models

from wjs.jcom_profile import models as wjs_models

FakeRequest = namedtuple("FakeRequest", ["user"])


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

    def process(self, raw_data):
        """Process an article's raw json data."""
        self.create_article(raw_data)
        self.set_files(raw_data)

    def create_article(self, raw_data):
        """Create a stub for an article with basics metadata.

        - [ ] All the rest (author, kwds, etc.) will be added by someone else.

        - [ ] If article already exists in Janeway, update it.

        - [ ] Empty fields set the value to NULL, but undefined field do nothing (the old value is preserverd).
        """
        # TODO: add Drupal's "nid" (node-id) to ArticleWrapper
        #       Drupal also has a "vid" (version-id), but JCOM does not use it, so we can ignore it
        # e.g.: article_wrapper, created = wjs_models.ArticleWrapper.objects.get_or_create(nid=int(raw_data["nid"]))
        journal = journal_models.Journal.objects.get(code="JCOM")
        title = raw_data["title"]
        article = submission_models.Article.objects.create(
            journal=journal,
            title=title,
        )
        self.set_history(article, raw_data)
        self.set_body_and_abstract(article, raw_data)
        self.set_files(article, raw_data)
        self.set_keywords(article, raw_data)
        self.set_sections(article, raw_data)
        self.set_issue(article, raw_data)
        self.set_authors(article, raw_data)
        self.publish_article(article)

    def set_history(self, article, raw_data):
        """Set the review history date: received, accepted, published dates."""
        # received / submitted
        article.date_submitted = datetime.fromtimestamp(int(raw_data["field_received_date"]))
        article.date_accepted = datetime.fromtimestamp(int(raw_data["field_accepted_date"]))
        article.date_published = datetime.fromtimestamp(int(raw_data["field_published_date"]))
        article.save()

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

        for file_dict in attachments:
            file_uri = file_dict["file"]["uri"]
            file_uri += ".json"
            response = requests.get(file_uri)
            file_download_url = response.json()["url"]
            uploaded_file = self.upload_file(file_download_url)
            save_galley(
                article,
                request=fake_request,
                uploaded_file=uploaded_file,  # how does this compare with `save_to_disk`???
                is_galley=True,
                label=file_dict["description"],
                save_to_disk=True,
                public=True,
            )
            self.stdout.write(file_download_url)

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

    def publish_article(self, article):
        """Publish an article."""
        # see src/journal/views.py:1078
        article.stage = submission_models.STAGE_PUBLISHED
        article.snapshot_authors()
        article.close_core_workflow_objects()
        article.date_published = timezone.now() - timedelta(days=1)
        article.save()

    def uploaded_file(self, url):
        """Download a file from the given url and upload it into Janeway."""
        response = requests.get(url)
        return response.content
