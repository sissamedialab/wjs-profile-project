"""Data migration POC."""
import os
import re
from collections import namedtuple
from datetime import datetime
from io import BytesIO
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import lxml.html
import pytz
import requests
from core import models as core_models
from core.logic import (
    handle_article_large_image_file,
    handle_article_thumb_image_file,
    resize_and_crop,
)
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.utils import timezone
from identifiers import models as identifiers_models
from journal import models as journal_models
from lxml.html import HtmlElement
from production.logic import save_galley, save_galley_image, save_supp_file
from requests.auth import HTTPBasicAuth
from submission import models as submission_models
from utils.logger import get_logger

from wjs.jcom_profile import models as wjs_models

logger = get_logger(__name__)
FakeRequest = namedtuple("FakeRequest", ["user"])
rome_timezone = pytz.timezone("Europe/Rome")
# Expect a "body" to be available since last issue of 2016 (Issue 06,
# 2016); the first document of that issue has been published
# 2016-10-21, but commentaries of that issue do not have a body. So
# I'll consider the first issue of 2017 as a boundary date (first
# document published 2017-01-11).
BODY_EXPECTED_DATE = timezone.datetime(2017, 1, 1, tzinfo=rome_timezone)
# Expect to have review dates (submitted/accepted) since this
# date. The first paper managed by wjapp has: submitted 2015-03-04 /
# published 2015-03-03 (published before submitted!?!).  All
# publication dates up to 29 Sep 2015 have timestamp at "00:00" and
# they are probably artificial.
HISTORY_EXPECTED_DATE = timezone.datetime(2015, 9, 29, tzinfo=rome_timezone)

# Janeway and wjapp country names do not completely overlap (sigh...)
COUNTRIES_MAPPING = {
    "Netherlands (the)": "Netherlands",
    "Philippines (the)": "Philippines",
    "Russian Federation (the)": "Russian Federation",
    "United Kingdom of Great Britain and Northern Ireland (the)": "United Kingdom",
    "United States of America (the)": "United States",
    "Taiwan": "Taiwan, Province of China",
}

# Default order of sections in any issue.
# It is not possible to mix different types (e.g. A1 E1 A2...)
SECTION_ORDER = {
    "Editorial": 1,
    "Article": 2,
    "Review Article": 3,
    "Practice insight": 4,
    "Essay": 5,
    "Focus": 6,
    "Comment": 7,
    "Letter": 8,
    "Book Review": 9,
    "Conference Review": 10,
}


