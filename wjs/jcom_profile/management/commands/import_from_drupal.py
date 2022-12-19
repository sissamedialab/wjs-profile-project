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


# TODO: rethink sections order?
# SECTION_ORDER =
#     "Editorial":
#     "Focus":
#     "Article":
#     "Practice insight":
#     "Essay":
#     "Comment":
#     "Letter":
#     "Book Review":
#     "Conference Review": 9,


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
        self.set_files(article, raw_data)
        self.set_body_and_abstract(article, raw_data)
        self.set_keywords(article, raw_data)
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
        if not article:
            logger.debug("Cannot find article with DOI=%s. Creating a new one.", raw_data["field_doi"])
            article = submission_models.Article.objects.create(
                journal=journal,
                title=raw_data["title"],
                is_import=True,
            )
            article.save()
            article.articlewrapper.nid = int(raw_data["nid"])
            article.articlewrapper.save()
        assert article.articlewrapper.nid == int(raw_data["nid"])
        return article

    def set_identifiers(self, article, raw_data):
        """Set DOI and publication ID onto the article."""
        # I use `get_or_create` because
        # (identifier x identifier_type x article) has no "unique"
        # constraint at DB level, so if issue a `create` it would just
        # work and the same article will end up with multiple
        # identical identifiers.
        doi = raw_data["field_doi"]
        assert doi.startswith("10.22323")
        identifiers_models.Identifier.objects.get_or_create(
            identifier=doi,
            article=article,
            id_type="doi",  # should be a member of the set identifiers_models.IDENTIFIER_TYPES
            enabled=True,
        )
        pubid = raw_data["field_id"]
        identifiers_models.Identifier.objects.get_or_create(
            identifier=pubid,
            article=article,
            id_type="pubid",
            enabled=True,
        )
        # Drupal's node id "nid"
        nid = raw_data["nid"]
        identifiers_models.Identifier.objects.get_or_create(
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
        expected_language = "und"
        if raw_data["language"] != expected_language:
            logger.error(
                "Abstract's language is %s (different from expected %s).",
                raw_data["language"],
                expected_language,
            )

        # Abstract
        abstract_dict = raw_data["field_abstract"]
        abstract = abstract_dict.get("value", None)
        if abstract and "This item is available only in the original language." in abstract:
            abstract = None
        expected_format = "filtered_html"
        if abstract_dict["format"] != expected_format:
            logger.error(
                "Abstract's format is %s (different from expected %s).",
                abstract_dict["format"],
                expected_format,
            )
        if abstract_dict["summary"] != "":
            logger.error("Abstract has a summary. What should I do?")
        article.abstract = abstract
        logger.debug("  %s - abstract", raw_data["nid"])

        # Body (NB: it's a galley with mime-type in files.HTML_MIMETYPES)
        body_dict = raw_data["body"]
        body = body_dict.get("value", None)
        if body and "This item is available only in the original language." in body:
            body = None
        expected_format = "full"
        if body_dict["format"] != expected_format:
            logger.error(
                "Body's format is %s (different from expected %s).",
                body_dict["format"],
                expected_format,
            )
        if body_dict["summary"] != "":
            if body_dict["summary"] != '<div class="tex2jax"></div>':
                logger.error("Body has a summary. What should I do?")

        name = "body.html"
        admin = core_models.Account.objects.filter(is_admin=True).first()
        fake_request = FakeRequest(user=admin)
        body_as_file = File(BytesIO(body_dict["value"].encode()), name)
        save_galley(
            article,
            request=fake_request,
            uploaded_file=body_as_file,
            is_galley=True,
            label="Body (TBV)",
            save_to_disk=True,
            public=True,
        )
        article.body = body
        article.save()
        logger.debug("  %s - body (as html galley)", raw_data["nid"])

    def set_files(self, article, raw_data):
        """Find info about the article "attachments", download them and import them as galleys."""
        # First, let's drop all existing files
        # see plugin imports.ojs.importers.import_galleys
        for galley in article.galley_set.all():
            galley.unlink_files()
            galley.delete()

        attachments = raw_data["field_attachments"]
        # TODO: who whould this user be???
        admin = core_models.Account.objects.filter(is_admin=True).first()
        fake_request = FakeRequest(user=admin)
        # "attachments" are only references to "file" nodes
        for file_node in attachments:
            file_dict = self.fetch_data_dict(file_node["file"]["uri"])
            file_download_url = file_dict["url"]
            uploaded_file = self.uploaded_file(file_download_url, file_dict["name"])
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
        # Drop all article's kwds (and KeywordArticles, used for kwd ordering)
        article.keywords.all().delete()
        for order, kwd_node in enumerate(raw_data.get("field_keywords", [])):
            kwd_dict = self.fetch_data_dict(kwd_node["uri"])
            keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_dict["name"])
            submission_models.KeywordArticle.objects.get_or_create(
                article=article,
                keyword=keyword,
                order=order,
            )
            article.keywords.add(keyword)
        article.save()
        logger.debug("  %s - keywords (%s)", raw_data["nid"], article.keywords.count())

    def set_issue(self, article, raw_data):
        """Create and set issue / collection and volume."""
        # adapting imports.ojs.importers.get_or_create_issue
        issue_data = self.fetch_data_dict(raw_data["field_issue"]["uri"])

        # in Drupal, volume is a dedicated document type, but in
        # Janeway it is only a number
        # sanity check (apparently Drupal exposes volume uri in both article and issue json):
        assert raw_data["field_volume"]["uri"] == issue_data["field_volume"]["uri"]
        volume_data = self.fetch_data_dict(issue_data["field_volume"]["uri"])

        volume_num = int(volume_data["field_id"])

        # I don't use the volume's title in Janeway, here I only want
        # to double check data's sanity. The volume's title always has the form
        # "Volume 01, 2002"
        volume_title = volume_data["title"]
        year = 2001 + volume_num
        assert volume_title == f"Volume {volume_num:02}, {year}"

        issue_num = int(issue_data["field_number"])

        # Drupal has "created" and "changed", but they are not what we
        # need here.
        # TODO: can I leave this empty??? should I evince from the issue number???
        #       maybe I can use the publication date of the issue's editorial?
        date_published = timezone.now()

        # TODO: JCOM has "special issues" published alongside normal
        # issues, while Janeway has "collections", that are orthogonal
        # (i.e. one article can belong to only one issue, but to
        # multiple collections). Also, issues are enumerated in a
        # dedicated page, but this page does not include collections.
        issue_type__code = "issue"
        if "Special" in issue_data["title"]:
            issue_type__code = "collection"
        issue, created = journal_models.Issue.objects.get_or_create(
            journal=article.journal,
            volume=volume_num,
            issue=issue_num,
            issue_type__code=issue_type__code,
            defaults={
                "date": date_published,
                "issue_title": issue_data["title"],
            },
        )
        if created:
            issue_type = journal_models.IssueType.objects.get(
                code=issue_type__code,
                journal=article.journal,
            )
            issue.issue_type = issue_type
            issue.save()
            logger.debug("  %s - new issue %s", raw_data["nid"], issue)

        if issue_data.get("description"):
            logger.error("Matteo doesn't expect this. Don't confuse him please!!!")
            issue.issue_description = issue_data["description"]

        issue.save()

        # Handle cover image
        if issue_data.get("field_image", None):
            image_node = issue_data.get("field_image")
            assert image_node["file"]["resource"] == "file"
            # Drop eventual existing cover images
            if issue.cover_image:
                issue.cover_image.delete()
            if issue.large_image:
                issue.large_image.delete()
            # Get the new cover
            # see imports.ojs.importers.import_issue_metadata
            file_dict = self.fetch_data_dict(image_node["file"]["uri"])
            issue_cover = self.uploaded_file(file_dict["url"], file_dict["name"])
            # A Janeway issue has both cover_image ("Image
            # representing the the cover of a printed issue or
            # volume"), and large_image ("landscape hero image used in
            # the carousel and issue page"). The second one appears in
            # the issue page. Using that.
            # NO: issue.cover_image = ..
            issue.large_image = issue_cover
            logger.debug("  %s - issue cover (%s)", raw_data["nid"], file_dict["name"])

        # must ensure that a SectionOrdering exists for this issue,
        # otherwise issue.articles.add() will fail
        section_data = self.fetch_data_dict(raw_data["field_type"]["uri"])
        section_name = section_data["name"]
        section, _ = submission_models.Section.objects.get_or_create(
            journal=article.journal,
            name=section_name,
        )
        article.section = section

        # TODO: J. has order of sections in issue + order of articles in section
        #       we just do order of article in issue (no relation with article's section)
        # Temporary workaround:
        section_order = int(section_data["weight"])
        # As an alternative, I could impose it:
        # ... = SECTION_ORDER(section_name)
        journal_models.SectionOrdering.objects.get_or_create(
            issue=issue,
            section=section,
            defaults={"order": section_order},
        )

        article.primary_issue = issue
        article.save()
        issue.articles.add(article)
        issue.save()
        logger.debug("  %s - issue (%s)", raw_data["nid"], issue.id)

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

    def fetch_data_dict(self, uri):
        """Fetch the json data from the given uri.

        Append .json to the uri, do a GET and return the result as a dictionary.
        """
        uri += ".json"
        response = requests.get(uri, auth=self.basic_auth)
        assert response.status_code == 200
        return response.json()
