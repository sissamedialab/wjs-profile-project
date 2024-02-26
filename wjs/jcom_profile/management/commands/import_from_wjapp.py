"""Data migration POC."""
import datetime
import os
import re
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

import lxml.html
from core.models import Account
from core.models import File as JanewayFile
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db.models import Count, Q, QuerySet
from identifiers import models as identifiers_models
from identifiers.models import Identifier
from jcomassistant import make_epub, make_xhtml
from jcomassistant.utils import (
    correct_translation,
    find_and_rename_main_galley,
    preprocess_xmlfile,
    read_tex,
    rebuild_translation_galley,
    tex_filename_from_wjs_ini,
)
from journal import models as journal_models
from lxml.html import HtmlElement
from production.logic import save_galley, save_galley_image, save_supp_file
from submission import models as submission_models
from utils.logger import get_logger

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_utils import (
    decide_galley_label,
    drop_existing_galleys,
    drop_render_galley,
    evince_language_from_filename_and_article,
    fake_request,
    process_body,
    publish_article,
    query_wjapp_by_pubid,
    set_author_country,
    set_language,
)
from wjs.jcom_profile.management.commands.import_from_drupal import (
    JOURNALS_DATA,
    NON_PEER_REVIEWED,
    rome_timezone,
)
from wjs.jcom_profile.utils import from_pubid_to_eid, generate_doi

from ...models import Recipient

# Map wjapp article types to Janeway section names
SECTIONS_MAPPING = {
    "editorial": "Editorial",
    "article": "Article",
    "review article": "Review Article",
    "practice insight": "Practice Insight",
    "essay": "Essay",
    "focus": "Focus",
    "commentary": "Commentary",
    "comment": "Commentary",  # comment ↔ commentary
    "letter": "Letter",
    "book review": "Book Review",
    "conference review": "Conference Review",
}


class UnknownSection(Exception):
    pass


logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Import an article from wjapp."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        self.options = options
        self.journal_data = JOURNALS_DATA[options["journal-code"]]
        self.read_from_watched_dir()

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--watch-dir",
            default="/home/wjs/incoming",
            help="Where to look for zip files to process. Defaults to %(default)s",
        )
        parser.add_argument(
            "--store-dir",
            default="/home/wjs/received-from-wjapp",
            help="Where to keep zip files received from wjapp (and processed). Defaults to %(default)s",
        )
        parser.add_argument(
            "journal-code",
            choices=["JCOM", "JCOMAL"],
            help="Toward which journal to import.",
        )
        parser.add_argument(
            "--only_regenerate_html_galley",
            action="store_true",
            help="Only regenerate HTML galleys without re-creating the article",
        )
        parser.add_argument(
            "--skip-galley-generation",
            action="store_true",
            help="Do not generate HTML and EPUB galleys (useful only in debug)",
        )

    def read_from_watched_dir(self):
        """Read zip files from the watched folder and start the import process."""
        if not os.path.isdir(self.options["watch_dir"]):
            logger.critical(f"No such directory {self.options['watch_dir']}")
            raise FileNotFoundError(f"No such directory {self.options['watch_dir']}")
        watch_dir = Path(self.options["watch_dir"])
        files = sorted(watch_dir.glob("*.zip"))
        for zip_file in files:
            self.process(watch_dir / zip_file)

    def process(self, zip_file):
        """Uncompress the zip file, and create the importing Article from the XML metadata."""
        logger.debug(f"Looking at {zip_file}")
        # wjapp provides an "atomic" upload: when the file if present,
        # it is ready and fully uploaded.

        # Copy to "storage" dir and unzip there in a temporary dir
        store_dir = Path(self.options["store_dir"])
        store_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(zip_file, store_dir)
        zip_file = store_dir / os.path.basename(zip_file)

        tmpdir = Path(tempfile.mkdtemp(dir=store_dir))
        with zipfile.ZipFile(zip_file, "r") as zip_ref:
            zip_ref.extractall(tmpdir)

        # Expect to find a folder
        workdir = os.listdir(tmpdir)
        if len(workdir) != 1:
            logger.error(f"Found {len(workdir)} files in the root of the zip file. Trying the first: {workdir[0]}")
        workdir = tmpdir / Path(workdir[0])

        # Expect to find one XML (and some PDF files)
        xml_files = list(workdir.glob("*.xml"))
        if len(xml_files) == 0:
            logger.critical(f"No XML file found in {zip_file}. Quitting and leaving a mess...")
            raise FileNotFoundError(f"No XML file found in {zip_file}")
        if len(xml_files) > 1:
            logger.warning(f"Found {len(xml_files)} XML files in {zip_file}. Using the first one {xml_files[0]}")
        xml_file = xml_files[0]

        # Need to read the TeX source in order to correct the XML
        # (mainly authors names and authors order)
        src_folder = workdir / "src"
        if not os.path.exists(src_folder):
            raise FileNotFoundError(f"Missing src folder {src_folder}")
        tex_filenames = list(src_folder.glob("JCOM*.tex"))
        multilingual = False
        if len(tex_filenames) > 1:
            # Assuming multilingual paper.
            multilingual = True

            # Find the tex source with the shortest name, because
            # usually we have the English version named <pubid>.tex
            # and the "translations" as <pubid>_<lang>.tex
            tex_filenames.sort()
            logger.warning(f"Found {len(tex_filenames)} tex files. Using {tex_filenames[0]}")

        tex_filename = tex_filenames[0]

        alternative_tex_filename = None
        wjs_ini = src_folder / "wjs.ini"
        if wjs_ini.exists():
            alternative_tex_filename = tex_filename_from_wjs_ini(wjs_ini)
            alternative_tex_filename = os.path.join(src_folder, alternative_tex_filename)
            if alternative_tex_filename != "":
                logger.warning(f"Found wjs.ini. Using {alternative_tex_filename}. Untested with multilingual papers.")
            else:
                alternative_tex_filename = None

        tex_data = read_tex(tex_filename)

        xml_obj = preprocess_xmlfile(xml_file, tex_data)

        if self.options["only_regenerate_html_galley"]:
            self.regen_html_galley(xml_obj, tex_filename, alternative_tex_filename)
            # Cleanup
            shutil.rmtree(tmpdir)
            return

        # extract pubid, create article
        article, pubid = self.create_article(xml_obj)
        self.set_keywords(article, xml_obj, pubid)
        issue = self.set_issue(article, xml_obj, pubid)
        self.set_section(article, xml_obj, pubid, issue)
        self.set_authors(article, xml_obj)
        self.set_license(article)
        self.set_pdf_galleys(article, xml_obj, pubid, workdir)
        self.set_supplementary_material(article, pubid, workdir)

        if not self.options["skip_galley_generation"]:
            try:
                # Generate the full-text html from the TeX sources
                html_galley_filename = make_xhtml.make(tex_filename, alternative_tex_filename=alternative_tex_filename)
                self.set_html_galley(article, html_galley_filename)

                # Generate the EPUB from the TeX sources
                epub_galley_filename = make_epub.make(html_galley_filename, tex_data=tex_data)
                self.set_epub_galley(article, epub_galley_filename, pubid)

            except Exception as exception:
                logger.error(f"Generation of HTML and EPUB galleys failes: {exception}")

            if multilingual:
                try:
                    for translation_tex_filename in tex_filenames[1:]:
                        correct_translation(translation_tex_filename, tex_filename)

                        translation_html_galley_filename = make_xhtml.make(translation_tex_filename)
                        # TODO: verify if Janeway can manage tranlastions of HTML galley

                        translation_tex_data = read_tex(translation_tex_filename)
                        translation_epub_galley_filename = make_epub.make(
                            translation_html_galley_filename,
                            tex_data=translation_tex_data,
                        )
                        self.set_epub_galley(article, translation_epub_galley_filename, pubid)

                except Exception as exception:
                    logger.error(
                        f"Generation of HTML and EPUB galley failed for {translation_tex_filename}: {exception}",
                    )

        self.set_doi(article)
        publish_article(article)
        # Cleanup
        shutil.rmtree(tmpdir)

    def regen_html_galley(self, xml_obj, tex_filename, alternative_tex_filename):
        """Regenerate only the render-galley."""
        # extract pubid, get article
        pubid = xml_obj.find("//document/articleid").text
        journal = journal_models.Journal.objects.get(code=self.options["journal-code"])
        logger.debug(f"getting {pubid}")
        article = submission_models.Article.get_article(
            journal=journal,
            identifier_type="pubid",
            identifier=pubid,
        )
        drop_render_galley(article)
        # Generate the full-text html from the TeX sources
        html_galley_filename = make_xhtml.make(tex_filename, alternative_tex_filename=alternative_tex_filename)
        self.set_html_galley(article, html_galley_filename)

    def set_html_galley(self, article, html_galley_filename):
        """Set the give file as HTML galley."""
        html_galley_text = open(html_galley_filename).read()
        galley_language = evince_language_from_filename_and_article(html_galley_filename, article)
        processed_html_galley_as_bytes = process_body(html_galley_text, style="wjapp", lang=galley_language)
        name = "body.html"
        html_galley_file = File(BytesIO(processed_html_galley_as_bytes), name)
        label = "HTML"
        new_galley = save_galley(
            article,
            request=fake_request,
            uploaded_file=html_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=True,
            html_prettify=False,
        )
        expected_mimetype = "text/html"
        acceptable_mimetypes = [
            "text/plain",
        ]
        if new_galley.file.mime_type != expected_mimetype:
            if new_galley.file.mime_type not in acceptable_mimetypes:
                logger.warning(f"Wrong mime type {new_galley.file.mime_type} for {html_galley_filename}")
            new_galley.file.mime_type = expected_mimetype
            new_galley.file.save()
        article.render_galley = new_galley
        article.save()
        mangle_images(article)

    def set_epub_galley(self, article, epub_galley_filename, pubid):
        """Set the give file as EPUB galley."""
        # We should be working in the folder where
        # `epub_galley_filename` resides, so the file name and the
        # file path are the same.
        epub_galley_file = File(open(epub_galley_filename, "rb"), name=epub_galley_filename)
        label = "EPUB"
        file_mimetype = "application/epub+zip"
        label, language = decide_galley_label(pubid, file_name=epub_galley_filename, file_mimetype=file_mimetype)
        # language is set when we process the PDF galleys
        save_galley(
            article,
            request=fake_request,
            uploaded_file=epub_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=True,
        )
        logger.debug(f"EPUB galley {label} set onto {pubid}")

    def set_authors(self, article, xml_obj):
        """Find and set the article's authors, creating them if necessary."""
        wjapp = query_wjapp_by_pubid(
            article.get_identifier("pubid"),
            url=self.journal_data["wjapp_url"],
            api_key=self.journal_data["wjapp_api_key"],
        )
        # The "source" of this author's info, used for future reference
        source = self.journal_data["correspondence_source"]
        pubid = article.get_identifier("pubid")
        # The first set of <author> elements (the one outside
        # <document>) is guaranteed to have the names and the order
        # correct. Ignore the rest (beware "//author" != "/author")
        for order, author_obj in enumerate(xml_obj.findall("/author")):
            # Don't confuse user_cod (camelcased originally) that is
            # the pk of the user in wjapp with Account.id in Janeway.
            user_cod = author_obj.get("authorid")
            imported_email = author_obj.get("email")
            if not imported_email:
                imported_email = f"{user_cod}@invalid.com"
                logger.error(f"No email for author {user_cod} on {pubid}. Using {imported_email}")
            # just in case:
            imported_email = imported_email.strip()

            # Check if we know this person form some other journal or by email
            account_created = False
            mappings = wjs_models.Correspondence.objects.filter(
                Q(user_cod=user_cod, source=source) | Q(email=imported_email),
            )
            if mappings.count() == 0:
                # We never saw this person in other journals.
                author, account_created = Account.objects.get_or_create(
                    email=imported_email,
                    defaults={
                        "first_name": author_obj.get("firstname"),
                        "last_name": author_obj.get("lastname"),
                    },
                )
                mapping = wjs_models.Correspondence.objects.create(
                    user_cod=user_cod,
                    source=source,
                    email=imported_email,
                    account=author,
                )
            elif mappings.count() >= 1:
                # We know this person from another journal
                mapping = check_mappings(mappings, imported_email, user_cod, source)

            author = mapping.account

            # `used` indicates that this usercod from this source
            # has been used to create the core.Account record
            if account_created:
                mapping.used = True
                mapping.save()

                link_to_existing_newsletter_recipient_maybe(author, article.journal)

            # Sanity check: it is possible that data from Janeway and from wjapp differ.
            # See also https://gitlab.sissamedialab.it/wjs/specs/-/issues/380
            imported_first_name = author_obj.get("firstname")
            if author.first_name != imported_first_name:
                logger.warning(
                    f"Different first name for {author.id}."
                    f" Janeway {author.first_name} vs. new {imported_first_name}",
                )
                author.first_name = imported_first_name
                author.save()

            imported_last_name = author_obj.get("lastname")
            if author.last_name != imported_last_name:
                logger.warning(
                    f"Different last name for {author.id}."
                    f" Janeway {author.last_name} vs. new {imported_last_name}",
                )
                author.last_name = imported_last_name
                author.save()

            assert mapping.email == imported_email
            if author.email != imported_email:
                logger.warning(
                    f"Different email for {author.id}. Janeway {author.email} vs. new {imported_email}",
                )
                author.email = imported_email
                author.save()

            author.add_account_role("author", article.journal)

            # Add authors to m2m and create an order record
            article.authors.add(author)
            # Warning: take care of the case when an article has been
            # imported, then the order of the authors changed and the
            # article is being re-imported: get_or_create the order
            # object with "defaults" and force the order if necessary
            # (i.e., do not use "order" to filter the
            # ArticleAuthorOrder table).
            order_obj, order_obj_created = submission_models.ArticleAuthorOrder.objects.get_or_create(
                article=article,
                author=author,
                defaults={
                    "order": order,
                },
            )
            if not order_obj_created:
                order_obj.order = order
                order_obj.save()

        # Set the primary author
        corresponding_author_usercod = wjapp.get("userCod")  # Expect to alway find something!
        mapping = wjs_models.Correspondence.objects.filter(
            user_cod=corresponding_author_usercod,
            source=source,
        ).first()
        main_author = mapping.account
        set_author_country(main_author, wjapp)
        article.owner = main_author
        article.correspondence_author = main_author
        article.save()
        logger.debug(f"Set {article.authors.count()} authors onto {pubid}")

    def set_keywords(self, article: submission_models.Article, xml_obj, pubid):
        """Set the keywords."""
        # Drop all article's kwds (and KeywordArticles, used for kwd ordering)
        article.keywords.clear()
        for order, kwd_obj in enumerate(xml_obj.findall("//document/keyword")):
            # Janeway's keywords are a simple model with a "word" field for the kwd text
            kwd_word = kwd_obj.text.strip()
            # in wjapp-JCOMAL, the keyword string contains all three
            # languages separated by ";". The first is English.
            if self.options["journal-code"] == "JCOMAL":
                kwd_word = kwd_word.split(";")[0].strip()
            keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_word)
            if created:
                logger.warning(f'Created keyword "{kwd_word}" for {pubid}. Kwds are not often created. Please check!')
                manage_newsletter_subscriptions(keyword, article.journal)

            # Always link kwd to journal (remember that journals have a set of kwds!)
            #
            # Even if the kwd was not created, it is possible that we got a pre-existing kwd that was linked only to
            # another journal.
            #
            # P.S. `add` won't duplicate an existing relation
            # https://docs.djangoproject.com/en/3.2/ref/models/relations/
            article.journal.keywords.add(keyword)

            submission_models.KeywordArticle.objects.get_or_create(
                article=article,
                keyword=keyword,
                order=order,
            )
            logger.debug(f"Keyword {kwd_word} set at order {order}")
            article.keywords.add(keyword)
        article.save()

    def set_issue(self, article, xml_obj, pubid):
        """Set the issue."""
        # Issue / volume
        # wjapp's XML has the same info in several places. Let's do some sanity check.
        vol_obj_a = xml_obj.find("//volume")
        vol_obj_b = xml_obj.find("//document/volume")
        issue_obj_a = xml_obj.find("//issue")
        issue_obj_b = xml_obj.find("//document/issue")
        volume = vol_obj_a.get("volumeid")
        issue = issue_obj_a.get("issueid")
        if vol_obj_a.get("volumeid") != vol_obj_b.get("volumeid"):
            logger.error(f"Mismatching volume ids for {pubid}. Using {volume}")
        if issue_obj_a.get("issueid") != issue_obj_b.get("issueid"):
            logger.error(f"Mismatching issue ids for {pubid}. Using {issue}")
        # The first issue element also has a reference to the volumeid...
        if issue_obj_a.get("volumeid") != volume:
            logger.error(f"Mismatching issue/volume ids for {pubid}.")
        if not issue.startswith(volume):
            logger.error(f'Unexpected issueid "{issue}". Trying to proceed anyway.')

        issue = issue.replace(volume, "")
        issue = int(issue)
        volume = int(volume)

        # More sanity check: the volume's title always has the form
        # "Volume 01, 2002"
        volume_title = vol_obj_a.text
        inception_year = self.journal_data["inception_year"]
        year = inception_year + volume
        expected_title = f"Volume {volume:02}, {year}"
        if volume_title != expected_title:
            logger.error(f'Unexpected volume title "{volume_title}"')

        # If the issue's text has a form different from
        # "Issue 01, 2023"
        # then it's a special issue (aka "collection")
        issue_type__code = "issue"
        issue_title = ""
        if "Special" in issue_obj_a.text:
            logger.warning(f'Unexpected issue title "{issue_title}", consider this a "special issue"')
            issue_type__code = "collection"
            issue_title = issue_obj_a.text

        # NB: we 0-pad the issue "number"
        issue = f"{issue:02}"

        issue, created = journal_models.Issue.objects.get_or_create(
            journal=article.journal,
            volume=volume,
            issue=issue,
            issue_type__code=issue_type__code,
            defaults={
                "date": article.date_published,  # ⇦ delicate
                "issue_title": issue_title,
            },
        )

        issue.issue_title = issue_title

        if created:
            issue_type = journal_models.IssueType.objects.get(
                code=issue_type__code,
                journal=article.journal,
            )
            issue.issue_type = issue_type
            issue.save()

        issue.save()
        return issue

    def set_section(self, article, xml_obj, pubid, issue):
        """Set the section and the section's order in the issue."""
        # not needed(?): journal_models.SectionOrdering.objects.filter(issue=issue).delete()
        section_name = xml_obj.find("//document/type").text
        if section_name not in SECTIONS_MAPPING:
            logger.critical(f'Unknown article type "{section_name}" for {pubid}')
            raise UnknownSection(f'Unknown article type "{section_name}" for {pubid}')
        section_name = SECTIONS_MAPPING.get(section_name)
        section_order_tuple = self.journal_data["section_order"]
        section, created = submission_models.Section.objects.get_or_create(
            journal=article.journal,
            name=section_name,
            defaults={
                "sequence": section_order_tuple[section_name][0],
                "plural": section_order_tuple[section_name][1],
            },
        )
        if created:
            logger.warning(
                f'Created section "{section_name}" for {pubid}. Sections are not ofter created. Please check!',
            )

        article.section = section

        if article.section.name in NON_PEER_REVIEWED:
            article.peer_reviewed = False

        # Must ensure that a SectionOrdering exists for this issue,
        # otherwise issue.articles.add() will fail.
        #
        section_order = section_order_tuple[section.name][0]
        journal_models.SectionOrdering.objects.get_or_create(
            issue=issue,
            section=section,
            defaults={"order": section_order},
        )

        article.primary_issue = issue
        article.save()
        issue.articles.add(article)
        issue.save()
        logger.debug(f"Issue {issue.volume}({issue.issue}) set for {pubid}")

    def set_license(self, article):
        """Set the license (always the same)."""
        article.license = submission_models.Licence.objects.get(short_name="CC BY-NC-ND 4.0", journal=article.journal)
        article.save()

    def set_pdf_galleys(self, article, xml_obj, pubid, workdir):
        """Set the PDF galleys: original language and tranlastion."""
        # PDF galleys
        # Should find one file (common case) or two files (original language + english translation)
        pdf_files = list(workdir.glob("*.pdf"))
        sanity_check_pdf_filenames(pdf_files)

        # Set default language to English. This will be overridden
        # later if we find a non-English galley.
        #
        # I'm not sure that it is correct to set a language different
        # from English when the doi points to English-only metadata
        # (even if there are two PDF files). But see #194.
        article.language = "eng"
        article.save()

        if len(pdf_files) == 0:
            logger.critical(f"No PDF file found in {workdir}. Doing nothing.")

        elif len(pdf_files) == 1:
            drop_existing_galleys(article)
            set_pdf_galley(article, pdf_files[0], pubid)

        elif len(pdf_files) == 2:
            logger.debug(f"Found {len(pdf_files)} PDF galleys.")
            drop_existing_galleys(article)

            # I'm working under these assumptions:
            #
            # - the file whose name ends in _en.pdf is probably correct
            #   (i.e. it includes DOI, publication date etc.), but the
            #   language is undecided (probably really English if it comes
            #   from JCOM, but something else if it comes from JCOMAL)
            #
            # - the file whose name ends in _xx.pdf (not _en.pdf) is
            #   probably in the language suggested by the name, but the
            #   content is wrong (missing DOI, etc.). This file should be
            #   dropped and re-created from the corresponding tex source
            #   found, but only _after_ the tex source has been corrected
            #   to include the missing content.

            main_pdf_filename, translation_pdf_filename = find_and_rename_main_galley(pdf_files)
            set_pdf_galley(article, main_pdf_filename, pubid)

            translation_pdf_file, translation_label, translation_language = rebuild_translation_galley(
                translation_pdf_filename,
                main_pdf_filename,
            )
            set_translation_galley(translation_pdf_file, translation_label, article)
        else:
            logger.critical(f"Found {len(pdf_files)} PDF galleys. Doing nothing.")

    def create_article(self, xml_obj):
        """Create the article."""
        pubid = xml_obj.find("//document/articleid").text
        journal = journal_models.Journal.objects.get(code=self.options["journal-code"])
        logger.debug(f"Creating {pubid}")
        article = submission_models.Article.get_article(
            journal=journal,
            identifier_type="pubid",
            identifier=pubid,
        )
        if article:
            # This is not the default situation: if we are here it
            # means that the article has been already imported and
            # that we are re-importing.
            logger.warning(f"Re-importing existing article {pubid} at {article.id}")
        else:
            article = submission_models.Article.objects.create(
                journal=journal,
            )
        article.title = xml_obj.find("//document/title").text
        article.abstract = xml_obj.find("//document/abstract").text
        article.imported = True
        article.date_accepted = rome_timezone.localize(
            datetime.datetime.fromisoformat(xml_obj.find("//document/date_accepted").text),
        )
        article.date_submitted = rome_timezone.localize(
            datetime.datetime.fromisoformat(xml_obj.find("//document/date_submitted").text),
        )
        article.date_published = rome_timezone.localize(
            datetime.datetime.fromisoformat(xml_obj.find("//document/date_published").text),
        )
        article.save()
        identifiers_models.Identifier.objects.get_or_create(
            identifier=xml_obj.find("//document/doi").text,
            article=article,
            id_type="doi",  # should be a member of the set identifiers_models.IDENTIFIER_TYPES
            enabled=True,
        )
        logger.debug(f"Set doi {article.get_doi()} onto {article.pk}")
        identifiers_models.Identifier.objects.get_or_create(
            identifier=pubid,
            article=article,
            id_type="pubid",  # should be a member of the set identifiers_models.IDENTIFIER_TYPES
            enabled=True,
        )
        logger.debug(f"Set pubid {pubid} onto {article.pk}")
        article.page_numbers = from_pubid_to_eid(pubid)
        article.save()
        article.refresh_from_db()
        return (article, pubid)

    def set_supplementary_material(self, article, pubid, workdir):
        """Set supplementary files if necessary."""
        # There is no useful info in wjapp's XML file about
        # supplementary material, so we don't need any reference to
        # the XML file.

        # We might be working on an exiting article, let's cleanup
        for supp_file in article.supplementary_files.all():
            supp_file.file.delete()
            supp_file.file = None
        article.supplementary_files.clear()

        attachments_folder = workdir / "attachments"
        if not os.path.exists(attachments_folder):
            return

        for supplementary_file in os.listdir(attachments_folder):
            file_name = os.path.basename(supplementary_file)
            uploaded_file = File(open(attachments_folder / supplementary_file, "rb"), file_name)
            save_supp_file(
                article,
                request=fake_request,
                uploaded_file=uploaded_file,
                label=file_name,
            )
            logger.debug(f"Supplementary material {file_name} set onto {pubid}")

    def set_doi(self, article):
        """Check that the article has a DOI ala JCOM."""
        # I'm not sure that wjapp is trustworty and Janeway's default
        # is {prefix}/{journal.id}.{article.id}
        expected_doi = generate_doi(article)
        if existing_doi := article.get_identifier("doi"):
            if existing_doi == expected_doi:
                logger.debug(f"DOI {existing_doi} for {article.id} already present. Doing nothing")
            else:
                logger.critical(
                    f"DOI {existing_doi} for {article.id} different from expected {expected_doi}! Doing nothing.",
                )
        else:
            logger.debug(f"Did not receive a DOI from wjapp. Setting {expected_doi} on {article.id}.")
            Identifier.objects.create(
                identifier=expected_doi,
                article=article,
                id_type="doi",
            )


