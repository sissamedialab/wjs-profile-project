"""Import article from wjapp."""

import datetime
from dataclasses import dataclass, field
from io import BytesIO

import freezegun
import mariadb
import requests
from core import files
from core.middleware import GlobalRequestMiddleware
from core.models import Account
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files import File as DjangoFile
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from identifiers import models as identifiers_models
from journal.models import Journal
from plugins.wjs_review.communication_utils import get_eo_user
from plugins.wjs_review.logic import (
    AssignToEditor,
    AssignToReviewer,
    AuthorHandleRevision,
    DeselectReviewer,
    EditorRevisionRequest,
    EvaluateReview,
    HandleDecision,
    SubmitReview,
    WorkflowReviewAssignment,
    render_template_from_setting,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    EditorDecision,
    Message,
    MessageRecipients,
    WjsEditorAssignment,
)
from plugins.wjs_review.utils import get_report_form
from review.models import (
    EditorAssignment,
    ReviewAssignment,
    ReviewRound,
    RevisionRequest,
)
from submission import models as submission_models
from utils.logger import get_logger
from utils.management.commands.test_fire_event import create_fake_request
from utils.setting_handler import get_setting

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.management.commands.import_from_drupal import (
    JOURNALS_DATA,
    NON_PEER_REVIEWED,
    rome_timezone,
)
from wjs.jcom_profile.management.commands.import_from_wjapp import (
    SECTIONS_MAPPING,
    check_mappings,
)
from wjs.jcom_profile.utils import create_rich_fake_request


class UnknownSection(Exception):
    """Unknown section / article-type."""


logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Connect to wjApp jcom database and read article data."  # noqa A003

    def handle(self, *args, **options):
        """Command entry point."""

        if not getattr(settings, "NO_NOTIFICATION", None):
            self.stderr.write(
                """Notifications are enabled, not importing to avoid spamming. Please set `NO_NOTIFICATION = True`
                in your django settings to proceed.""",
            )
            return
        self.options = options
        for journal_code in ("JCOM",):
            self.journal = Journal.objects.get(code=journal_code)
            self.journal_data = JOURNALS_DATA[journal_code]
            self.import_data_article(**options)

    def add_arguments(self, parser):
        """Add arguments to command."""

        parser.add_argument(
            "--preprintid",
            default="",
            help="jcom wjApp preprintid ex: JCOM_010A_0324",
            required=True,
        )
        parser.add_argument(
            "--importfiles",
            default=False,
            action="store_true",
            help="also dowloads files from wjapp jcom",
            required=False,
        )

    def import_data_article(self, **options):
        """Process one article."""

        session = None
        if self.options["importfiles"]:
            login_setting = f"WJAPP_{self.journal.code.upper()}_IMPORT_LOGIN_PARAMS"
            login_parameters = getattr(settings, login_setting, None)
            if login_parameters is None:
                logger.error(
                    f'Missing login data for {self.journal.code}. Please ensure "{login_setting}" exists in settings.'
                    f"Cannot import files, quitting.",
                )
                return
            elif login_parameters.get("username", "") == "":
                logger.error(
                    f'Empty username parameter for "{login_setting}". Please ensure `username`, etc. are correct.'
                    f"Cannot login, quitting.",
                )
                return

            username = login_parameters.get("username", "")
            passwd = login_parameters.get("password", "")
            session = self.wjapp_login(username, passwd)

        preprintid = self.options["preprintid"]

        # In wjapp, both messages related to the workflow (e.g. the message that the editor sends to
        # the reviewer during selection, the author's cover letter, ecc.) and out-of-workflow
        # messages (e.g. mails from the author to the editor, from the editor to the EO, etc.) are stored
        # in Document_Layer.
        #
        # When we rifle through the actions, we collect some of these records (from Document_Layer).
        # When we are done with all the actions, all the remaining records will then be imported as
        # messages/correspondence.
        self.imported_document_layer_cod_list = []

        if not preprintid:
            return
        setting = f"WJAPP_{self.journal.code.upper()}_IMPORT_CONNECTION_PARAMS"
        connection_parameters = getattr(settings, setting, None)

        if connection_parameters is None:
            logger.error(
                f'Missing connection parameters for {self.journal.code}. Please ensure "{setting}" exists in settings.'
                f"Cannot connect, quitting.",
            )
            return
        elif connection_parameters.get("user", "") == "":
            logger.error(
                f'Empty connection parameters for "{setting}". Please ensure `user`, `host`, etc. are correct.'
                f"Cannot connect, quitting.",
            )
            return

        self.connection = mariadb.connect(**connection_parameters)

        row = self.read_article_data(preprintid)

        if not row:
            self.connection.close()
            logger.debug(f"Article not found {self.journal.code} {preprintid}.")
            return

        document_cod = row["documentCod"]
        preprintid = row["preprintId"]
        section = row["documentType"]
        version_cod = row["versionCod"]
        # current_version -> row  "versionNumber"

        logger.debug(f"""Importing {preprintid}""")

        # create article and section
        article, preprintid, main_author = self.create_article(row)

        self.set_section(article, section)

        # article keywords
        keywords = self.read_article_keywords(version_cod)
        self.set_keywords(article, keywords)

        # read all version versionNum, versionCod
        versions = self.read_versions_data(document_cod)

        # In wjapp, the concept of version is paramount. All actions revolve around versions.
        # Here we cycle through each version and manage the data that need.
        for v in versions:
            imported_version_cod = v["versionCod"]
            imported_version_num = v["versionNumber"]
            imported_version_bios_text = v["authorsBio"]

            # read actions history from wjapp preprint
            history = self.read_history_data(imported_version_cod)

            # Note: editor selection is done with actions of the history

            for action in history:
                # TODO: move the update of imported_document_layer_cod_list out of the action manager
                # using as return value of each action manager run() the partial list
                logger.debug(f"Looking at action {action['actionID']} ({action['actHistCod']})")
                if action_manager := globals().get(action["actionID"]):
                    # "actionID" is something like SYS_ASS_ED, that is also
                    # the name of a class defined in this module
                    action_manager(
                        action=action,
                        connection=self.connection,
                        session=session,
                        journal=self.journal,
                        preprintid=preprintid,
                        article=article,
                        imported_version_num=imported_version_num,
                        imported_version_cod=imported_version_cod,
                        importfiles=self.options["importfiles"],
                        imported_document_layer_cod_list=self.imported_document_layer_cod_list,
                    ).run()
                else:
                    logger.warning(f"Action {action['actionID']} not yet managed.")

            # set_authors bios
            self.set_authors_bios(imported_version_bios_text, article, imported_version_num)

            ImportCorrespondenceManager(
                connection=self.connection,
                session=session,
                journal=self.journal,
                preprintid=preprintid,
                article=article,
                imported_version_num=imported_version_num,
                imported_version_cod=imported_version_cod,
                importfiles=self.options["importfiles"],
                imported_document_layer_cod_list=self.imported_document_layer_cod_list,
            ).run()

        mark_all_messages_read(article)

        self.connection.close()

    #
    # http login to wjapp
    #

    def wjapp_login(self, username, passwd):
        """Login to wjapp to download files."""

        # TODO: add login successful check (verify reponse.content)
        payload = {
            "userid": f"{username}",
            "password": f"{passwd}",
            "orcidid": "",
            "loginOkRedUrl": "https://jcom.sissa.it/jcom/index.jsp",
            "loginFailRedUrl": "https://jcom.sissa.it/jcom/index.jsp",
            "submit": "Sign in",
        }

        with requests.Session() as session:
            # login
            p = session.post("https://jcom.sissa.it/jcom/authentication/authenticate", data=payload)
            assert p.status_code == 200, f"Got {p.status_code}!"

        return session

    #
    # functions to read data from wjapp
    #

    def read_article_data(self, preprintid):
        """Read article main data."""

        cursor_article = self.connection.cursor(dictionary=True)
        query = """
SELECT
d.documentCod,
d.preprintId,
d.documentType,
d.submissionDate,
d.authorCod,
d.eoInChargeCod,
u2.lastname AS eoInCharge_lastname,
u2.firstname AS eoInCharge_firstname,
u2.email AS eoInCharge_email,
u1.lastname AS author_lastname,
u1.firstname AS author_firstname,
u1.email AS author_email,
v.versionCod,
v.versionNumber,
v.versionTitle,
v.versionAbstract
FROM Document d
LEFT JOIN User u1 ON (d.authorCod=u1.userCod)
LEFT JOIN Version v ON (v.documentCod=d.documentCod)
LEFT JOIN User u2 ON (d.eoInChargeCod=u2.userCod)
WHERE
    v.isCurrentVersion=1
AND d.preprintId = %(preprintid)s
"""
        cursor_article.execute(
            query,
            {
                "preprintid": preprintid,
            },
        )
        row = cursor_article.fetchone()
        cursor_article.close()
        return row

    def read_article_keywords(self, version_cod):
        """Read article keywords."""
        cursor_keywords = self.connection.cursor(dictionary=True)
        query_keywords = """
SELECT
keywordName
FROM Version_Keyword
LEFT JOIN Keyword USING (keywordCod)
WHERE
    versioncod=%(version_cod)s
"""
        cursor_keywords.execute(query_keywords, {"version_cod": version_cod})
        keywords = []
        for rk in cursor_keywords:
            keywords.append(rk["keywordName"])
        cursor_keywords.close()
        return keywords

    def read_versions_data(self, document_cod):
        """Read article versions data."""
        cursor_versions = self.connection.cursor(dictionary=True)
        query_versions = """
SELECT
versionCod,
versionNumber,
authorsBio
FROM Version
WHERE documentCod=%(document_cod)s
ORDER BY versionNumber
"""
        cursor_versions.execute(query_versions, {"document_cod": document_cod})
        versions = cursor_versions.fetchall()
        cursor_versions.close()
        return versions

    def read_history_data(self, imported_version_cod):
        """Read history data."""
        cursor_history = self.connection.cursor(dictionary=True)
        query_history = """
SELECT
ah.actHistCod,
ah.versionCod,
ah.actionCod,
ah.agentCod,
u1.lastname AS agentLastname,
u1.firstname AS agentFirstname,
u1.email AS agentEmail,
ah.userCod AS targetCod,
u2.lastname AS targetLastname,
u2.firstname AS targetFirstname,
u2.email AS targetEmail,
u2.editorWorkload AS targetEditorWorkload,
ah.realAgentCod,
ah.actionDate,
a.actionID
FROM Action_History ah
LEFT JOIN Action a USING (actionCod)
LEFT JOIN User u1 ON (u1.userCod=ah.agentCod)
LEFT JOIN User u2 ON (u2.userCod=ah.userCod)
WHERE versionCod=%(imported_version_cod)s
ORDER BY ah.actionDate
"""
        query_history = cursor_history.execute(
            query_history,
            {"imported_version_cod": imported_version_cod},
        )
        history = cursor_history.fetchall()
        cursor_history.close()
        return history

    #
    # functions to set data in wjs
    #

    def create_article(self, row):
        """Create the article."""

        preprintid = row["preprintId"]
        article = submission_models.Article.get_article(
            journal=self.journal,
            identifier_type="preprintid",
            identifier=preprintid,
        )
        if article:
            # This is not the default situation: if we are here it
            # means that the article has been already imported and
            # that we are re-importing.
            logger.warning(
                f"Re-importing existing article {preprintid} at {article.id} "
                f"The {article.id} here will disappear because of the delete() below",
            )

            # clean some data related to the article
            EditorRevisionRequest.objects.filter(article=article).delete()
            WorkflowReviewAssignment.objects.filter(article=article).delete()
            for rr in ReviewRound.objects.filter(article=article):
                EditorDecision.objects.filter(review_round=rr).delete()

            # necessary to delete
            Message.objects.filter(object_id=article.id).delete()

            article.manuscript_files.all().delete()
            article.data_figure_files.all().delete()
            article.supplementary_files.all().delete()
            article.source_files.all().delete()
            article.galley_set.all().delete()

            # after delete the id is lost
            article_id_check = article.id

            article.delete()

            # check deleted when article is deleted
            assert RevisionRequest.objects.filter(article__id=article_id_check).count() == 0
            assert ReviewAssignment.objects.filter(article__id=article_id_check).count() == 0
            assert ReviewRound.objects.filter(article__id=article_id_check).count() == 0

        article = submission_models.Article.objects.create(
            journal=self.journal,
        )
        article.title = row["versionTitle"]
        article.abstract = row["versionAbstract"]
        article.imported = True
        article.date_submitted = rome_timezone.localize(row["submissionDate"])
        article.save()
        main_author = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            row["authorCod"],
            row["author_lastname"],
            row["author_firstname"],
            row["author_email"],
        )

        eo_in_charge = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            row["eoInChargeCod"],
            row["eoInCharge_lastname"],
            row["eoInCharge_firstname"],
            row["eoInCharge_email"],
        )

        if not main_author.check_role(self.journal, "author"):
            main_author.add_account_role("author", self.journal)
        article.owner = main_author
        article.authors.add(main_author)
        article.correspondence_author = main_author
        article.save()

        article.articleworkflow.eo_in_charge = eo_in_charge
        article.articleworkflow.save()

        identifiers_models.Identifier.objects.get_or_create(
            identifier=preprintid,
            article=article,
            id_type="preprintid",  # NOT a member of the set identifiers_models.IDENTIFIER_TYPES
            enabled=True,
        )
        logger.debug(f"Set preprintid {preprintid} onto {article.pk}")
        article.refresh_from_db()
        return (article, preprintid, main_author)

    def set_authors_bios(self, bios_text: str, article: submission_models.Article, imported_version_num: int):
        "Sets authors bios if found."

        # bios from wjapp is a unique text field with paragraphs starting with
        # the full name of the author and separated by two newlines ex: JCOM_004A_0124 JCOM_003N_0324
        #
        # in some cases there is only the bio of the main author
        # without the full name at the beginning ex: JCOM_018A_0624
        #
        # examples of other cases with format no standard:
        # JCOM_005A_0224 JCOM_003A_0424 JCOM_004Y_0424 JCOM_021A_0424 JCOM_001N_0524
        # names match problem: JCOM_028A_0724
        #
        # There are versions ex. JCOM_002N_0324/5 (current version published) without bio
        #
        # TODO: if there are maintenance on wjapp with changes in coauthors
        # without actions in the history, better to check also the db with
        # a direct query? The actions must be managed the same for the timeline.
        #
        # NOTE: decison from jcom-eo: import only bios with name at the beginning. In any case
        #       the authors will check/correct/enter their bio after migration
        # TODO: name match can be improved
        if not bios_text:
            logger.warning(
                f"No author bios found in wjapp for version: {imported_version_num} {article} ",
            )
            return

        authors_bios = bios_text.split("\r\n\r\n")

        if len(authors_bios) != article.authors.count():
            logger.warning(
                f"Authors bios paragraphs: {len(authors_bios)}, article authors: {article.authors.count()}",
            )

        for author in article.authors.all():
            bios_found = self.get_author_bio_by_name(authors_bios, author)
            if len(bios_found) != 1:
                logger.warning(
                    f"Found {len(bios_found)} bios for author {author.full_name()} version: {imported_version_num}.",
                )

            # saved first bio found or let unchanged
            if bios_found:
                # TODO: add always to article frozen author frozen biography

                logger.debug(
                    f"Updated bio for author {author.full_name()} version: {imported_version_num}.",
                )
                # save only if not present or the article is the last submitted
                # for the author
                if not author.biography or self.last_submitted_for_author(author, article):
                    author.biography = bios_found[0]
                    author.save()

    def last_submitted_for_author(self, author, article):
        "Last submitted article for the author"

        return not submission_models.Article.objects.filter(
            authors__in=[author],
            date_submitted__gt=article.date_submitted,
        ).exists()

    def get_author_bio_by_name(self, authors_bios, author):
        "Get author bio from authors bios list by name."

        bios_found = []
        for bio in authors_bios:
            if bio.startswith(author.full_name()):
                bios_found.append(bio)
        return bios_found

    def set_section(self, article, section_name):
        """Set the section."""

        if section_name not in SECTIONS_MAPPING:
            logger.critical(f'Unknown article type "{section_name}" for {article.get_identifier("preprintid")}')
            raise UnknownSection(f'Unknown article type "{section_name}" for {article.get_identifier("preprintid")}')
        section_name = SECTIONS_MAPPING.get(section_name)
        section_order_tuple = self.journal_data["section_order"]
        section, created = submission_models.Section.objects.get_or_create(
            journal=self.journal,
            name=section_name,
            defaults={
                "sequence": section_order_tuple[section_name][0],
                "plural": section_order_tuple[section_name][1],
            },
        )
        if created:
            logger.warning(
                f'Created section "{section_name}" for {article.get_identifier("preprintid")}. Please check!',
            )

        article.section = section
        if article.section.name in NON_PEER_REVIEWED:
            article.peer_reviewed = False

        article.save()

    def set_keywords(self, article: submission_models.Article, keywords):
        """Set the keywords."""

        # Drop all article's kwds (and KeywordArticles, used for kwd ordering)
        article.keywords.clear()
        order = 0
        for kwd in keywords:
            order = order + 1
            # Janeway's keywords are a simple model with a "word" field for the kwd text
            kwd_word = kwd.strip()
            # in wjapp-JCOMAL, the keyword string contains all three
            # languages separated by ";". The first is English.
            if self.journal.code.upper() == "JCOMAL":
                kwd_word = kwd_word.split(";")[0].strip()
            keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_word)
            if created:
                logger.warning(
                    f'Created keyword "{kwd_word}" for {article.get_identifier("preprintid")}. Please check!',
                )

            # Always link kwd to journal (remember that journals have a set of kwds!)
            #
            # Even if the kwd was not created, it is possible that we got a pre-existing kwd that was linked only to
            # another journal.
            #
            # P.S. `add` won't duplicate an existing relation
            # https://docs.djangoproject.com/en/3.2/ref/models/relations/
            self.journal.keywords.add(keyword)

            submission_models.KeywordArticle.objects.get_or_create(
                article=article,
                keyword=keyword,
                order=order,
            )
            logger.debug(f"Keyword {kwd_word} set at order {order}")
            article.keywords.add(keyword)
        article.save()


