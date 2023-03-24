"""Data migration POC."""
import os
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
from wjs.jcom_profile.import_utils import (
    decide_galley_label,
    drop_existing_galleys,
    fake_request,
    process_body,
    publish_article,
    query_wjapp_by_pubid,
    set_author_country,
    set_language,
)
from wjs.jcom_profile.utils import from_pubid_to_eid

logger = get_logger(__name__)
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

# Licences:
# - up to last issue of 2008: Copyright Sissa, all right reserved
# - from last issue 2008 to today: CC BY NC ND, but no explicit copyright
# (and all articles in the next-to-last issue have been published 2009-09-19)
LICENCE_CCBY_FROM_DATE = timezone.datetime(2008, 9, 20, tzinfo=rome_timezone)

# Non-peer reviewd sections (#200)
NON_PEER_REVIEWED = ("Editorial", "Commentary")

JOURNALS_DATA = {
    "JCOM": {
        "inception_year": 2001,
        "correspondence_source": "jcom",
        "wjapp_url": "https://jcom.sissa.it/jcom/services/jsonpublished",
        "wjapp_api_key": "WJAPP_JCOM_APIKEY",
        # Default order of sections in any issue.
        # It is not possible to mix different types (e.g. A1 E1 A2...)
        "section_order": {
            "Editorial": (1, "Editorials"),
            "Article": (2, "Articles"),
            "Review Article": (3, "Review Articles"),
            "Practice insight": (4, "Practice insights"),
            "Essay": (5, "Essays"),
            "Focus": (6, "Focus"),
            "Commentary": (7, "Commentaries"),
            "Letter": (8, "Letters"),
            "Book Review": (9, "Book Reviews"),
            "Conference Review": (10, "Conference Reviews"),
        },
        "expected_languages": ("und",),
    },
    "JCOMAL": {
        "inception_year": 2017,
        "correspondence_source": "jcomal",
        "wjapp_url": "https://jcomal.sissa.it/jcomal/services/jsonpublished",
        "wjapp_api_key": "WJAPP_JCOMAL_APIKEY",
        "section_order": {
            "Editorial": (1, "Editorials"),
            "Article": (2, "Articles"),
            "Review Article": (3, "Review Articles"),
            "Practice Insight": (4, "Practice Insights"),
            "Essay": (5, "Essays"),
            "Focus": (6, "Focus"),
            "Commentary": (7, "Commentaries"),
            "Letter": (8, "Letters"),
            "Review": (9, "Reviews"),
        },
        "expected_languages": ("es", "pt-br"),
    },
}