# TODO: consider refactoring with import_from_drupal
def download_and_store_article_file(image_source_url, article):
    """Downaload a media file and link it to the article."""
    if not os.path.exists(image_source_url):
        logger.error(f"Img {image_source_url} does not exist in {os.getcwd()}")
    image_name = image_source_url.split("/")[-1]
    image_file = File(open(image_source_url, "rb"), name=image_name)
    new_file: JanewayFile = save_galley_image(
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


def mangle_images(article):
    """Download all <img>s in the article's galley and adapt the "src" attribute."""
    render_galley = article.get_render_galley
    galley_file: JanewayFile = render_galley.file
    galley_string: str = galley_file.get_file(article)
    html: HtmlElement = lxml.html.fromstring(galley_string)
    images = html.findall(".//img")
    for image in images:
        img_src = image.attrib["src"].split("?")[0]
        img_obj: JanewayFile = download_and_store_article_file(img_src, article)
        # TBV: the `src` attribute is relative to the article's URL
        image.attrib["src"] = img_obj.label

    with open(galley_file.self_article_path(), "wb") as out_file:
        out_file.write(lxml.html.tostring(html, pretty_print=False))


def set_pdf_galley(article, file_path, pubid):
    """Set a pdf galley onto the given article.

    Infer and set the galley's label, including the language code.
    Set the article's language.
    """
    file_name = os.path.basename(file_path)
    file_mimetype = "application/pdf"  # I just know it! (sry :)
    uploaded_file = File(open(file_path, "rb"), file_name)
    label, language = decide_galley_label(pubid, file_name=file_name, file_mimetype=file_mimetype)
    # We can have 2 non-English galleys (PDF and EPUB),
    # they are supposed to be of the same language. Not checking.
    #
    # If the article language is different from
    # english, this means that a non-English gally has
    # already been processed and there is no need to
    # set the language again.
    if language and language != "en":
        if article.language != "eng":
            pass
        else:
            set_language(article, language)
    save_galley(
        article,
        request=fake_request,
        uploaded_file=uploaded_file,
        is_galley=True,
        label=label,
        save_to_disk=True,
        public=True,
    )
    logger.debug(f"PDF galley {label} set onto {pubid}")


def set_translation_galley(pdf_file, label, article):
    """Set the given file as galley for the given article, using the given language and label."""
    file_name = os.path.basename(pdf_file)
    uploaded_file = File(open(pdf_file, "rb"), file_name)
    save_galley(
        article,
        request=fake_request,
        uploaded_file=uploaded_file,
        is_galley=True,
        label=label,
        save_to_disk=True,
        public=True,
    )
    logger.debug(f"PDF galley {label} set onto {article}")


def check_mappings(
    mappings: QuerySet[wjs_models.Correspondence],
    imported_email: str,
    imported_usercod: str,
    imported_source: str,
) -> wjs_models.Correspondence:
    """Run throught the given mappings comparing them to info from the XML.

    If necessary update one mapping or add a new one.
    """
    # Sanity check: all mapping with the same source/usercod or email
    # should point to the same account.
    accounts = [mapping.account for mapping in mappings]
    if len(set(accounts)) != 1:
        logger.critical(
            f"More than 1 mapping from {imported_source}/{imported_usercod}, but they point to different accounts!"
            " You should quit and check your DB!",
        )
        return mappings.first()

    try:
        full_match = mappings.get(
            user_cod=imported_usercod,
            source=imported_source,
            email=imported_email,
        )
    except wjs_models.Correspondence.DoesNotExist:
        pass
    else:
        return full_match

    # If we get here, we are sure that there is no full-match (same
    # source/usercod/email wrt XML) in the Correspondence table.

    # Let's see if we have an "incomplete" match (i.e. same
    # source/usercod, but missing email)
    try:
        match = mappings.get(
            user_cod=imported_usercod,
            source=imported_source,
            email__isnull=True,
        )
    except wjs_models.Correspondence.DoesNotExist:
        pass
    else:
        logger.info(f"Setting {imported_email} onto previously empty mapping {match.id}")
        match.email = imported_email
        match.save()
        return match

    # If we get here, then there is not mapping with the
    # source/usercod/email under consideration, so we should add one
    #
    # NB: we don't check if the imported_email is the same as the
    # account.email. The new mapping is not however redundand, since
    # it is a proof that the imported_email also exists/existed in the
    # source.
    account = mappings.first().account
    new_mapping = wjs_models.Correspondence.objects.create(
        user_cod=imported_usercod,
        source=imported_source,
        email=imported_email,
        account=account,
    )
    logger.warning(
        f"Created new mapping {imported_source}/{imported_usercod}/{imported_email} for account {account}.",
    )
    return new_mapping


def sanity_check_pdf_filenames(pdf_files: list[str]) -> None:
    """Ensure that all the files in the given list are proper PDF file names.

    Just log errors if some item does not match.
    """
    # E.g. JCOM_2204_2023_A06_pt.pdf
    #      JCOM_2204_2023_A06_en.pdf
    #      JCOM_2204_2023_E.pdf

    pubid_and_maybe_language_pattern = re.compile(r"JCOM(?:AL)?_\d{4}_\d{4}_[A-Z]{1,2}[0-9]{0,2}(?:_[a-z]{2})?\.pdf")
    for pdf_file in pdf_files:
        if not re.match(pubid_and_maybe_language_pattern, pdf_file.name):
            logger.error(f'Unexpected filename "{pdf_file}". Please check.')


def manage_newsletter_subscriptions(keyword: submission_models.Keyword, journal: journal_models.Journal):
    """Add a newly created keyword to newsletter subscriptions.

    Newsletter recipients have a list of kwds that they are interested in.
    We always add newly created kwds to this list.

    Recipients are informed of this behavior in their preference page.
    """
    for recipient in Recipient.objects.filter(journal=journal).annotate(tc=Count("topics")).filter(tc__gt=0):
        recipient.topics.add(keyword)


def link_to_existing_newsletter_recipient_maybe(author: Account, journal: journal_models.Journal):
    """Link an account to its corresponding newsletter recipient.

    It is possible that someone
    - already has an anonymous subscription to the newsletter (the ones with only the email and no account associated)
    - and then registers to the wjapp journal
    - and then he is "imported" into Janeway.

    If this happens, here we link the new account to the pre-exisint anonymous recipient.

    But (!) since we don't have _active_ users (for now), we just report the situation and do nothing.

    """
    number_of_matches = Recipient.objects.filter(journal=journal, email=author.email).count()
    # When we are ready for active users, just set `.update(user_id=author.id)` in place of `count()` in the line above
    # and review the log messages below. Maybe also check that we'll update only one Recipient before proceeding.
    if number_of_matches == 1:
        logger.info(f"We could link author {author} to a pre-existing newsletter recipient via email {author.email}")
    elif number_of_matches > 1:
        logger.error(f"Found {number_of_matches} recipients for author {author} on journal {journal}. This is bad!")