#
# general function
#


def account_get_or_create_check_correspondence(source, user_cod, last_name, first_name, imported_email):
    """Get a user account - check Correspondence and eventually create new account."""

    # ex: source: jcom, jcomal, prophy, ...
    # Check if we know this person form some other journal or by email

    if imported_email == "jcom_hidden_user@jcom.sissa.it":
        # using the wjapp userCod in the email the same hidden user is identified
        # in Correspondence in unique way after the hiding action on wjapp.
        # Searching in Correspondence with source and user_cod only can find
        # more than one match (email changes)
        imported_email = f"{user_cod}_jcom_hidden_user@invalid.com"
        logger.debug(f"found wjapp hidden user {user_cod=} {imported_email}")

    account_created = False
    mappings = wjs_models.Correspondence.objects.filter(
        Q(user_cod=user_cod, source=source) | Q(email=imported_email),
    )
    if mappings.count() == 0:
        # We never saw this person in other journals.
        account, account_created = Account.objects.get_or_create(
            email=imported_email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
            },
        )
        mapping = wjs_models.Correspondence.objects.create(
            user_cod=user_cod,
            source=source,
            email=imported_email,
            account=account,
        )
    elif mappings.count() >= 1:
        # We know this person from another journal
        logger.debug(
            f"wjs mapping exists ({mappings.count()} correspondences)" f" for {user_cod}/{source} or {imported_email}",
        )
        mapping = check_mappings(mappings, imported_email, user_cod, source)

    account = mapping.account
    # `used` indicates that this usercod from this source
    # has been used to create the core.Account record
    if account_created:
        mapping.used = True
        mapping.save()

    # to activate hijack function
    if not account.is_active:
        account.is_active = True
        account.save()

    return account


def mark_all_messages_read(article: submission_models.Article) -> None:
    """Mark all messages for the given article as read from each recipient."""

    MessageRecipients.objects.filter(
        message__content_type=ContentType.objects.get_for_model(article),
        message__object_id=article.id,
    ).update(read=True)
    Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
    ).update(read_by_eo=True)


def newlines_text_to_html(message: str) -> str:
    """Format Document_Layer message read from wjapp."""
    # TBV: format other new-line styles?
    #      Document_Layer messages/report from jcom jcomal are text message
    if not message:
        return ""
    message = message.strip()
    message = message.replace("\r\n", "<br>")
    message = message.replace("\n", "<br>")
    message = message.replace("\r", "<br>")
    return message