class Command(BaseCommand):
    help = "Import an article."  # NOQA

    # There is no point in importing the same things for every
    # article, so I'm keeping track of what I've already imported
    # to be able to do it once only.
    #
    # Also, these must be class variables, since the process can
    # recurse.
    seen_issues = {}
    seen_keywords = {}
    seen_sections = {}
    seen_authors = {}
    # and when I import children before parents, I can fall into
    # importing the same child twice, so I keep track of articles also
    seen_articles = {}

    def handle(self, *args, **options):
        """Command entry point."""
        self.options = options
        self.prepare()

        for raw_data in self.find_articles():
            if interesting_year := self.options["year"]:
                article_year = rome_timezone.localize(datetime.fromtimestamp(int(raw_data["field_year"]))).year
                if article_year < int(interesting_year):
                    continue
            elif interesting_pubids := self.options["ids"]:
                interesting_pubids = interesting_pubids.split(",")
                if raw_data["field_id"] not in interesting_pubids:
                    continue

            try:
                self.process(raw_data)
            except Exception as e:
                logger.critical("Failed import for %s (%s)!\n%s", raw_data["field_id"], raw_data["nid"], e)
                # raise e

        self.tidy_up()

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
        filters.add_argument(
            "--ids",
            help="Comma-separated lists of pubids to import. E.g. --ids=JCOM_2107_2022_C01,JCOM_2107_2022_C02",
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
            "--article-image-thumbnail",
            action="store_true",
            help="Do create a thumbnail for the article from the large image.",
        )
        parser.add_argument(
            "--journal-code",
            default="JCOM",
            help="Toward which journal to import. Defaults to %(default)s.",
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
        logger.debug("Processing %s (nid=%s)", raw_data["field_id"], raw_data["nid"])
        self.journal_data = JOURNALS_DATA[self.options["journal_code"]]
        self.nid = int(raw_data["nid"])
        # Ugly hack in order not to overlap with JCOM imported papers.
        # Also, do I really need to keep the Drupal node id?
        if self.options["journal_code"] == "JCOMAL":
            self.nid += 10000
        if article_pk := Command.seen_articles.get(raw_data["field_id"], None):
            logger.debug("  %s - already imported. Just retrieving from DB (%s).", raw_data["field_id"], article_pk)
            article = submission_models.Article.objects.get(pk=article_pk)
            return article

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
        self.set_license(article, raw_data)
        publish_article(article)
        self.set_children(article, raw_data)
        return article

    def create_article(self, raw_data):
        """Create a stub for an article with basic metadata.

        - All the rest (author, kwds, etc.) will be added by someone else.
        - If article already exists in Janeway, update it.
        - Empty fields set the value to NULL, but undefined field do
          nothing (the old value is preserverd).

        """
        journal = journal_models.Journal.objects.get(code=self.options["journal_code"])
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
            article.articlewrapper.nid = self.nid
            article.articlewrapper.save()
        assert article.articlewrapper.nid == self.nid
        eid = from_pubid_to_eid(raw_data["field_id"])
        article.page_numbers = eid
        article.save()
        Command.seen_articles.setdefault(raw_data["field_id"], article.pk)
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
        nid = self.nid
        identifiers_models.Identifier.objects.get_or_create(
            identifier=nid,
            article=article,
            id_type="id",
            enabled=True,
        )
        # If we don't refresh the article object, we get an error when saving:
        # Key (render_galley_id)=(29294) is not present in table "core_galley".
        article.refresh_from_db()
        article.save()
        logger.debug("  %s - identifiers set", raw_data["field_id"])

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
        """Find info about the article "attachments", download them and import them as galleys.

        Here we also set the article's language, as it depends on which galleys we find.
        """
        # See also plugin imports.ojs.importers.import_galleys.

        # First, let's drop all existing galleys
        drop_existing_galleys(article)

        # Set default language to English. This will be overridden
        # later if we find a non-English galley.
        #
        # I'm not sure that it is correct to set a language different
        # from English when the doi points to English-only metadata
        # (even if there are two PDF files). But see #194.
        article.language = "eng"

        attachments = raw_data["field_attachments"]
        # Drupal "attachments" are only references to "file" nodes
        for file_node in attachments:
            file_dict = self.fetch_data_dict(file_node["file"]["uri"])
            file_download_url = file_dict["url"]
            uploaded_file = self.uploaded_file(file_download_url, file_dict["name"])
            label, language = decide_galley_label(
                pubid=raw_data["field_id"],
                file_name=file_dict["name"],
                file_mimetype=file_dict["mime"],
            )
            if language and language != "en":
                if article.language != "eng":
                    # We can have 2 non-English galleys (PDF and EPUB),
                    # they are supposed to be of the same language. Not checking.
                    #
                    # If the article language is different from
                    # english, this means that a non-English gally has
                    # already been processed and there is no need to
                    # set the language again.
                    pass
                else:
                    set_language(article, language)
            save_galley(
                article,
                request=fake_request,
                uploaded_file=uploaded_file,  # how does this compare with `save_to_disk`???
                is_galley=True,
                label=label,
                save_to_disk=True,
                public=True,
            )
        logger.debug("  %s - attachments / galleys (%s)", raw_data["field_id"], len(attachments))

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
                request=fake_request,
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
            handle_article_large_image_file(image_file, article, fake_request)
        if self.options["article_image_thumbnail"]:
            image_file.name = self.make_thumb_name(image_file.name)
            handle_article_thumb_image_file(image_file, article, fake_request)
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
        if raw_data["language"] not in self.journal_data["expected_languages"]:
            logger.error(
                "Abstract's language is %s (different from expected %s).",
                raw_data["language"],
                " ".join(self.journal_data["expected_languages"]),
            )

        abstract_dict = raw_data["field_abstract"]
        if not abstract_dict:
            logger.warning("Missing abstract in %s (%s)", raw_data["field_id"], raw_data["nid"])
            article.abstract = ""
            article.save()
            return

        abstract = abstract_dict.get("value", None)
        if abstract and "This item is available only in the original language." in abstract:
            abstract = ""
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
                logger.warning("Missing body in (%s)", raw_data["field_id"])
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
        body_bytes = process_body(body, lang=article.language)
        body_as_file = File(BytesIO(body_bytes), name)
        new_galley = save_galley(
            article,
            request=fake_request,
            uploaded_file=body_as_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=True,
        )
        expected_mimetype = "text/html"
        acceptable_mimetypes = [
            "text/plain",
        ]
        if new_galley.file.mime_type != expected_mimetype:
            if new_galley.file.mime_type not in acceptable_mimetypes:
                logger.warning(
                    "Wrong mime type %s for %s (%s)",
                    new_galley.file.mime_type,
                    new_galley.file.uuid_filename,
                    raw_data["field_id"],
                )
            new_galley.file.mime_type = "text/html"
            new_galley.file.save()
        article.render_galley = new_galley
        self.mangle_images(article)
        article.save()
        logger.debug("  %s - body (as html galley)", raw_data["field_id"])

    def set_keywords(self, article, raw_data):
        """Create and set keywords."""
        # Drop all article's kwds (and KeywordArticles, used for kwd ordering)
        article.keywords.clear()
        for order, kwd_node in enumerate(raw_data.get("field_keywords", [])):
            if keyword_pk := Command.seen_keywords.get(kwd_node["uri"], None):
                keyword = submission_models.Keyword.objects.get(pk=keyword_pk)
            else:
                kwd_dict = self.fetch_data_dict(kwd_node["uri"])
                keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_dict["name"])
                Command.seen_keywords[kwd_node["uri"]] = keyword.pk

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
        if issue_pk := Command.seen_issues.get(issue_uri, None):
            issue = journal_models.Issue.objects.get(pk=issue_pk)
        else:
            issue = self.create_new_issue(article, raw_data)
            # this should not be necessary...
            journal_models.SectionOrdering.objects.filter(issue=issue).delete()

        section_uri = raw_data["field_type"]["uri"]
        if session_pk := Command.seen_sections.get(section_uri, None):
            section = submission_models.Section.objects.get(pk=session_pk)
        else:
            section_data = self.fetch_data_dict(raw_data["field_type"]["uri"])
            section_name = section_data["name"]
            if section_name == "review article":
                section_name = "Review Article"
            # Change all Comment to Commentary. See #211
            if section_name == "Comment":
                section_name = "Commentary"
            section, _ = submission_models.Section.objects.get_or_create(
                journal=article.journal,
                name=section_name,
                defaults={
                    "sequence": self.journal_data["section_order"][section_name][0],
                    "plural": self.journal_data["section_order"][section_name][1],
                },
            )
            Command.seen_sections[section_uri] = section.pk

        article.section = section

        if article.section.name in NON_PEER_REVIEWED:
            article.peer_reviewed = False

        # Must ensure that a SectionOrdering exists for this issue,
        # otherwise issue.articles.add() will fail.
        #
        # Drupal has a `section_data["weight"]`, but we decided to
        # go with a default ordereing, which seems more "orderly".
        section_order = self.journal_data["section_order"][section.name][0]
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
        if raw_data["field_volume"]["uri"] != issue_data["field_volume"]["uri"]:
            logger.critical(
                f'Volume uri in document {raw_data["field_volume"]["uri"]}'
                f' and in issue {issue_data["field_volume"]["uri"]} differ!',
            )
            raise Exception
        volume_data = self.fetch_data_dict(issue_data["field_volume"]["uri"])

        volume_num = int(volume_data["field_id"])

        # I don't use the volume's title in Janeway, here I only want
        # to double check data's sanity. The volume's title always has the form
        # "Volume 01, 2002"
        volume_title = volume_data["title"]
        inception_year = self.journal_data["inception_year"]
        year = inception_year + volume_num
        expected_title = f"Volume {volume_num:02}, {year}"
        if volume_title != expected_title:
            logger.critical(f"Unexpected volume title {volume_title} != {expected_title}!")
            raise Exception

        # Force the issue num to "3" for issue "3-4"
        # article in that issue have publication ID in the form
        # JCOM1203(2013)A03
        # and similar "how to cite":
        # ...JCOM 12(03) (2013) A03.
        if issue_data["field_number"] == "3-4":
            issue_num = 3
        else:
            issue_num = "{:02d}".format(int(issue_data["field_number"]))

        # Drupal has "created" and "changed", but they are not what we
        # need here.
        # - I cannot leave this empty, it defaults to now()
        # - I could evince from the issue number(maybe)
        # - I will set it to the first article's published_date, then,
        #   when I wrap up the article "publication process", I will
        #   compare the dates of the issue and the article, and set
        #   the publication date of the issue to the oldest of the two
        date_published = article.date_published

        issue_type__code = "issue"
        # No title for standard issues.
        issue_title = ""
        if "Special" in issue_data["title"]:
            issue_type__code = "collection"
            issue_title = issue_data["title"][issue_data["title"].find("Special ") :]  # NOQA
        issue, created = journal_models.Issue.objects.get_or_create(
            journal=article.journal,
            volume=volume_num,
            issue=issue_num,
            issue_type__code=issue_type__code,
            defaults={
                "date": date_published,
                "issue_title": issue_title,
            },
        )
        Command.seen_issues[issue_uri] = issue.pk

        issue.issue_title = issue_title

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
            if author_pk := Command.seen_authors.get(author_uri, None):
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
                author, created = core_models.Account.objects.get_or_create(
                    email=email,
                    defaults={
                        "first_name": author_dict["field_name"],
                        "last_name": author_dict["field_surname"],
                    },
                )
                if not created:
                    if author.first_name != author_dict["field_name"]:
                        logger.error(
                            f'Different first name {author_dict["field_name"]}'
                            f" for {author.email} ({author.first_name})",
                        )
                    if author.last_name != author_dict["field_surname"]:
                        logger.error(
                            f'Different last name {author_dict["field_surname"]}'
                            f" for {author.email} ({author.last_name})",
                        )

                Command.seen_authors[author_uri] = author.pk
                author.add_account_role("author", article.journal)

                # Store away wjapp's userCod
                if author_dict["field_id"]:
                    source = self.journal_data["correspondence_source"]
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
                source = self.journal_data["correspondence_source"]
                mapping = wjs_models.Correspondence.objects.get(user_cod=corresponding_author_usercod, source=source)
                main_author = mapping.account
                set_author_country(main_author, self.wjapp)

        article.owner = main_author
        article.correspondence_author = main_author
        article.save()
        logger.debug("  %s - authors (%s)", raw_data["field_id"], article.authors.count())

    def set_license(self, article, raw_data):
        """Set the license (based on the publication date)."""
        if article.date_published < LICENCE_CCBY_FROM_DATE:
            article.license = self.license_copyright
        else:
            article.license = self.license_ccbyncnd
        article.save()

    def uploaded_file(self, url, name):
        """Download a file from the given url and upload it into Janeway."""
        response = requests.get(url, auth=self.basic_auth)
        return File(BytesIO(response.content), name)

    def fetch_data_dict(self, uri):
        """Fetch the json data from the given uri.

        Append .json to the uri, do a GET and return the result as a dictionary.
        """
        lang_code = "es"
        uri_nolang = uri.replace(f"/{lang_code}/", "/")
        if uri_nolang != uri:
            # Too much noise: logger.debug(f"Removed lang code {lang_code} from {uri}")
            uri = uri_nolang
        uri += ".json"
        response = requests.get(uri, auth=self.basic_auth)
        if response.status_code != 200:
            logger.critical(f"Got {response.status_code} for {uri}!")
            raise FileNotFoundError()
        return response.json()

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
            request=fake_request,
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
        return query_wjapp_by_pubid(
            raw_data["field_id"],
            url=self.journal_data["wjapp_url"],
            api_key=self.journal_data["wjapp_api_key"],
        )

    def prepare(self):
        """Run una-tantum operations before starting any import."""
        logger.debug("Setup licences.")
        # This one is the standard one created by Janeway for every
        # journal. We just know it's there.
        self.license_ccbyncnd = submission_models.Licence.objects.get(
            short_name="CC BY-NC-ND 4.0",
            journal=journal_models.Journal.objects.get(code=self.options["journal_code"]),
        )
        if "NoDerivatives" not in self.license_ccbyncnd.name:
            logger.warning('Please fix the text of the ND licenses: should read "NoDerivatives".')

        # This one is Sissa-special, we must create it
        self.license_copyright, created = submission_models.Licence.objects.get_or_create(
            name="© Sissa",
            short_name="Sissa",
            text="""Copyright Sissa, all right reserved""",
            url="https://medialab.sissa.it/en",
            available_for_submission=False,
            journal=self.license_ccbyncnd.journal,
        )

        self.correct_existing_users_metadata()

    def tidy_up(self):
        """Run una-tantum operations at the end of the import process."""
        if self.options["journal_code"] == "JCOMAL":
            translate_kwds()

    def set_children(self, article, raw_data):
        """Process children if present (mainly for commentaries)."""
        # "field_document" is always present, but can be an empty list
        if not raw_data["field_document"]:
            return
        assert raw_data["field_subdoc"] is False, "We don't expect to ever see nephews!!!"
        genealogy, created = wjs_models.Genealogy.objects.get_or_create(parent=article)
        if not created:
            genealogy.children.clear()
        for child in raw_data["field_document"]:
            child_raw_data = self.fetch_data_dict(child["uri"])
            logger.debug("  %s - retrieving child %s", raw_data["field_id"], child_raw_data["field_id"])
            child_article = self.process(child_raw_data)
            genealogy.children.add(child_article)

    def correct_existing_users_metadata(self):
        """Correct metadata of some known users."""
        # country Mexico for author especializacion@dgdc.unam.mx (Spain).
        # Set in wjapp (mg 2023-03-24)

        # first name Carlos for chfioravanti@gmail.com (Carlos Henrique)
        # Set in Drupal (mg 2023-03-23)

        # first name Luiz Felipe for luiz.felipe@ufg.br (Luiz Felipe Fernandes)
        a = core_models.Account.objects.get(email="luiz.felipe@ufg.br")
        a.first_name = "Luiz Felipe"
        a.last_name = "Fernandes Neves"
        a.save()

        # last name Crúz-Mena for cruzmena@dgdc.unam.mx (Cruz-Mena)
        a = core_models.Account.objects.get(email="cruzmena@dgdc.unam.mx")
        a.last_name = "Crúz-Mena"
        a.save()

        # last name de Régules for sergioderegules@gmail.com (de Regules)
        a = core_models.Account.objects.get(email="sergioderegules@gmail.com")
        a.last_name = "de Régules"
        a.save()

        # last name Fernandes Neves for luiz.felipe@ufg.br (Neves)
        a = core_models.Account.objects.get(email="luiz.felipe@ufg.br")
        a.last_name = "Fernandes Neves"
        a.save()

        # last name Herrera Lima for shl@iteso.mx (Herrera-Lima)
        a = core_models.Account.objects.get(email="shl@iteso.mx")
        a.last_name = "Herrera Lima"
        a.save()

        # last name Reynoso Haynes for elareyno@dgdc.unam.mx (Reynoso-Haynes)
        a = core_models.Account.objects.get(email="elareyno@dgdc.unam.mx")
        a.last_name = "Reynoso Haynes"
        a.save()

        # last name Sánchez Mora for masanche@dgdc.unam.mx (Sánchez-Mora)
        a = core_models.Account.objects.get(email="masanche@dgdc.unam.mx")
        a.last_name = "Sánchez Mora"
        a.save()


