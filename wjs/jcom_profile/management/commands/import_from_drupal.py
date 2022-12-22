"""Data migration POC."""

from collections import namedtuple
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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
        self.options = options
        for raw_data in self.find_articles():
            # TODO: cycle through pagination
            try:
                self.process(raw_data)
            except Exception as e:
                logger.critical("Failed import for %s!\n%s", raw_data["nid"], e)
                # raise e

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--id",
            help='Pubication ID of the article to process (e.g. "JCOM_2106_2022_A01").'
            " If not given, all articles are queried and processed.",
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
            "--skip-files",
            help="Skip files download/uplaod (only import metadata).",
            action="store_true",
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
        for date, field_name in (
            ("field_received_date", "date_submitted"),
            ("field_accepted_date", "date_accepted"),
            ("field_published_date", "date_published"),
        ):
            if not raw_data[date]:
                logger.warning("Missing %s in %s", date, raw_data["nid"])
            else:
                setattr(article, field_name, rome_timezone.localize(datetime.fromtimestamp(int(raw_data[date]))))
        article.save()
        logger.debug("  %s - history", raw_data["field_id"])

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
        if not abstract_dict:
            logger.warning("Missing abstract in %s", raw_data["nid"])
        else:
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
            logger.debug("  %s - abstract", raw_data["field_id"])

        if self.options["skip_files"]:
            article.save()
            return

        # Body (NB: it's a galley with mime-type in files.HTML_MIMETYPES)
        body_dict = raw_data["body"]
        if not body_dict:
            logger.warning("Missing body in %s", raw_data["nid"])
            article.save()
            return
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
        logger.debug("  %s - body (as html galley)", raw_data["field_id"])

    def set_files(self, article, raw_data):
        """Find info about the article "attachments", download them and import them as galleys."""
        # First, let's drop all existing files
        # see plugin imports.ojs.importers.import_galleys
        for galley in article.galley_set.all():
            galley.unlink_files()
            galley.delete()

        if not self.options["skip_files"]:
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
            logger.debug("  %s - attachments (as galleys)", raw_data["field_id"])

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
        logger.debug("  %s - keywords (%s)", raw_data["field_id"], article.keywords.count())

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
        # TODO:
        # - can I leave this empty??? ⇨ no, it defaults to now()
        # - should I evince from the issue number??? ⇨ maybe...
        # - maybe I can use the publication date of the issue's editorial? ⇨ not all issues have an editorial
        date_published = timezone.datetime(year, 1, 1)

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

        if issue_data.get("description"):
            logger.error("Matteo doesn't expect this. Don't confuse him please!!!")
            issue.issue_description = issue_data["description"]
        # issue.short_description or issue.issue_description is shown
        # in the "collections" page. Temporarily using the title. See
        # also https://gitlab.sissamedialab.it/wjs/specs/-/issues/145
        issue.issue_description = issue_data["title"]

        issue.save()

        if not self.options["skip_files"]:
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
        logger.debug("  %s - issue (%s)", raw_data["field_id"], issue.id)

    def set_authors(self, article, raw_data):
        """Find and set the article's authors, creating them if necessary."""
        # TODO: article.owner = user
        # TODO: article.authors = [user]
        # article.correspondence_author = ???  # This info is missing / lost
        # Add authors
        first_author = None
        for order, author_node in enumerate(raw_data["field_authors"]):
            author_dict = self.fetch_data_dict(author_node["uri"])
            # TODO: Here I'm expecting emails to be already lowercase and NFKC-normalized.
            email = author_dict["field_email"]
            if not email:
                email = f"{author_dict['field_id']}@invalid.com"
                logger.warning("Missing email for %s.", raw_data["nid"])
            author, _ = core_models.Account.objects.get_or_create(
                email=email,
                first_name=author_dict["field_name"],  # TODO: this contains first+middle; split!
                last_name=author_dict["field_surname"],
            )
            author.add_account_role("author", article.journal)

            # Store away wjapp's userCod
            if author_dict["field_id"]:
                source = "jcom"
                assert article.journal.code == "JCOM"
                try:
                    usercod = int(author_dict["field_id"])
                except ValueError:
                    logger.warning(
                        "Non-integer usercod for author %s on %s: %s.",
                        author_dict["field_surname"],
                        raw_data["nid"],
                        author_dict["field_id"],
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
        article.owner = first_author
        article.correspondence_author = first_author
        article.save()
        logger.debug("  %s - authors (%s)", raw_data["field_id"], article.authors.count())

    def publish_article(self, article, raw_data):
        """Publish an article."""
        # see src/journal/views.py:1078
        article.stage = submission_models.STAGE_PUBLISHED
        article.snapshot_authors()
        article.close_core_workflow_objects()
        article.date_published = timezone.now() - timedelta(days=1)
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