class Command(BaseCommand):
    help = "Import an article."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        self.options = options
        # TODO: who whould this user be???
        admin = core_models.Account.objects.filter(is_admin=True).first()
        self.fake_request = FakeRequest(user=admin)

        # There is no point in importing the same things for every
        # article, so I'm keeping track of what I've already imported
        # to be able to do it once only.
        self.seen_issues = {}
        self.seen_keywords = {}
        self.seen_sections = {}
        self.seen_authors = {}

        for raw_data in self.find_articles():
            try:
                self.process(raw_data)
            except Exception as e:
                logger.critical("Failed import for %s (%s)!\n%s", raw_data["field_id"], raw_data["nid"], e)
                # raise e

    def add_arguments(self, parser):
        """Add arguments to command."""
        filters = parser.add_mutually_exclusive_group()
        filters.add_argument(
            "--id",
            help='Pubication ID of the article to process (e.g. "JCOM_2106_2022_A01").'
            " If not given, all articles are queried and processed.",
        )
        filters.add_argument(
            "--year",
            help="Process all articles of this year.",
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
        parser.add_argument(
            "--article-image-meta-only",
            action="store_true",
            help='Set the article image as "meta" only. By default it is set as "large".'
            " See also https://janeway.readthedocs.io/en/latest/published/articles.html#images",
        )
        parser.add_argument(
            "--article-image-no-thumbnail",
            action="store_true",
            help="Do NOT create a thumbnail for the article from the large image.",
        )

    def find_articles(self):
        """Find all articles to process.

        We go through the "/node" entry point and we _filter_ any
        document by giving the name of the filtering field as first
        parameter in the query string,.
        E.g.
        https://staging.jcom.sissamedialab.it/node.json?field_id=JCOM_2106_2022_A01
        or
        https://staging.jcom.sissamedialab.it/node.json?type=Document
        """
        url = self.options["base_url"]
        url += "node.json"

        self.basic_auth = None
        if self.options["auth"]:
            self.basic_auth = HTTPBasicAuth(*(self.options["auth"].split(":")))

        # Find the first batch
        params = {}
        if self.options["id"]:
            params.setdefault("field_id", self.options["id"])
        else:
            params.setdefault("type", "Document")
        response = requests.get(url, params, auth=self.basic_auth)
        assert response.status_code == 200, f"Got {response.status_code}!"
        response_json = response.json()
        batch = response_json["list"]
        while True:
            if not batch:
                if "next" not in response_json:
                    break
                # next batch
                u = urlsplit(response_json["next"])
                url = urlunsplit(
                    [
                        u.scheme,
                        u.netloc,
                        u.path,
                        "",
                        "",
                    ],
                )
                # Warning: url cannot be used as it is: it lacks the ".json"
                url += ".json"
                params = dict(parse_qsl(u.query))
                response = requests.get(url, params, auth=self.basic_auth)
                response_json = response.json()
                batch.extend(response_json["list"])
                logger.debug(" ------------- Next batch -------------")
            raw_data = batch.pop(0)
            yield raw_data

    def process(self, raw_data):
        """Process an article's raw json data."""
        if interesting_year := self.options["year"]:
            article_year = rome_timezone.localize(datetime.fromtimestamp(int(raw_data["field_year"]))).year
            if article_year < int(interesting_year):
                return

        logger.debug("Processing %s (nid=%s)", raw_data["field_id"], raw_data["nid"])
        self.wjapp = self.data_from_wjapp(raw_data)
        article = self.create_article(raw_data)
        self.set_identifiers(article, raw_data)
        self.set_history(article, raw_data)
        self.set_files(article, raw_data)
        self.set_supplementary_material(article, raw_data)
        self.set_image(article, raw_data)
        self.set_abstract(article, raw_data)
        self.set_body(article, raw_data)
        self.set_keywords(article, raw_data)
        self.set_issue(article, raw_data)
        self.set_authors(article, raw_data)
        self.publish_article(article, raw_data)

    def create_article(self, raw_data):
        """Create a stub for an article with basic metadata.

        - All the rest (author, kwds, etc.) will be added by someone else.
        - If article already exists in Janeway, update it.
        - Empty fields set the value to NULL, but undefined field do
          nothing (the old value is preserverd).

        """
        journal = journal_models.Journal.objects.get(code="JCOM")
        # There is a document with no DOI (JCOM_1303_2014_RCR), so I use the "pubid"
        article = submission_models.Article.get_article(
            journal=journal,
            identifier_type="pubid",
            identifier=raw_data["field_id"],
        )
        if not article:
            logger.debug("Cannot find article with pubid=%s. Creating a new one.", raw_data["field_id"])
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
        if doi := raw_data["field_doi"]:
            assert doi.startswith("10.22323")
            identifiers_models.Identifier.objects.get_or_create(
                identifier=doi,
                article=article,
                id_type="doi",  # should be a member of the set identifiers_models.IDENTIFIER_TYPES
                enabled=True,
            )
        else:
            logger.warning("Missing DOI for %s (%s)", raw_data["field_id"], raw_data["nid"])
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
        """Set the review history date: received, accepted, published dates.

        Fields names are as follow:
        | wjapp           | Drupal               | Janeway        |
        +-----------------+----------------------+----------------+
        | publicationDate | field_published_date | date_published |
        | ...             |                      |                |
        """
        # Do publication date first, because we should always have it
        # and the other two are expected to exist after a certain
        # publication date.
        timestamp = raw_data["field_published_date"]
        if not timestamp:
            logger.error("Missing publication date for %s. This is unexpected...", raw_data["field_id"])
            timestamp = self.wjapp.get("publicationDate", None)
            if not timestamp:
                logger.error("Even more fun: no publication date for %s even on wjapp.", raw_data["field_id"])
                timestamp = timezone.now().timestamp()
        article.date_published = rome_timezone.localize(datetime.fromtimestamp(int(timestamp)))

        # submission / received date
        timestamp = raw_data["field_received_date"]
        if timestamp:
            article.date_submitted = rome_timezone.localize(datetime.fromtimestamp(int(timestamp)))
        elif article.date_published >= HISTORY_EXPECTED_DATE:
            timestamp = self.wjapp.get("submissionDate", None)
            if timestamp:
                article.date_submitted = rome_timezone.localize(datetime.fromtimestamp(int(timestamp)))
            else:
                logger.error("Missing submission date for %s.", raw_data["field_id"])
        # else... it's ok not having submission date before HISTORY_EXPECTED_DATE

        # acceptance date
        timestamp = raw_data["field_accepted_date"]
        if timestamp:
            article.date_accepted = rome_timezone.localize(datetime.fromtimestamp(int(timestamp)))
        elif article.date_published >= HISTORY_EXPECTED_DATE:
            timestamp = self.wjapp.get("acceptanceDate", None)
            if timestamp:
                article.date_accepted = rome_timezone.localize(datetime.fromtimestamp(int(timestamp)))
            else:
                logger.error("Missing acceptance date for %s.", raw_data["field_id"])
        # else... it's ok not having acceptance date before HISTORY_EXPECTED_DATE

        article.save()
        logger.debug("  %s - history", raw_data["field_id"])

    def set_files(self, article, raw_data):
        """Find info about the article "attachments", download them and import them as galleys."""
        # See also plugin imports.ojs.importers.import_galleys.

        # First, let's drop all existing galleys
        for galley in article.galley_set.all():
            for file_obj in galley.images.all():
                file_obj.delete()
            galley.images.clear()
            galley.file.delete()
            galley.file = None
            galley.delete()
        article.galley_set.clear()
        article.render_galley = None

        attachments = raw_data["field_attachments"]
        # Drupal "attachments" are only references to "file" nodes
        for file_node in attachments:
            file_dict = self.fetch_data_dict(file_node["file"]["uri"])
            file_download_url = file_dict["url"]
            uploaded_file = self.uploaded_file(file_download_url, file_dict["name"])
            label = self.decide_galley_label(raw_data, file_node, file_dict)
            save_galley(
                article,
                request=self.fake_request,
                uploaded_file=uploaded_file,  # how does this compare with `save_to_disk`???
                is_galley=True,
                label=label,
                save_to_disk=True,
                public=True,
            )
        logger.debug("  %s - attachments / galleys (%s)", raw_data["field_id"], len(attachments))

    def decide_galley_label(self, raw_data, file_node, file_dict):
        """Decide the galley's label."""
        # Remember that we can have ( PDF + EPUB galley ) x languages (usually two),
        # so a label of just "PDF" might not be sufficient.
        lang = re.search(r"_([a-z]{2,3})\.", file_dict["name"])
        mime_to_extension = {
            "application/pdf": "PDF",
            "application/epub+zip": "EPUB",
        }
        label = mime_to_extension.get(file_dict["mime"], None)
        if label is None:
            logger.error("""Unknown mime type "%s" for %s""", file_dict["mime"], raw_data["field_id"])
            label = "Other"
        if lang is not None:
            label = f"{label} ({lang.group(1)})"
        return label

    def set_supplementary_material(self, article, raw_data):
        """Import JCOM's supllementary material as another galley."""
        for supp_file in article.supplementary_files.all():
            supp_file.file.delete()
            supp_file.file = None
        article.supplementary_files.clear()

        supplementary_materials = raw_data["field_additional_files"]
        # "supplementary_materials" are references to "file" nodes
        for file_node in supplementary_materials:
            file_dict = self.fetch_data_dict(file_node["file"]["uri"])
            file_download_url = file_dict["url"]
            uploaded_file = self.uploaded_file(file_download_url, file_dict["name"])
            save_supp_file(
                article,
                request=self.fake_request,
                uploaded_file=uploaded_file,  # how does this compare with `save_to_disk`???
                label=file_node["description"],
            )
        logger.debug("  %s - supplementary_materials (%s)", raw_data["field_id"], len(supplementary_materials))

    def set_image(self, article, raw_data):
        """Download and set the "social" image of the article."""
        # Clean up possibly existing files from previous imports.
        #
        # In theory, the file_obj could be shared with other articles,
        # but during import I'm sure that it is not the case. So I
        # just delete everything.
        # NB: file_obj.delete() calls file_obj.unlink_file()
        if article.large_image_file:
            article.large_image_file.delete()
            article.large_image_file = None
        if article.thumbnail_image_file:
            article.thumbnail_image_file.delete()
            article.thumbnail_image_file = None

        if not raw_data["field_image"]:
            return
        images_list = raw_data["field_image"]
        if len(images_list) != 1:
            logger.warning(
                "Found %s image nodes for %s (expecing 1)",
                len(images_list),
                raw_data["field_id"],
            )
        image_node = raw_data["field_image"]["file"]
        image_dict = self.fetch_data_dict(image_node["uri"])
        image_file: File = self.uploaded_file(image_dict["url"], image_dict["name"])
        if self.options["article_image_meta_only"]:
            article.meta_image = image_file
        else:
            handle_article_large_image_file(image_file, article, self.fake_request)
        if not self.options["article_image_no_thumbnail"]:
            image_file.name = self.make_thumb_name(image_file.name)
            handle_article_thumb_image_file(image_file, article, self.fake_request)
            thumb_size = [138, 138]
            resize_and_crop(article.thumbnail_image_file.self_article_path(), thumb_size)
        article.save()
        logger.debug("  %s - article image", raw_data["field_id"])

    def make_thumb_name(self, name):
        """Make the name of the thumbnail image as name_san_extension-small.extension."""
        [name_sans_extension, extension] = os.path.splitext(name)
        small = "-small"
        return name_sans_extension + small + extension

    def set_abstract(self, article, raw_data):
        """Set the abstract."""
        expected_language = "und"
        if raw_data["language"] != expected_language:
            logger.error(
                "Abstract's language is %s (different from expected %s).",
                raw_data["language"],
                expected_language,
            )

        abstract_dict = raw_data["field_abstract"]
        if not abstract_dict:
            logger.warning("Missing abstract in %s (%s)", raw_data["field_id"], raw_data["nid"])
            return

        abstract = abstract_dict.get("value", None)
        if abstract and "This item is available only in the original language." in abstract:
            abstract = None
        expected_formats = ("full", "filtered_html")
        if abstract_dict["format"] not in expected_formats:
            logger.error(
                """Unexpected abstract's format: "%s" for %s.""",
                abstract_dict["format"],
                raw_data["field_id"],
            )
        if abstract_dict["summary"] != "":
            logger.debug(
                "  %s - dropping short-abstract (%s chars)",
                raw_data["field_id"],
                len(abstract_dict["summary"]),
            )
        article.abstract = abstract
        logger.debug("  %s - abstract", raw_data["field_id"])

    def set_body(self, article, raw_data):
        """Manage the body."""
        # All galleys have already been deleted in `set_files`.

        # Body (NB: it's a galley with mime-type in files.HTML_MIMETYPES)
        body_dict = raw_data["body"]
        if not body_dict:
            if article.date_published > BODY_EXPECTED_DATE:
                logger.warning("Missing body in (%s)", raw_data["field_id"], raw_data["nid"])
            article.save()
            return
        body = body_dict.get("value", None)
        if body and "This item is available only in the original language." in body:
            body = None
        expected_formats = ("full", "filtered_html")
        if body_dict["format"] not in expected_formats:
            logger.error("""Unexpected body's format: "%s" for %s.""", body_dict["format"], raw_data["field_id"])
        if body_dict["summary"] != "":
            if body_dict["summary"] != '<div class="tex2jax"></div>':
                logger.error("Body has a summary. What should I do?")

        name = "body.html"
        label = "HTML"
        body_bytes = self.process_body(article, body)
        body_as_file = File(BytesIO(body_bytes), name)
        new_galley = save_galley(
            article,
            request=self.fake_request,
            uploaded_file=body_as_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=True,
        )
        article.render_galley = new_galley
        self.mangle_images(article)
        article.save()
        logger.debug("  %s - body (as html galley)", raw_data["field_id"])

    def set_keywords(self, article, raw_data):
        """Create and set keywords."""
        # Drop all article's kwds (and KeywordArticles, used for kwd ordering)
        article.keywords.clear()
        for order, kwd_node in enumerate(raw_data.get("field_keywords", [])):
            if keyword_pk := self.seen_keywords.get(kwd_node["uri"], None):
                keyword = submission_models.Keyword.objects.get(pk=keyword_pk)
            else:
                kwd_dict = self.fetch_data_dict(kwd_node["uri"])
                keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_dict["name"])
                self.seen_keywords[kwd_node["uri"]] = keyword.pk

            submission_models.KeywordArticle.objects.get_or_create(
                article=article,
                keyword=keyword,
                order=order,
            )
            article.keywords.add(keyword)
        article.save()
        logger.debug("  %s - keywords (%s)", raw_data["field_id"], article.keywords.count())

    def set_issue(self, article, raw_data):
        """Create and set issue / collection and volume."""
        # adapting imports.ojs.importers.get_or_create_issue
        issue_uri = raw_data["field_issue"]["uri"]
        if issue_pk := self.seen_issues.get(issue_uri, None):
            issue = journal_models.Issue.objects.get(pk=issue_pk)
        else:
            issue = self.create_new_issue(article, raw_data)
            # this should not be necessary...
            journal_models.SectionOrdering.objects.filter(issue=issue).delete()

        section_uri = raw_data["field_type"]["uri"]
        if session_pk := self.seen_sections.get(section_uri, None):
            section = submission_models.Section.objects.get(pk=session_pk)
        else:
            section_data = self.fetch_data_dict(raw_data["field_type"]["uri"])
            section_name = section_data["name"]
            if section_name == "review article":
                section_name = "Review Article"
            section, _ = submission_models.Section.objects.get_or_create(
                journal=article.journal,
                name=section_name,
            )
            self.seen_sections[section_uri] = section.pk
        article.section = section

        # Must ensure that a SectionOrdering exists for this issue,
        # otherwise issue.articles.add() will fail.
        #
        # Drupal has a `section_data["weight"]`, but we decided to
        # go with a default ordereing, which seems more "orderly".
        section_order = SECTION_ORDER[section.name]
        journal_models.SectionOrdering.objects.get_or_create(
            issue=issue,
            section=section,
            defaults={"order": section_order},
        )

        # If it should be needed to force-sort articles, check
        # `issue.order_articles_in_sections(sort_field='date_published',
        # order='asc')` in journal.views.mange_issues

        article.primary_issue = issue
        article.save()
        issue.articles.add(article)
        issue.save()
        logger.debug("  %s - issue (%s)", raw_data["field_id"], issue.id)

    def create_new_issue(self, article, raw_data) -> journal_models.Issue:
        """Create a new issue from json data."""
        issue_uri = raw_data["field_issue"]["uri"]
        issue_data = self.fetch_data_dict(issue_uri)

        # in Drupal, volume is a dedicated document type, but in
        # Janeway it is only a number
        # Sanity check (apparently Drupal exposes volume uri in both article and issue json):
        assert raw_data["field_volume"]["uri"] == issue_data["field_volume"]["uri"]
        volume_data = self.fetch_data_dict(issue_data["field_volume"]["uri"])

        volume_num = int(volume_data["field_id"])

        # I don't use the volume's title in Janeway, here I only want
        # to double check data's sanity. The volume's title always has the form
        # "Volume 01, 2002"
        volume_title = volume_data["title"]
        year = 2001 + volume_num
        assert volume_title == f"Volume {volume_num:02}, {year}"

        # Force the issue num to "3" for issue "3-4"
        # article in that issue have publication ID in the form
        # JCOM1203(2013)A03
        # and similar "how to cite":
        # ...JCOM 12(03) (2013) A03.
        if issue_data["field_number"] == "3-4":
            issue_num = 3
        else:
            issue_num = int(issue_data["field_number"])

        # Drupal has "created" and "changed", but they are not what we
        # need here.
        # - I cannot leave this empty, it defaults to now()
        # - I could evince from the issue number(maybe)
        # - I will set it to the first article's published_date, then,
        #   when I wrap up the article "publication process", I will
        #   compare the dates of the issue and the article, and set
        #   the publication date of the issue to the oldest of the two
        date_published = article.date_published

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
        self.seen_issues[issue_uri] = issue.pk

        # Force this to correct previous imports
        issue.date = date_published

        if created:
            issue_type = journal_models.IssueType.objects.get(
                code=issue_type__code,
                journal=article.journal,
            )
            issue.issue_type = issue_type
            issue.save()
            logger.debug("  %s - new issue %s", raw_data["field_id"], issue)

        # issue.short_description or issue.issue_description is shown
        # in the "collections" page.
        description = ""
        if issue_data.get("field_description"):
            description = issue_data["field_description"]
        issue.issue_description = description

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
            logger.debug("  %s - issue cover (%s)", raw_data["field_id"], file_dict["name"])

        return issue

    def set_authors(self, article, raw_data):
        """Find and set the article's authors, creating them if necessary."""
        # For old documents, the corresponding/correspondence/main
        # author info is lost. I get what I can from wjapp, and just
        # use the first author when I don't have the info.
        first_author = None
        for order, author_node in enumerate(raw_data["field_authors"]):
            author_uri = author_node["uri"]
            if author_pk := self.seen_authors.get(author_uri, None):
                author = core_models.Account.objects.get(pk=author_pk)
            else:
                author_dict = self.fetch_data_dict(author_uri)
                # TODO: Here I'm expecting emails to be already lowercase and NFKC-normalized.
                email = author_dict["field_email"]
                if not email:
                    email = f"{author_dict['field_id']}@invalid.com"
                    # Some known authors that do not have an email:
                    # - VACCELERATE: it's a consortium
                    if article.date_published >= HISTORY_EXPECTED_DATE:
                        logger.warning("Missing email for author %s on %s.", author_dict["field_id"], raw_data["nid"])
                # yeah... one was not stripped... 😢
                email = email.strip()
                author, _ = core_models.Account.objects.get_or_create(
                    email=email,
                    first_name=author_dict["field_name"],  # TODO: this contains first+middle; split!
                    last_name=author_dict["field_surname"],
                )
                self.seen_authors[author_uri] = author.pk
                author.add_account_role("author", article.journal)

                # Store away wjapp's userCod
                if author_dict["field_id"]:
                    source = "jcom"
                    assert article.journal.code == "JCOM"
                    try:
                        usercod = int(author_dict["field_id"])
                    except ValueError:
                        if article.date_published >= HISTORY_EXPECTED_DATE:
                            logger.warning(
                                "Non-integer usercod for author %s (%s) on %s (%s)",
                                author_dict["field_surname"],
                                author_dict["field_id"],
                                raw_data["field_id"],
                                raw_data["nid"],
                            )
                    else:
                        mapping, _ = wjs_models.Correspondence.objects.get_or_create(
                            account=author,
                            user_cod=usercod,
                            source=source,
                        )
                        # `used` indicates that this usercod from this source
                        # has been used to create the core.Account record
                        mapping.used = True
                        mapping.save()

            # Arbitrarly selecting the first author as owner and
            # correspondence_author for this article. This is a
            # necessary workaround for those paper that never went
            # through wjapp. For those that we know about (i.e. those
            # that went through wjapp), see
            # https://gitlab.sissamedialab.it/wjs/specs/-/issues/146
            if not first_author:
                first_author = author

            # Add authors to m2m and create an order record
            article.authors.add(author)
            order, _ = submission_models.ArticleAuthorOrder.objects.get_or_create(
                article=article,
                author=author,
                order=order,
            )

        # Set the primary author
        main_author = first_author
        if article.date_published >= HISTORY_EXPECTED_DATE:
            corresponding_author_usercod = self.wjapp.get("userCod", None)
            if corresponding_author_usercod is None:
                logger.warning("Cannot find corresponding author for %s from wjapp", raw_data["field_id"])
            else:
                source = "jcom"
                mapping = wjs_models.Correspondence.objects.get(user_cod=corresponding_author_usercod, source=source)
                main_author = mapping.account
                self.set_author_country(main_author)

        article.owner = main_author
        article.correspondence_author = main_author
        article.save()
        logger.debug("  %s - authors (%s)", raw_data["field_id"], article.authors.count())

    def publish_article(self, article, raw_data):
        """Publish an article."""
        # see src/journal/views.py:1078
        article.stage = submission_models.STAGE_PUBLISHED
        article.snapshot_authors()
        article.close_core_workflow_objects()
        if article.date_published < article.issue.date_published:
            article.issue.date = article.date_published
            article.issue.save()
        article.save()
        logger.debug("  %s - Janeway publication process", raw_data["field_id"])

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
        assert response.status_code == 200, f"Got {response.status_code}!"
        return response.json()

    def process_body(self, article, body: str) -> bytes:
        """Rewrite and adapt body to match Janeway's expectations.

        Take care of
        - TOC (heading levels)
        - how-to-cite

        Images included in body are done elsewhere since they require an existing galley.
        """
        html = lxml.html.fromstring(body)

        # src/themes/material/assets/toc.js expects
        # - the root element of the article must have id="main_article"
        html.set("id", "main_article")
        # - the headings that go in the toc must be h2-level, but Drupal has them at h3-level
        self.promote_headings(html)
        self.drop_toc(html)
        self.drop_how_to_cite(html)
        return lxml.html.tostring(html)

    def promote_headings(self, html: HtmlElement):
        """Promote all h2-h6 headings by one level."""
        for level in range(2, 7):
            for heading in html.findall(f"h{level}"):
                heading.tag = f"h{level-1}"

    def drop_toc(self, html: HtmlElement):
        """Drop the "manual" TOC present in Drupal body content."""
        tocs = html.find_class("tableofcontents")
        if len(tocs) == 0:
            logger.warning("No TOC in WRITEME!!!")
            return

        if len(tocs) > 1:
            logger.error("Multiple TOCs in WRITEME!!!")

        tocs[0].drop_tree()

    def drop_how_to_cite(self, html: HtmlElement):
        """Drop the "manual" How-to-cite present in Drupal body content."""
        htc_h2 = html.xpath(".//h2[text()='How to cite']")
        if len(htc_h2) == 0:
            logger.warning("No How-to-cite in WRITEME!!!")
            return

        if len(htc_h2) > 1:
            logger.error("Multiple How-to-cites in WRITEME!!!")

        htc_h2 = htc_h2[0]
        max_expected = 3
        count = 0
        while True:
            # we are going to `drop_tree` this element, so `getnext()`
            # should provide for new elments
            p = htc_h2.getnext()
            count += 1
            if count > max_expected:
                logger.warning("Too many elements after How-to-cite's H2 in WRITEME!!!")
                break
            if p is None:
                break
            if p.tag != "p":
                break
            if p.text is not None and p.text.strip() == "":
                p.drop_tree()
                break
            p.drop_tree()

        htc_h2.drop_tree()

    # Adapted from plugins/imports/logic.py
    def mangle_images(self, article):
        """Download all <img>s in the article's galley and adapt the "src" attribute."""
        render_galley = article.get_render_galley
        galley_file: core_models.File = render_galley.file
        # NB: cannot use `body` from the json dict here because it has already been modified
        galley_string: str = galley_file.get_file(article)
        html: HtmlElement = lxml.html.fromstring(galley_string)
        images = html.findall(".//img")
        for image in images:
            img_src = image.attrib["src"].split("?")[0]
            img_obj: core_models.File = self.download_and_store_article_file(img_src, article)
            # TBV: the `src` attribute is relative to the article's URL
            image.attrib["src"] = img_obj.label

        with open(galley_file.self_article_path(), "wb") as out_file:
            out_file.write(lxml.html.tostring(html, pretty_print=False))

    def download_and_store_article_file(self, image_source_url, article):
        """Downaload a media file and link it to the article."""
        image_name = image_source_url.split("/")[-1]
        if not image_source_url.startswith("http"):
            if "base_url" not in self.options:
                logger.error("Unknown image src for %s", image_source_url)
                return None
            image_source_url = f"{self.options['base_url']}{image_source_url}"
        image_file = self.uploaded_file(image_source_url, name=image_name)
        new_file: core_models.File = save_galley_image(
            article.get_render_galley,
            request=self.fake_request,
            uploaded_file=image_file,
            label=image_name,  # [*]
        )
        # [*] I tryed to look for some IPTC metadata in the image
        # itself (Exif would probably useless as it is mostly related
        # to the picture technical details) with `exiv2 -P I ...`, but
        # found 3 maybe-useful metadata on ~1600 files and abandoned
        # this idea.
        return new_file

    def data_from_wjapp(self, raw_data):
        """Get data from wjapp."""
        # No point in interrogating wjapp before JCOM moved there
        timestamp = raw_data["field_published_date"]
        if not timestamp:
            logger.error("Missing publication date for %s. This is unexpected...", raw_data["field_id"])
        else:
            if rome_timezone.localize(datetime.fromtimestamp(int(timestamp))) < HISTORY_EXPECTED_DATE:
                return {}

        # Should parametrize url
        url = "https://jcom.sissa.it/jcom/services/jsonpublished"
        apikey = settings.WJAPP_JCOM_APIKEY
        params = {
            "pubId": raw_data["field_id"],
            "apiKey": apikey,
        }
        response = requests.get(url=url, params=params)
        if response.status_code != 200:
            logger.warning(
                "Got HTTP code %s from wjapp for %s",
                response.status_code,
                raw_data["field_id"],
            )
            return {}
        return response.json()

    def set_author_country(self, author: core_models.Account):
        """Set the author's country according to wjapp info."""
        country_name = self.wjapp["countryName"]
        if country_name is None:
            logger.warning("No country for %s", self.wjapp["userCod"])
            return
        country_name = COUNTRIES_MAPPING.get(country_name, country_name)
        try:
            country = core_models.Country.objects.get(name=country_name)
        except core_models.Country.DoesNotExist:
            logger.error("""Unknown country "%s" for %s""", country_name, self.wjapp["userCod"])
        author.country = country
        author.save()