@dataclass
class ImportCorrespondenceManager:
    """Data class that manages the import of all the correspondence of the wjapp imported version."""

    connection: mariadb.Connection
    session: requests.sessions.Session
    journal: Journal
    preprintid: str
    article: submission_models.Article
    imported_version_num: int
    imported_version_cod: int
    importfiles: bool
    imported_document_layer_cod_list: list

    def run(self):
        """Import wjapp correspondence for the imported_version."""

        # The management of all the types implies where it is necessary
        # to set author or recipient coeherently with the
        # type before the message creation because the user data are not
        # read from wjapp database

        # to be used in case of runtime problem on a specific type
        self.types_skipped = []

        # types message "to admin", in wjs added eo_user as recipient
        # TODO: manage files attached to AUANN
        self.types_to_admin_list = [
            "AUANN",  # production info sent by author to eo_user can have files attached
            "TOADE",
            "TOPME",  # to pm (in wjs to eo_user)
            "TPSAN",  # typ annotation (in wjs to eo_user)
        ]

        # types message "from admin", in wjs added eo_user as author
        # FRPME: ex. JCOM_002A_1216, JCOM_016A_0418
        # TBV: TOPUM needs jcom publication manager user jcom-pum@jcom.sissa.it but set eo_user
        self.types_from_admin_list = [
            "FRPME",  # from pm (in wjs from eo_user) to someone (author, typesetter, ...)
            "FRADE",
            "AREMA",
            "AREME",
            "AREMR",
            "PREMA",  # pm (in wjs from eo_user) auth reminder
            "PREMT",  # pm (in wjs from eo_user) typesetter reminder
            "PMANN",  # pm (in wjs from eo_user) annotation
            "TOPUM",  # from eo_user to publisher jcom publication manager user jcom-pum@jcom.sissa.it
        ]

        # types which have always author and recipient taken from wjapp database
        self.types_with_auth_and_recipient = [
            "ATOEE",
            "EDANN",
            "EMAIL",
            "EREMA",
            "EREMR",
            "ETOAE",
            "FRASDIE",
            "FRDIE",
            "FRPUM",
            "PUMANN",
            "TOASDIE",
            "TODIE",
        ]

        self.managed_types_list = (
            self.types_with_auth_and_recipient
            + self.types_from_admin_list
            + self.types_to_admin_list
            + self.types_skipped
        )

        for m in self.read_all_messages_of_imported_version():

            # as wjs msg.recipient added from wjapp: recipient, reader, readerCC
            # readerBCC are excluded by the query on wjapp
            (author_from_wjapp, message_recipients_no_bcc) = self.read_message_author_recipients(m["documentLayerCod"])

            # managed types
            if m["documentLayerType"] not in self.managed_types_list:
                raise RuntimeError(
                    f"msg {m['documentLayerCod']} type not managed {self.article.id} {m['documentLayerSubject']}"
                    f" {m['documentLayerType']}"
                )

            if m["documentLayerType"] in self.types_skipped:
                logger.error(
                    f"msg {m['documentLayerCod']} type skipped {self.article.id} {m['documentLayerSubject']}"
                    f" {m['documentLayerType']}"
                )
                continue

            logger.debug(
                f"msg {m['documentLayerCod']} to be imported {self.article.id} {m['documentLayerSubject']}"
                f" {m['documentLayerType']}"
            )

            if m["documentLayerType"] in self.types_from_admin_list:
                # author set directly, not read from wjapp
                author = get_eo_user(self.journal)
                logger.debug(f"msg {m['documentLayerCod']} Add author eo user, type {m['documentLayerType']}")
            else:
                if not author_from_wjapp:
                    raise RuntimeError(f"msg {m['documentLayerCod']} Missing author. type {m['documentLayerType']}")

                # author data comes from wjapp
                author = account_get_or_create_check_correspondence(
                    self.journal.code.lower(),
                    author_from_wjapp[0]["userCod"],
                    author_from_wjapp[0]["lastname"],
                    author_from_wjapp[0]["firstname"],
                    author_from_wjapp[0]["email"],
                )
                logger.debug(
                    f"msg {m['documentLayerCod']} add wjapp author {author.last_name} {m['documentLayerSubject']}"
                )

            with freezegun.freeze_time(
                rome_timezone.localize(m["submissionDate"]),
            ):
                msg = Message.objects.create(
                    actor=author,
                    subject=m["documentLayerSubject"],
                    body=newlines_text_to_html(m["documentLayerText"]),
                    content_type=ContentType.objects.get_for_model(self.article),
                    object_id=self.article.id,
                )
                logger.debug(f"msg {m['documentLayerCod']} imported: {m['documentLayerSubject']}")

                # recipient management

                # if not message_recipients_no_bcc or "recipient" not in message_recipients_no_bcc.values():
                if m["documentLayerType"] in self.types_to_admin_list:
                    msg.recipients.add(get_eo_user(self.journal))
                    logger.debug(f"msg {m['documentLayerCod']} add recipient eo_user {m['documentLayerSubject']}")

                for msg_rec in message_recipients_no_bcc:
                    recipient = account_get_or_create_check_correspondence(
                        self.journal.code.lower(),
                        msg_rec["userCod"],
                        msg_rec["lastname"],
                        msg_rec["firstname"],
                        msg_rec["email"],
                    )
                    if recipient not in msg.recipients.all():
                        msg.recipients.add(recipient)
                    logger.debug(
                        f"msg {m['documentLayerCod']} add wjapp recipient {recipient} {m['documentLayerSubject']}"
                    )

                # management of special cases
                # TBV: correct management of this type?
                if not msg.recipients.exists() and m["documentLayerType"] in ("FRDIE", "TOPUM"):
                    msg.recipients.add(get_eo_user(self.journal))

                # error if no recipients at all from wjapp and not added eo_user
                if not msg.recipients.all():
                    raise RuntimeError(
                        f"msg {m['documentLayerCod']} without recipients: {self.article.id}"
                        f" {m['documentLayerSubject']} { m['documentLayerType']} {message_recipients_no_bcc=}"
                    )

        logger.debug(f"imported correspondence for {self.preprintid}/{self.imported_version_num}")

    def read_all_messages_of_imported_version(self):
        """Read all the messages of the imported version."""

        cursor_all_messages = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # read all messages for imported version
        # Please note that 'REREP', 'EDREP', 'CVLETT' are reviewer and editor reports and
        # author's cover letter, which are managed elsewhere.
        query_all_messages = """
SELECT
dl.documentLayerCod,
dl.documentLayerSubject,
dl.documentLayerText,
dl.documentLayerOnlyTex,
dl.documentLayerType,
dl.submissionDate
FROM Document_Layer dl
WHERE
    versioncod=%(imported_version_cod)s
AND dl.documentLayerType NOT IN ('REREP', 'EDREP', 'CVLETT')
ORDER BY dl.submissionDate
"""
        cursor_all_messages.execute(
            query_all_messages,
            {
                "imported_version_cod": self.imported_version_cod,
            },
        )

        all_messages = cursor_all_messages.fetchall()
        cursor_all_messages.close()
        all_messages_not_yet_imported = []
        for m in all_messages:
            if m["documentLayerCod"] not in self.imported_document_layer_cod_list:
                all_messages_not_yet_imported.append(m)
                logger.debug(
                    f"msg found: {m['documentLayerCod']} {m['documentLayerType']} {m['documentLayerSubject']}"
                )
            else:
                logger.warning(
                    f"msg cleaned: {m['documentLayerCod']} {m['documentLayerType']} {m['documentLayerSubject']}"
                )

        if not all_messages_not_yet_imported:
            logger.warning(f"Found 0 messages for {self.preprintid}/{self.imported_version_num}")

        return all_messages_not_yet_imported

    def read_message_author_recipients(self, document_layer_cod):
        """Read the author and all the recipients of a message but BCC."""
        # WJS does not allow for Bcc messages, so we decided to silently ignore them.
        # The alternative (import the Bcc recipients as Cc) is not viable,
        # because we should not disclose an info that the message's author considered private.
        cursor_all_message_author_recipients = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        query_all_message_author_recipients = """
SELECT
ur.documentLayerCod,
ur.userCod,
ur.userType,
u.lastname,
u.firstname,
u.email
FROM User_Rights ur
LEFT JOIN User u USING (userCod)
WHERE
    ur.documentLayerCod = (%(document_layer_cod)s)
AND ur.userType!='readerBCC'
"""
        cursor_all_message_author_recipients.execute(
            query_all_message_author_recipients,
            {
                "document_layer_cod": document_layer_cod,
            },
        )
        if cursor_all_message_author_recipients.rowcount == 0:
            logger.error(
                f"Found {cursor_all_message_author_recipients.rowcount} users for message {document_layer_cod}"
                f" {self.preprintid}/{self.imported_version_num}"
            )
            all_message_author_recipients = []
        else:
            all_message_author_recipients = cursor_all_message_author_recipients.fetchall()

        author = [a for a in all_message_author_recipients if a["userType"] == "author"]
        assert len(author) in {0, 1}
        recipients = [r for r in all_message_author_recipients if r["userType"] != "author"]

        cursor_all_message_author_recipients.close()
        return (author, recipients)


@dataclass
class BaseActionManager:
    """Data class that manages one action."""

    # one of the records returned by of the read_history_data() / action-history
    # item (agent, target, version_code, etc.)
    action: dict
    connection: mariadb.Connection
    session: requests.sessions.Session
    journal: Journal
    preprintid: str
    article: submission_models.Article
    imported_version_num: int
    imported_version_cod: int
    importfiles: bool
    imported_document_layer_cod_list: list

    def run(self):
        raise NotImplementedError

    def get_current_editor(self):
        return WjsEditorAssignment.objects.get_current(self.article).editor

    def check_editor_set(self):
        if not self.get_current_editor():
            logger.error(f"editor not set for {self.preprintid} {self.article.id}")
            self.connection.close()
            raise Exception

    def download_manuscript_version(self):
        """Download pdf manuscript for imported version."""

        # TODO: move url to plugin settings (depends on journal)?
        # authorised request.
        # ex: https://jcom.sissa.it/jcom/common/archiveFile?
        #    filePath=JCOM_003N_0623/5/JCOM_003N_0623.pdf&fileType=pdf
        url_base = "https://jcom.sissa.it/jcom/common/archiveFile?filePath="
        file_url = f"{url_base}{self.preprintid}/{self.imported_version_num}/{self.preprintid}.pdf&fileType=pdf"
        logger.debug(f"{file_url=}")

        response = self.session.get(file_url)

        assert response.status_code == 200, f"Got {response.status_code}!"

        if response.headers["Content-Length"] == "0":
            logger.error(f"check wjapp login credentials empty file downloaded: {response.headers['Content-Length']}")

        return response

    def save_manuscript(self, response):
        """Save manuscript from response"""

        manuscript_dj = DjangoFile(BytesIO(response.content), f"{self.preprintid}.pdf")

        manuscript_file = files.save_file_to_article(
            manuscript_dj,
            self.article,
            self.article.correspondence_author,
        )
        self.article.manuscript_files.add(manuscript_file)
        manuscript_file.label = "PDF"
        manuscript_file.description = ""
        manuscript_file.save()
        self.article.save()
        return