def translate_kwds():
    """Translate JCOMAL kwds."""
    # Adapted from https://jcomal.sissa.it/jcomal/help/keywordsList.jsp
    keyword_list = (
        ("Citizen science", "Ciência cidadã", "Ciencia ciudadana"),
        ("Community action", "Ação comunitária", "Acción comunitaria"),
        ("Environmental communication", "Comunicação ambiental", "Comunicación ambiental"),
        ("Health communication", "Comunicação de saúde", "Comunicación en salud"),
        (
            "History of public communication of science",
            "História da comunicação pública da ciência",
            "Historia de la comunicación pública de la ciencia",
        ),
        (
            "History of science communication",
            "História da divulgação científica ",
            "Historia de la divulgación de la ciencia",
        ),
        ("Informal learning", "Aprendizagem informal", "Aprendizaje informal"),
        ("Outreach", "Extensão universitária", "Extensión universitaria"),
        (
            "Participation and science governance",
            "Participação e governança científica",
            "Participación y gobernanza de la ciencia",
        ),
        (
            "Popularization of science and technology",
            "Popularização da ciência e da tecnologia",
            "Popularización de la ciencia y la tecnología",
        ),
        (
            "Professionalism, professional development and training in science communication",
            "Profissionalismo, desenvolvimento profissional e formação em divulgação científica",
            "Profesionalidad, desarrollo profesional y formación en divulgación científica",
        ),
        (
            "Public engagement with science and technology",
            "Engajamento público com a ciência e a tecnologia",
            "Compromiso público con la ciencia y la tecnología",
        ),
        (
            "Public perception of science and technology",
            "Percepção pública de ciência e tecnologia",
            "Percepción pública de la ciencia y la tecnología",
        ),
        (
            "Public understanding of science and technology",
            "Entendimento público de ciência e tecnologia",
            "Comprensión pública de la ciencia y la tecnología",
        ),
        (
            "Representations of science and technology",
            "Representações da ciência e da tecnologia",
            "Representaciones de la ciencia y la tecnología",
        ),
        ("Risk communication", "Comunicação de risco", "Comunicación de riesgos"),
        ("Scholarly communication", "Comunicação acadêmica", "Comunicación académica"),
        ("Science and media", "Ciência e mídia", "Ciencia y medios"),
        ("Science and policy-making", "Ciência e formulação de políticas", "Ciencia y formulación de políticas"),
        ("Science and Society", "Ciência e Sociedade ", "Ciencia y Sociedad"),
        (
            "Science and technology, art and literature",
            "Ciência e tecnologia, arte e literatura",
            "Ciencia y tecnología, arte y literatura",
        ),
        ("Science centres and museums", "Centros e museus de ciência", "Centros y museos de ciencia"),
        (
            "Science communication in the developing world",
            "Divulgação científica nos países em desenvolvimento",
            "Divulgación de la ciencia en los países en desarrollo",
        ),
        (
            "Science communication: theory and models",
            "Divulgação científica: teoria e modelos",
            "Comunicación científica: teoría y modelos",
        ),
        ("Science education", "Educação científica", "Enseñanza científica"),
        ("Science journalism", "Jornalismo científico", "Periodismo científico"),
        ("Science writing", "Redação científica", "Escritura científica"),
        ("Social appropriation of science", "Apropriação social da ciência", "Apropriación social de la ciencia"),
        ("Social inclusion", "Inclusão social", "Inclusión social"),
        (
            "Social studies of science and technology",
            "Estudos sociais da ciência e da tecnologia",
            "Estudios sociales de la ciencia y la tecnología",
        ),
        ("Visual communication", "Comunicação visual", "Comunicación visual"),
        ("Women in science", "Mulheres na ciência", "La mujer en la ciencia"),
    )
    for eng_word, por_word, spa_word in keyword_list:
        try:
            keyword = submission_models.Keyword.objects.get(word=eng_word)
        except submission_models.Keyword.DoesNotExist:
            logger.error(f'Kwd "{eng_word}" does not exist. Please check!')
            continue
        logger.warning(f"Please translate {keyword} to {por_word} and {spa_word}")
