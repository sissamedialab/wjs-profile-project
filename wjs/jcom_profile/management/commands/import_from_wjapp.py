"""Import published articles from wjapp."""

# Examples (remember that you need data on the staging site!):
# - JCOM with translation: JCOM_001CR_0524 / JCOM_2305_2024_R01
#                                            JCOM_2301_2024_R01
# - JCOM with images: JCOM_018A_0523 / JCOM_2305_2024_A04
# - JCOM with images and ESM: JCOM_001N_0923 / JCOM_2305_2024_N02

import datetime
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import lxml.etree
import lxml.html
from core.models import Account
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db.models import Count, Q, QuerySet
from identifiers import models as identifiers_models
from identifiers.models import Identifier
from journal import models as journal_models
from production.logic import save_supp_file
from submission import models as submission_models
from utils.logger import get_logger

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_utils import (
    admin_fake_request,
    drop_existing_galleys,
    map_language,
    publish_article,
    query_wjapp_by_pubid,
    set_author_country,
)
from wjs.jcom_profile.management.commands.import_from_drupal import (
    JOURNALS_DATA,
    NON_PEER_REVIEWED,
    rome_timezone,
)
from wjs.jcom_profile.utils import from_pubid_to_eid, generate_doi

from ...models import Recipient
from ...refugium_peccatorum import AttachGalleys, JcomAssistantClient

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
    "review": "Review",
}


class UnknownSection(Exception):
    """The section (aka article type) found in the XML file is unknown."""


class JCOMAssitantException(Exception):
    """The processing of the galleys by JCOMAssistant failed."""


logger = get_logger(__name__)

fake_request = admin_fake_request()


class Command(BaseCommand):
    """Import an article from wjapp."""

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

        @dataclass
        class UserInfo:
            email: str

        try:
            jcomassistant_client = JcomAssistantClient(
                archive_with_files_to_process=zip_file,
                user=UserInfo(email="gamboz@medialab.sissa.it"),
            )
            response = jcomassistant_client.ask_jcomassistant_to_process()
        except JCOMAssitantException as ja_exception:
            logger.critical(f"Galley generation failed. Aborting process! {ja_exception}")
            logger.warning(f"Please cleanup tmpfolder {tmpdir}")
            return

        # Here we use the AttachGalleys service only to unpack the response in order to find the XML file that will
        # tells us how to create the article.  We then "complete" the initialization of the service (by adding a
        # reference to the newly created article) and manually set the galleys.
        attach_service = AttachGalleys(archive_with_galleys=response.content, article=None, request=fake_request)
        folder_with_unpacked_files = attach_service.unpack_targz_from_jcomassistant()
        if not attach_service.reemit_info_and_up():
            logger.error("Errors during galley generation (see above). Aborting.")
            return

        ja_files = os.listdir(folder_with_unpacked_files)
        xml_files_with_metadata = [f for f in ja_files if f.endswith(".xml")]
        # If we have more that one XML file, we are probably working with a translation.
        # All files (pdf, epub, html and the image folder) are doubled.
        # NB: the XML file of the "translation" is in JATS format.
        num_papers = len(xml_files_with_metadata)
        assert num_papers <= 2
        if num_papers == 1:
            main_wjapp_xml_filename = xml_files_with_metadata[0]
            translation_jats_xml_filename = None
        else:
            # The file that starts with <root> is the main one (in wjapp style)
            # the other should start with <article> and be a JATS
            tree = lxml.etree.parse(folder_with_unpacked_files / xml_files_with_metadata[0])
            root_tagname = tree.getroot().tag
            if root_tagname == "root":
                main_wjapp_xml_filename = xml_files_with_metadata[0]
                translation_jats_xml_filename = xml_files_with_metadata[1]
            else:
                main_wjapp_xml_filename = xml_files_with_metadata[1]
                translation_jats_xml_filename = xml_files_with_metadata[0]

        main_xml_obj = lxml.etree.parse(folder_with_unpacked_files / main_wjapp_xml_filename)

        if self.options["only_regenerate_html_galley"]:
            logger.error("Not yet implemented")
            return
            self.regen_html_galley(main_xml_obj)
            # Cleanup
            shutil.rmtree(tmpdir)
            return

        # extract pubid, create article
        article, pubid = self.create_article(main_xml_obj)
        self.set_keywords(article, main_xml_obj, pubid)
        issue = self.set_issue(article, main_xml_obj, pubid)
        self.set_section(article, main_xml_obj, pubid, issue)
        self.set_authors(article, main_xml_obj)
        self.set_license(article)
        self.set_doi(article)
        self.set_supplementary_material(article, pubid, workdir)

        # Attach galleys (PDF, HTML, EPUB, etc.)
        try:
            attach_service.article = article

            main_basefilename = main_wjapp_xml_filename.replace(".xml", "")
            main_lang = map_language(main_xml_obj.find("//document").get("lang"))
            main_xml_obj.find("//document").get("lang")
            if num_papers > 1:
                label_suffix = f" ({main_lang.alpha_2})"
            else:
                label_suffix = ""
            attach_service.save_pdf(
                filename=f"{main_basefilename}.pdf",
                label=f"PDF{label_suffix}",
                language=main_lang.alpha_3,
            )
            attach_service.save_html(
                filename=f"{main_basefilename}.html",
                label=f"HTML{label_suffix}",
                language=main_lang.alpha_3,
            )
            attach_service.save_epub(
                filename=f"{main_basefilename}.epub",
                label=f"EPUB{label_suffix}",
                language=main_lang.alpha_3,
            )

            if num_papers > 1:
                # We have a "translation"
                logger.debug(
                    "Multilingual paper - "
                    f"main: {main_wjapp_xml_filename}; translation: {translation_jats_xml_filename}",
                )
                translation_basefilename = translation_jats_xml_filename.replace(".xml", "")
                translation_xml_obj = lxml.etree.parse(folder_with_unpacked_files / translation_jats_xml_filename)
                translation_lang = map_language(translation_xml_obj.getroot().get("lang"))
                label_suffix = f" ({translation_lang.alpha_2})"
                attach_service.save_pdf(
                    filename=f"{translation_basefilename}.pdf",
                    label=f"PDF{label_suffix}",
                    language=main_lang.alpha_3,
                )
                attach_service.save_epub(
                    filename=f"{translation_basefilename}.epub",
                    label=f"EPUB{label_suffix}",
                    language=main_lang.alpha_3,
                )

        except Exception as exception:
            logger.error(f"Setup of galleys failed: {exception}")

        publish_article(article)

        # Cleanup
        shutil.rmtree(tmpdir)

    def regen_html_galley(self, xml_obj):
        """Regenerate only the render-galley."""
        logger.critical("Not implemented!")
        return

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
                author.username = imported_email
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
            drop_existing_galleys(article)
        else:
            article = submission_models.Article.objects.create(
                journal=journal,
            )

        article.language = map_language(xml_obj.find("//document").get("lang")).alpha_3
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