@dataclass
class EditorAssignmentAction(BaseActionManager):
    """Manages editor assignment action."""

    action_triggers_import_files: bool = field(init=False, default=False)

    def run(self):
        """Editor assignment management."""

        # these map (roughly) to EditorAssignment
        editor_cod = self.action["targetCod"]
        editor_lastname = self.action["targetLastname"]
        editor_firstname = self.action["targetFirstname"]
        editor_email = self.action["targetEmail"]
        editor_assign_date = self.action["actionDate"]
        editor_maxworkload = self.action["targetEditorWorkload"]

        # there are wjapp actions SYS_ASS_ED with editor assigned None
        # example: JCOM_003A_0424 version 2
        if editor_cod:
            # attribute editor added
            self.set_editor(
                editor_cod,
                editor_lastname,
                editor_firstname,
                editor_email,
                editor_assign_date,
            )

            # added attribute editor parameters
            editor_parameters = self.read_editor_parameters(editor_cod)
            self.set_editor_parameters(editor_parameters, editor_maxworkload)

            if self.action_triggers_import_files and self.importfiles:
                # TODO: import files must be done not only for this case but for each new wjapp version
                # TODO: import files must be extended to wjapp source zip/targz file and attachments
                response = self.download_manuscript_version()
                self.save_manuscript(response)

    # TODO: check why new review_round is not created
    def set_editor(self, editor_cod, editor_lastname, editor_firstname, editor_email, editor_assign_date):
        """Assign the editor.

        Also create the editor's Account if necessary.
        """
        editor = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            editor_cod,
            editor_lastname,
            editor_firstname,
            editor_email,
        )

        # An account must have the "section-editor" role on the journal to be able to be assigned as editor of an
        # article.
        if not editor.check_role(self.journal, "section-editor"):
            editor.add_account_role("section-editor", self.journal)

        logger.debug(f"Assigning {editor.last_name} {editor.first_name} onto {self.article.pk}")

        # TODO: we need a function in the logic to reassign a new editor to the article.
        #       As temporary replacement we delete the editor assignments for the article
        EditorAssignment.objects.filter(article=self.article).delete()

        # Manually move into a state where editor assignment can take place
        # TODO: check if this is not the case already...
        self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        self.article.articleworkflow.save()

        request = create_fake_request(user=None, journal=self.journal)
        GlobalRequestMiddleware.process_request(request)

        with freezegun.freeze_time(
            rome_timezone.localize(editor_assign_date),
        ):
            AssignToEditor(
                article=self.article,
                editor=editor,
                request=request,
            ).run()
            self.article.save()
        self.article.refresh_from_db()
        return editor

    def read_editor_parameters(self, editor_cod):
        """Read editor parameters."""

        # Note mar 2 lug 2024:
        # in jcom only 1 editor has keywords
        cursor_editor_parameters = self.connection.cursor(dictionary=True)
        query_editor_parameters = """
SELECT
ek.editorCod,
ek.keywordCod,
ek.keywordWeight,
kw.keywordName
FROM Editor_Keyword ek
LEFT JOIN Keyword kw USING (keywordCod)
WHERE editorCod=%(editor_cod)s
"""
        editor_parameters = cursor_editor_parameters.execute(query_editor_parameters, {"editor_cod": editor_cod})
        editor_parameters = cursor_editor_parameters.fetchall()
        cursor_editor_parameters.close()
        return editor_parameters

    def set_editor_parameters(self, editor_parameters, editor_maxworkload):
        """Set the editor parameters.

        - max-workload (EditorAssignmentParameters workload)
        - keyword      (EditorKeyword into EditorAssignmentParameters keywords)
        - kwd weight   (EditorKeyword weight)
        """

        if editor_parameters:
            assignment_parameters, eap_created = wjs_models.EditorAssignmentParameters.objects.get_or_create(
                editor=self.get_current_editor(),
                journal=self.journal,
            )
        else:
            return

        if not editor_maxworkload:
            logger.error(f"Missing editor max workload: {editor_maxworkload}")

        if editor_maxworkload == 9999:
            logger.warning(f"Workload of {editor_maxworkload} found. Verify WJS implementation of assignment funcs!")

        assignment_parameters.workload = editor_maxworkload
        assignment_parameters.save()

        # delete all existing editor kwds
        wjs_models.EditorKeyword.objects.filter(editor_parameters=assignment_parameters).delete()

        # create all new editor kwds
        for ep in editor_parameters:
            kwd_word = ep["keywordName"]
            kwd_weight = ep["keywordWeight"]
            logger.debug(f"Editor parameter: {kwd_word} {kwd_weight}")
            keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_word)
            if created:
                logger.warning(
                    f'Created keyword "{kwd_word}" for editor {self.get_current_editor()}. Please check!',
                )
            wjs_models.EditorKeyword.objects.create(
                editor_parameters=assignment_parameters,
                keyword=keyword,
                weight=kwd_weight,
            )

        return


@dataclass
class SYS_ASS_ED(EditorAssignmentAction):  # noqa N801
    """Manages action SYS_ASS_ED."""

    def __post_init__(self):
        self.action_triggers_import_files = True


class ED_SEL_N_ED(EditorAssignmentAction):  # noqa N801
    """Manages action ED_SEL_N_ED."""


class ADMIN_ASS_N_ED(EditorAssignmentAction):  # noqa N801
    """Manages action ADMIN_ASS_N_ED."""


class ReviewAssignmentAction(BaseActionManager):
    """Review assignment management.

    All actions of this class map (roughly) to ReviewAssignment.
    Review assignments are created onto the current review round; see external loop on versions.
    """

    def run(self):
        self.check_editor_set()

        # reviewer data from Current_Referees
        reviewer_data = self.read_reviewer_data()

        # Reviewer not in Current_Referees - for example a removed referee
        #
        # The Action_History contains some data of the referee-related actions:
        # - referee assignment
        # - referee acceptance
        # - referee removal
        # - ..
        #
        # Current_Referees contains all the data of the (current) referees assignments (it is the
        # closest thing to Janeway's ReviewAssignment). But, if a referee has been "removed", the
        # relative assignment data is lost (we have a note about it only in Action_History).
        #
        # In wjapp, only referees that have not done any report can be removed.
        #
        # The import process loops on the action_history and executes all the actions version by
        # version.  When a referee assignment data is found also in Current_Referees, it is checked to
        # extract data (the fact that the action exists, means that the referee has not been removed),
        # otherwise remain only the action data.

        if not reviewer_data:
            reviewer_data = {
                "refereeCod": self.action["targetCod"],
                "refereeLastName": self.action["targetLastname"],
                "refereeFirstName": self.action["targetFirstname"],
                "refereeEmail": self.action["targetEmail"],
                "refereeAssignDate": self.action["actionDate"],
                "report_due_date": None,
                "refereeAcceptDate": None,
            }

        # select reviewer message
        reviewer_message = self.read_reviewer_message()
        logger.debug(f"Reviewer message: {reviewer_message.get('documentLayerCod')}")

        self.set_reviewer(reviewer_data, reviewer_message)

    def read_reviewer_data(self):
        """Read reviewer data."""

        cursor_reviewer = self.connection.cursor(dictionary=True)
        query_reviewer = """
SELECT
refereeCod,
u.lastName  AS refereeLastName,
u.firstName AS refereeFirstName,
u.email     AS refereeEmail,
assignDate  AS refereeAssignDate,
refereeReportDeadlineDate AS report_due_date,
acceptDate AS refereeAcceptDate
FROM Current_Referees c
LEFT JOIN User u ON (u.userCod=c.refereeCod)
WHERE
        versioncod=%(imported_version_cod)s
    AND refereeCod=%(user_cod)s
ORDER BY assignDate
"""
        cursor_reviewer.execute(
            query_reviewer,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
            },
        )
        reviewer_data = cursor_reviewer.fetchone()
        cursor_reviewer.close()
        return reviewer_data

    def read_reviewer_message(self):
        """Read the message that is sent to the reviewer when he is assigned to a paper."""

        cursor_reviewer_message = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (5") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        query_reviewer_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(user_cod)s
AND dl.documentLayerType='EMAIL'
AND ur.userType='recipient'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 5 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_reviewer_message.execute(
            query_reviewer_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_reviewer_message.rowcount != 1:
            logger.error(f"Found {cursor_reviewer_message.rowcount} reviewer assignment messages: {self.preprintid}")
            reviewer_message = None
        else:
            reviewer_message = cursor_reviewer_message.fetchone()
        cursor_reviewer_message.close()
        return reviewer_message

    def set_reviewer(self, reviewer_data, reviewer_message):
        """Set a reviewer."""

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_data["refereeCod"],
            reviewer_data["refereeLastName"],
            reviewer_data["refereeFirstName"],
            reviewer_data["refereeEmail"],
        )
        logger.debug(f"Creating review assignment of {self.article.id} to reviewer {reviewer}")

        request = create_fake_request(user=None, journal=self.journal)
        request.user = self.get_current_editor()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_data["refereeAssignDate"]),
        ):
            # default message from settings
            # TODO: verify mail subject exists
            # TODO: verify signature in the final message request.user.signature is not missing

            interval_days = get_setting(
                "wjs_review",
                "acceptance_due_date_days",
                self.journal,
            )
            # wjapp does not record a due-date, so we set a fictitious date that simulates what wjs would do
            # using freeze_time now() is refereeAssignDate
            date_due = timezone.now().date() + datetime.timedelta(days=interval_days.process_value())

            if reviewer_message:
                message = newlines_text_to_html(reviewer_message.get("documentLayerText"))
                self.imported_document_layer_cod_list.append(reviewer_message.get("documentLayerCod"))
                logger.debug(f"append reviewer message: {reviewer_message.get('documentLayerCod')}")

            else:
                message = render_template_from_setting(
                    setting_group_name="wjs_review",
                    setting_name="review_invitation_message_body",
                    journal=self.journal,
                    request=request,
                    context={
                        "article": self.article,
                        "request": request,
                    },
                    template_is_setting=True,
                )
                logger.warning(
                    f"used default reviewer message {reviewer=} {self.article=} {self.get_current_editor()=}"
                )

            form_data = {
                "acceptance_due_date": date_due,
                "message": message,
            }
            review_assignment = AssignToReviewer(
                reviewer=reviewer,
                workflow=self.article.articleworkflow,
                editor=self.get_current_editor(),
                form_data=form_data,
                request=request,
            ).run()

            # refereeAcceptDate = 1970-01-02 01:00:00 from wjapp means "refereeAcceptDate not set"
            if reviewer_data["refereeAcceptDate"] and reviewer_data["refereeAcceptDate"].year != 1970:
                request = create_fake_request(user=None, journal=self.journal)
                request.user = reviewer

                with freezegun.freeze_time(
                    rome_timezone.localize(reviewer_data["refereeAcceptDate"]),
                ):
                    EvaluateReview(
                        assignment=review_assignment,
                        reviewer=reviewer,
                        editor=self.get_current_editor(),
                        form_data={"reviewer_decision": "1", "accept_gdpr": True},
                        request=request,
                        token=None,
                    ).run()
                    if reviewer_data["report_due_date"]:
                        datetime_due = rome_timezone.localize(
                            reviewer_data["report_due_date"],
                        )
                        # note: review_assignment date_due is datetime.date not datetime.datetime
                        review_assignment.date_due = datetime_due.date()
                        review_assignment.save()

        return review_assignment


class ED_ASS_REF(ReviewAssignmentAction):  # noqa N801
    """Manages wjapp action ED_ASS_REF."""


class ED_ADD_REF(ReviewAssignmentAction):  # noqa N801
    """Manages wjapp action ED_ADD_REF."""


class DeselectReviewerAction(BaseActionManager):
    """Editor deselect reviewer."""

    def run(self):
        # wjapp actions for editor deselect reviewer (ex:JCOM_005N_0324)

        # EQ1_ED_REM_REF	[#referee=1]/editor removes referee (ex. JCOM_007A_0724)
        # GT1_ED_REM_REF	[#referee>1]/editor removes referee (ex. JCOM_013A_0724)
        # ED_REM_REF	editor removes referee (ex. JCOM_018A_0724)

        logger.warning(f"management of {self.action['actionID']} WIP")

        reviewer_cod = self.action["targetCod"]
        reviewer_lastname = self.action["targetLastname"]
        reviewer_firstname = self.action["targetFirstname"]
        reviewer_email = self.action["targetEmail"]
        reviewer_deselection_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
        )

        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()

        request = create_fake_request(user=None, journal=self.journal)
        request.user = self.get_current_editor()

        # deselect reviewer message
        deselect_reviewer_message = self.read_deselect_reviewer_message()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_deselection_date),
        ):
            DeselectReviewer(
                assignment=review_assignment,
                editor=review_assignment.editor,
                request=request,
                send_reviewer_notification=False,
                form_data={
                    "notification_subject": deselect_reviewer_message.get("documentLayerSubject"),
                    "notification_body": deselect_reviewer_message.get("documentLayerText"),
                },
            ).run()

    def read_deselect_reviewer_message(self):
        """Read the message that is sent to the reviewer when he is deselected."""

        cursor_deselect_reviewer_message = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (6") respect to the action_date
        # ex. 6" delay JCOM_009A_0124  actionDate:  2024-04-01 16:54:02 docLayer submissionDate 2024-04-01 16:54:07

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported_version_cod ensures to retrive the correct article

        query_deselect_reviewer_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(user_cod)s
AND dl.documentLayerType='EMAIL'
AND ur.userType='recipient'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 6 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_deselect_reviewer_message.execute(
            query_deselect_reviewer_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_deselect_reviewer_message.rowcount != 1:
            logger.error(
                f"Found {cursor_deselect_reviewer_message.rowcount} deselect reviewer messages: {self.preprintid}"
            )
            deselect_reviewer_message = None
        else:
            deselect_reviewer_message = cursor_deselect_reviewer_message.fetchone()
        cursor_deselect_reviewer_message.close()

        return deselect_reviewer_message


class EQ1_ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action EQ1_ED_REM_REF."""


class GT1_ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action GT1_ED_REM_REF."""


class ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action ED_REM_REF."""


class ReviewerDeclineAction(BaseActionManager):
    """Reviewer decline management."""

    def run(self):
        # wjapp actions for referee declined assignment for preprintid in wjapp:

        # - EQ1_REF_REF: this action indicates that a referee declined an assignment on a
        #   paper with exactly one referee (i.e. the paper has no more active review assignments)

        # - GT1_REF_REF: this action indicates that a referee declined an assignment on a
        #   paper with more than one referee (i.e. the paper has still active review assignments)

        # - REF_REF:  this action indicates that a referee declined an assignment.
        #   It is present in the wjapp code and Action table, but seems not used.
        #   Probably has been replaced by the two above. Added for completeness

        self.check_editor_set()
        reviewer_decline_message = self.read_reviewer_decline_message()
        self.reviewer_declines(reviewer_decline_message)

    def read_reviewer_decline_message(self):
        """Read decline message."""

        cursor_reviewer_decline_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (5") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        query_reviewer_decline_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='EMAIL'
AND ur.userType='author'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 5 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_reviewer_decline_message.execute(
            query_reviewer_decline_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_reviewer_decline_message.rowcount != 1:
            logger.error(
                f"Found {cursor_reviewer_decline_message.rowcount} reviewer decline messages: {self.preprintid}"
            )
            reviewer_decline_message = None
        else:
            reviewer_decline_message = cursor_reviewer_decline_message.fetchone()
        cursor_reviewer_decline_message.close()
        return reviewer_decline_message

    def reviewer_declines(self, reviewer_decline_message):
        """Reviewer declines."""

        reviewer_cod = self.action["agentCod"]
        reviewer_lastname = self.action["agentLastname"]
        reviewer_firstname = self.action["agentFirstname"]
        reviewer_email = self.action["agentEmail"]
        reviewer_declines_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
        )

        request = create_fake_request(user=None, journal=self.journal)
        request.user = reviewer

        # We create versions (and RAs) in a serial fashion, i.e. one after the other,
        # respecting the temporal order in which they have been originally created;
        # so we are always working on the latest/current version/review-round.

        # replaced "get()"" with "filter last()" to fix case JCOM_013A_0524:
        # more review assignment for the same referee in the same wjapp
        # version (same wjs review round)
        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_declines_date),
        ):
            logger.debug(f"Reviewer {reviewer} declines {self.article}")

            decline_reason = newlines_text_to_html(reviewer_decline_message.get("documentLayerText"))
            self.imported_document_layer_cod_list.append(reviewer_decline_message.get("documentLayerCod"))
            logger.debug(f"append reviewer decline message: {self.imported_document_layer_cod_list=}")

            EvaluateReview(
                assignment=review_assignment,
                reviewer=reviewer,
                editor=self.get_current_editor(),
                form_data={"reviewer_decision": "0", "decline_reason": decline_reason, "accept_gdpr": True},
                request=request,
                token=None,
            ).run()

        return


class EQ1_REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action EQ1_REF_REF."""


class GT1_REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action GT1_REF_REF."""


class REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action REF_REF."""


class REF_ACC(BaseActionManager):  # noqa N801
    """Reviewer send report management: wjapp action REF_SENDS_REP."""

    def run(self):
        logger.warning("REF_ACC managed in ReviewAssignmentAction but without reviewer confirmation message")


class REF_SENDS_REP(BaseActionManager):  # noqa N801
    """Reviewer send report management: wjapp action REF_SENDS_REP."""

    def run(self):
        # Reviewer send report

        self.check_editor_set()
        self.reviewer_send_report(self.read_reviewer_report_message())

    def read_reviewer_report_message(self):
        """Read report message."""

        cursor_reviewer_report_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp a certain message is not directly linked to an action. So we make a list of REREP
        # from the reviewer, in a certain time range (-10" +5") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - imported _version_cod ensures to retrive the correct article

        query_reviewer_report_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerText,
dl.documentLayerOnlyTex
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='REREP'
AND ur.userType='author'
AND dl.submissionDate>DATE_SUB(%(action_date)s, INTERVAL 10 SECOND)
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 5 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_reviewer_report_message.execute(
            query_reviewer_report_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_reviewer_report_message.rowcount != 1:
            logger.error(f"Found {cursor_reviewer_report_message.rowcount} reviewer report: {self.preprintid}")
            reviewer_report_message = None
        else:
            reviewer_report_message = cursor_reviewer_report_message.fetchone()
        cursor_reviewer_report_message.close()
        return reviewer_report_message

    def reviewer_send_report(
        self,
        wjapp_report,
    ):
        """Reviewer sends report."""

        reviewer_cod = self.action["agentCod"]
        reviewer_lastname = self.action["agentLastname"]
        reviewer_firstname = self.action["agentFirstname"]
        reviewer_email = self.action["agentEmail"]
        reviewer_report_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
        )

        # filter and last() not get() to manage JCOM_008A_0324 with 2 review assignments
        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()
        request = create_fake_request(user=None, journal=self.journal)
        request.user = reviewer
        submit_final = True

        # the (current) default review form element "Cover letter for the Editor (confidential)"
        # has a rich-text/html widget.  Text from wjapp formatted to html
        formatted_cover_letter_message = newlines_text_to_html(wjapp_report.get("documentLayerText"))

        # the (current) default review form element "Report text (to be sent to authors)"
        # has a rich-text/html widget. Text from wjapp formatted to html
        formatted_report_message = newlines_text_to_html(wjapp_report.get("documentLayerOnlyTex"))

        # we leave empty those fields that don't exist in wjapp and
        # that do not have a "mandatory" value (such as no-conflict-of-interests).
        report_form = get_report_form(self.journal)
        jcom_report_form_data = {
            "no_conflict_of_interest": True,
            "structure_and_writing_style": "",
            "originality": "",
            "scope_and_methods": "",
            "argument_and_discussion": "",
            "recommendation": "",
            "editor_cover_letter": formatted_cover_letter_message,
            "author_review": formatted_report_message,
        }
        form = report_form(
            data=jcom_report_form_data, review_assignment=review_assignment, request=request, submit_final=True
        )

        # form is not valid because missing required fields
        # import data problem: the required fields have fixed select values ex:
        # Structure and writing style: Poor | Acceptable | Good | Excellent
        # and we have not this values from wjapp
        #
        # we do not need to check our data because they already exist in the (wjapp) system
        # and so we can consider them to be valid a priori
        if form.is_valid():
            logger.warning("Report form is valid this was unexpected because of missing required fields")

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_report_date),
        ):
            # SubmitReview does not validate the form.
            # the form is validated in view ReviewSubmit -> ReportForm.save()
            # and SubmitReview is called only afterwards
            submit = SubmitReview(
                assignment=review_assignment,
                form=form,
                submit_final=submit_final,
                request=request,
            )
            submit.run()

            self.imported_document_layer_cod_list.append(wjapp_report.get("documentLayerCod"))
            logger.debug(f"append referee report message {self.imported_document_layer_cod_list=}")

        return


@dataclass
class EditorDecisionAction(BaseActionManager):
    """Editor decision management."""

    editor_decison: tuple = field(init=False)
    requires_revision: bool = field(init=False)
    revision_interval_days: int = field(init=False)

    def run(self):
        # wjs editor report store:
        #
        # - for ED_REQ_REV, ED_ACC_DOC_WMC
        #     the EDREP is visible for the author on revision request page
        #        the view is ArticleRevisionUpdate based on model EditorRevisionRequest
        #         the templates are
        #            "wjs_review/revision/revision_form.html.
        #            --> wjs_review/revision/elements/info.html
        #
        # - for ED_REJ_DOC the EDREP is NOT visible for the author
        #
        # - all editor reports are stored in EditorDecision.decision_editor_report
        #
        # - editor reports with revision request are stored also in EditorRevisionRequest

        self.check_editor_set()

        revision = self.editor_decides()
        if self.requires_revision:
            logger.debug(f"editor decision with revision request EditorRevisionRequest: {revision=}")

    def read_editor_report_message(self):
        """Read editor report message."""

        cursor_editor_report_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp a certain message is not directly linked to an action. So we make a list of EDREP
        # from the editor, in a certain time range (-10" +5") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - imported _version_cod ensures to retrive the correct article

        query_editor_report_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerText,
dl.documentLayerOnlyTex
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='EDREP'
AND ur.userType='author'
AND dl.submissionDate>DATE_SUB(%(action_date)s, INTERVAL 10 SECOND)
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 5 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_editor_report_message.execute(
            query_editor_report_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_editor_report_message.rowcount != 1:
            logger.error(f"Found {cursor_editor_report_message.rowcount} editor report: {self.preprintid}")
            editor_report_message = None
        else:
            editor_report_message = cursor_editor_report_message.fetchone()
            logger.debug(f"{self.preprintid} EDREP: {editor_report_message.get('documentLayerCod')}")
        cursor_editor_report_message.close()
        return editor_report_message

    def editor_decides(self):
        """Editor decides on article."""

        wjapp_editor_report = self.read_editor_report_message()
        editor_report_date = self.action["actionDate"]

        # the (current) default review form element for editor cover letter
        # has a rich-text/html widget.  Text from wjapp formatted to html
        wjapp_editor_cover_letter_message = newlines_text_to_html(wjapp_editor_report.get("documentLayerText"))

        # the (current) default review form element for editor report
        # has a rich-text/html widget.  Text from wjapp formatted to html
        # ex. JCOM_027Y_0215 has the cover letter but not the report file
        wjapp_editor_report_message = newlines_text_to_html(wjapp_editor_report.get("documentLayerOnlyTex"))
        editor_report_message = "<br><br><br><br>".join(
            filter(None, [wjapp_editor_cover_letter_message, wjapp_editor_report_message])
        )

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.get_current_editor()

        with freezegun.freeze_time(
            rome_timezone.localize(editor_report_date),
        ):
            date_due = timezone.now().date()
            if self.requires_revision:
                date_due = date_due + datetime.timedelta(days=self.revision_interval_days)

            # TBV: date_due has to be set in the form in the case of rejection?
            form_data = {
                "decision": self.editor_decision,
                "decision_editor_report": editor_report_message,
                "withdraw_notice": "notice",
                "date_due": date_due,
            }

            handle = HandleDecision(
                workflow=self.article.articleworkflow,
                form_data=form_data,
                user=self.get_current_editor(),
                request=request,
            )
            handle.run()
        self.article.refresh_from_db()
        revision = None
        if self.requires_revision:
            revision = EditorRevisionRequest.objects.get(
                article=self.article, review_round=self.article.current_review_round_object()
            )

        self.imported_document_layer_cod_list.append(wjapp_editor_report.get("documentLayerCod"))
        logger.debug(f"append editor report message {self.imported_document_layer_cod_list=}")

        return revision


@dataclass
class ED_REQ_REV(EditorDecisionAction):  # noqa N801
    """Manages wjapp action ED_REQ_REQ: editor requires major revision."""

    def __post_init__(self):
        self.editor_decision = ArticleWorkflow.Decisions.MAJOR_REVISION
        self.requires_revision = True
        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_major_revision_days",
            self.journal,
        ).process_value()


@dataclass
class ED_ACC_DOC_WMC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action ED_ACC_DOC_WMC: editor requires minor revision."""

    def __post_init__(self):
        self.editor_decision = ArticleWorkflow.Decisions.MINOR_REVISION
        self.requires_revision = True
        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_minor_revision_days",
            self.journal,
        ).process_value()


@dataclass
class ED_REJ_DOC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action ED_REJ_DOC: editor rejects."""

    def __post_init__(self):
        self.editor_decision = ArticleWorkflow.Decisions.REJECT
        self.requires_revision = False
        self.revision_interval_days = 0


@dataclass
class ED_CON_NOT_SUIT(EditorDecisionAction):  # noqa N801
    """Manages wjapp action ED_CON_NOT_SUIT: editor considers not suitable."""

    def __post_init__(self):
        self.editor_decision = ArticleWorkflow.Decisions.NOT_SUITABLE
        self.requires_revision = False
        self.revision_interval_days = 0


# TBV: DEBUG 2024-06-02 11:00:33,000 M:logic: No XML galleys found for crossref citation extraction
# TO FIX: exeception
@dataclass
class ED_ACC_DOC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action ED_ACC_DOC: editor accepts."""

    def __post_init__(self):  # noqa
        self.editor_decision = ArticleWorkflow.Decisions.ACCEPT
        self.requires_revision = False
        self.revision_interval_days = 0


class AuthorSubmitRevisionAction(BaseActionManager):
    """Author submit revision management."""

    def run(self):
        # TBV: author of the action is the same of main_author?
        #      the author could be switched with coauthor

        author_report_date = self.action["actionDate"]

        request = create_fake_request(user=None, journal=self.journal)
        request.user = self.article.correspondence_author

        author_note = self.read_author_cover_letter_message()

        form_data = {"author_note": newlines_text_to_html(author_note["documentLayerText"])}

        revision_request = EditorRevisionRequest.objects.get(
            article=self.article, review_round=self.article.current_review_round_object()
        )

        with freezegun.freeze_time(
            rome_timezone.localize(author_report_date),
        ):
            service = AuthorHandleRevision(
                revision=revision_request,
                form_data=form_data,
                user=self.article.correspondence_author,
                request=request,
            )
            service.run()

        self.article.refresh_from_db()

        self.imported_document_layer_cod_list.append(author_note.get("documentLayerCod"))
        logger.debug(f"append cover letter message {self.imported_document_layer_cod_list=}")
        return

    def read_author_cover_letter_message(self):
        """Read author cover letter message."""

        cursor_cover_letter_message = self.connection.cursor(buffered=True, dictionary=True)

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - imported _version_cod ensures to retrive the correct article

        query_cover_letter_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='CVLETT'
AND ur.userType='author'
AND dl.submissionDate>DATE_SUB(%(action_date)s, INTERVAL 10 SECOND)
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 5 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_cover_letter_message.execute(
            query_cover_letter_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_cover_letter_message.rowcount != 1:
            logger.error(f"Found {cursor_cover_letter_message.rowcount} cover letter: {self.preprintid}")
            cover_letter_message = None
        else:
            cover_letter_message = cursor_cover_letter_message.fetchone()
            logger.debug(f"{self.preprintid} CVLETT: {cover_letter_message.get('documentLayerCod')}")
        cursor_cover_letter_message.close()

        return cover_letter_message


class AU_SUB_REV(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action AU_SUB_REV."""


class AU_SUB_REV_WMC(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action AU_SUB_REV_WMC."""


class SelectCoauthorAction(BaseActionManager):
    """Coauthor selection management."""

    def run(self):

        # coauthor is the target of the wjapp action
        coauthor_cod = self.action["targetCod"]
        coauthor_lastname = self.action["targetLastname"]
        coauthor_firstname = self.action["targetFirstname"]
        coauthor_email = self.action["targetEmail"]
        coauthor_assign_date = self.action["actionDate"]

        # coauthor data
        coauthor = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            coauthor_cod,
            coauthor_lastname,
            coauthor_firstname,
            coauthor_email,
        )
        logger.debug(f"Creating coauthor of {self.article.id} user: {coauthor}")

        # NOTE: the wjapp message related to select coauthor acion is imported to wjs
        # as general correspondence
        with freezegun.freeze_time(
            rome_timezone.localize(coauthor_assign_date),
        ):
            if not coauthor.check_role(self.journal, "author"):
                coauthor.add_account_role("author", self.journal)
            self.article.authors.add(coauthor)
            self.article.save()

        return


class AU_SELECTS_COAUT(SelectCoauthorAction):  # noqa N801
    """Manages wjapp action AU_SELECTS_COAUT."""


class ADMIN_SELECTS_COAUT(SelectCoauthorAction):  # noqa N801
    """Manages wjapp action ADMIN_SELECTS_COAUT."""
