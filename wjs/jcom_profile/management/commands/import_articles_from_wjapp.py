"""Import article from wjapp."""

import datetime
import os
import tarfile
import textwrap
import traceback
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

import freezegun
import mariadb
import requests
from core import files
from core.middleware import GlobalRequestMiddleware
from core.models import Account, Galley, SupplementaryFile
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files import File as DjangoFile
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from identifiers import models as identifiers_models
from journal.models import Issue, IssueType, Journal
from plugins.typesetting.models import (
    GalleyProofing,
    TypesettingAssignment,
    TypesettingRound,
)
from plugins.wjs_review import communication_utils
from plugins.wjs_review.logic import (
    AssignToEditor,
    AssignToReviewer,
    AuthorHandleRevision,
    DeselectReviewer,
    EditorRevisionRequest,
    EvaluateReview,
    HandleDecision,
    HandleEditorDeclinesAssignment,
    OpenAppeal,
    PermissionAssignment,
    Reminder,
    SubmitReview,
    SupervisorChangeEditorAssignment,
    WithdrawPreprint,
    WorkflowReviewAssignment,
    render_template_from_setting,
)
from plugins.wjs_review.logic__production import (
    AssignTypesetter,
    AuthorSendsCorrections,
    HandleEOSendBackToTypesetter,
    ReadyForPublication,
    RequestProofs,
    UploadFile,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    EditorDecision,
    Message,
    MessageRecipients,
    PastEditorAssignment,
    WjsEditorAssignment,
)
from plugins.wjs_review.permissions import is_article_typesetter
from plugins.wjs_review.utils import get_report_form
from review.models import (
    EditorAssignment,
    ReviewAssignment,
    ReviewRound,
    RevisionRequest,
)
from submission import models as submission_models
from submission.models import Licence
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile import constants
from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_utils import (
    JANEWAY_LANGUAGES_BY_CODE,
    JOURNALS_DATA,
    NON_PEER_REVIEWED,
    SECTIONS_MAPPING,
    check_mappings,
    rome_timezone,
    set_author_country,
    sync_frozen_authors_with_authors,
)
from wjs.jcom_profile.permissions import get_hijacker, has_eo_role
from wjs.jcom_profile.utils import create_rich_fake_request, get_eo_user


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

        journal_code = self.options["preprintid"].split("_")[0].upper()
        if journal_code in (
            "JCOM",
            "JCOMAL",
        ):
            self.journal = Journal.objects.get(code=journal_code)
            self.journal_data = JOURNALS_DATA[journal_code]
            self.import_data_article(**options)
        else:
            logger.error(f"Journal not identified from {self.options['preprintid']} {journal_code}. Please check.")
            return

    def add_arguments(self, parser):
        """Add arguments to command."""

        parser.add_argument(
            "--preprintid",
            default="",
            help="jcom/jcomal wjApp preprintid ex: JCOM_010A_0324",
            required=True,
        )
        parser.add_argument(
            "--importfiles",
            default=False,
            action="store_true",
            help="also downloads files from wjapp jcom/jcomal",
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
                    f"Cannot import files, quitting."
                )
                return
            elif login_parameters.get("username", "") == "":
                logger.error(
                    f'Empty username parameter for "{login_setting}". Please ensure `username`, etc. are correct.'
                    f"Cannot login, quitting."
                )
                return

            if not getattr(settings, f"WJAPP_{self.journal.code.upper()}_BASE_URL", None):
                self.stderr.write(
                    f"Missing base wjapp url, import files is not possible. Please set "
                    f"WJAPP_{self.journal.code.upper()}_BASE_URL in your django settings to proceed.",
                )
                return

            username = login_parameters.get("username", "")
            passwd = login_parameters.get("password", "")
            login_base_url = login_parameters.get("login_base_url", "")
            http_ba_username = login_parameters.get("http_ba_username", "")
            http_ba_password = login_parameters.get("http_ba_password", "")

            if not http_ba_username and self.journal.code.upper() == "JCOM":
                logger.error(
                    f"Missing Basic Authentication username for {self.journal.code.upper()}. "
                    f"Check your django settings to proceed."
                )
                return

            session = self.wjapp_login(username, passwd, login_base_url, http_ba_username, http_ba_password)

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

        # structure of the global nested dict used to save the associations for EDREP, REREP, CVLETT
        # ("documentLayerCod1" is the primary key of one Document Layer):
        #
        # {...
        #  "documentLayerCod1": {"obj": wjs_obj, "type": "REREP",}
        # ...}
        self.imported_doclayer_check_visibility = {}

        if not preprintid:
            return
        setting = f"WJAPP_{self.journal.code.upper()}_IMPORT_CONNECTION_PARAMS"
        connection_parameters = getattr(settings, setting, None)

        if connection_parameters is None:
            logger.error(
                f'Missing connection parameters for {self.journal.code}. Please ensure "{setting}" exists in settings.'
                f"Cannot connect, quitting."
            )
            return
        elif connection_parameters.get("user", "") == "":
            logger.error(
                f'Empty connection parameters for "{setting}". Please ensure `user`, `host`, etc. are correct.'
                f"Cannot connect, quitting."
            )
            return

        self.connection = mariadb.connect(**connection_parameters)

        current_version_row = self.read_article_data(preprintid)

        if not current_version_row:
            self.connection.close()
            logger.debug(f"Article not found {self.journal.code} {preprintid}.")
            return

        document_cod = current_version_row["documentCod"]
        preprintid = current_version_row["preprintId"]
        publicationid = current_version_row["publicationId"]
        document_revision_dead_line = current_version_row["revisionDeadline"]
        section = current_version_row["documentType"]
        language = current_version_row["language"]
        version_cod = current_version_row["versionCod"]
        article_expected_final_state = current_version_row["stateID"]
        # current_version -> row  "versionNumber"

        logger.info(f"""Importing {preprintid}""")

        #
        # check consistency between preprintid and publicationid
        # regarding already imported articles
        #

        # specific case correction: different pubid:
        # - preprintid: JCOMAL_006A_1022
        # - wjapp pubid: JCOMAL_0602_2023_A10"
        # - wjs pubid: JCOMAL_0602_2023_V01
        if preprintid == "JCOMAL_006A_1022" and publicationid == "JCOMAL_0602_2023_A10":
            publicationid = "JCOMAL_0602_2023_V01"
            logger.warning(
                f"replace pubid because different in wjapp and wjs for {preprintid},"
                f" wjapp: {current_version_row['publicationId']}"
                f" wjs: {publicationid}"
            )

        # to be checked to avoid to add roles editor
        self.original_editor_roles = Account.objects.filter(
            accountrole__journal=self.journal, accountrole__role__slug=constants.SECTION_EDITOR_ROLE
        )
        # to avoid queryset lazy evaluation problem
        self.ids_orig_list = list(self.original_editor_roles.values_list("id", flat=True))

        # search if exists an id for publicationid in the wjs journal
        pub_article = None
        if publicationid:
            pub_article = submission_models.Article.get_article(
                journal=self.journal,
                identifier_type="pubid",
                identifier=publicationid,
            )
            if not pub_article:
                logger.error(
                    f"missing published version for {publicationid}. Import published version"
                    f" before to import review and production versions of {preprintid}"
                )
                return

        # search if exists an id for preprintid in the wjs journal
        article = submission_models.Article.get_article(
            journal=self.journal,
            identifier_type="preprintid",
            identifier=preprintid,
        )

        # Scenarios
        #
        # Note:
        # - publicationid is the article found searching the publicationid
        # - preprintid is the article found searching the preprintid
        #
        # 1. publicationid != preprintid
        #       runtime error
        #
        # 2. preprintid  == publicationid
        #       re-import preprintid versions, restore publicationid status
        #
        # 3. publicationid exists and preprintid is none
        #       import preprintid using publicationid, restore publicationid status
        #
        # 4. preprintid exists and publicationid is none
        #       re-import preprintid versions
        #
        # 5. preprintid is none and publicationid is none
        #       import preprintid versions creating new id
        #
        # e.g. published papers: JCOM_002N_0722 JCOM_005A_1022 JCOM_002N_0323
        self.stored_status = False

        try:
            if pub_article and article:
                if pub_article.id == article.id:
                    self.store_article_status(pub_article)
                    logger.debug(
                        f"Re-importing history ({preprintid}) of a paper already published in WJS "
                        f"{publicationid} / {pub_article.id}"
                    )

                else:
                    raise RuntimeError(
                        f"different wjs id for pubid {pub_article.id} and preprintid {article.id}"
                        f"delete {preprintid} from wjs and import in wjs the galley for {publicationid}"
                        f"afterwards import old versions of {preprintid}"
                    )

            elif pub_article and not article:
                logger.debug(
                    f"Importing history ({preprintid}) of a paper already published in WJS "
                    f"({publicationid} / {pub_article.id}"
                )
                article = pub_article
                self.store_article_status(article)

            elif not pub_article and article:
                logger.debug(f"Re-importing {preprintid} as {article.id} (paper is not published).")

            else:
                logger.debug(f"Importing {preprintid} (paper is not published).")

            #
            # publicationid can be used as flag to check if the import is a published paper import
            #

            if article:
                # This is not the default situation: if we are here it
                # means that the article has been already imported and
                # that we are re-importing.
                self.reset_article_data(article, publicationid)
            else:
                article = submission_models.Article.objects.create(
                    journal=self.journal,
                )

            identifiers_models.Identifier.objects.get_or_create(
                identifier=preprintid,
                article=article,
                id_type="preprintid",  # NOT a member of the set identifiers_models.IDENTIFIER_TYPES
                enabled=True,
            )
            logger.debug(f"Set preprintid {preprintid} onto {article.pk}")

            # now article is defined and, if exists, is also the the published article

            # create article and section
            article, main_author = self.create_main_article_data(current_version_row, article)

            # get article last saved country and profession
            #
            # country conversion:
            #
            # taken from import_utils the country mapping conversion from wjapp to wjs
            # (added one: Iran)
            #
            # profession conversion:
            #
            # wjs
            # TBV: actual valus in wjs are 2,3,4 seems out of range of jcom_profile models -> PROFESSIONS (0-3)
            #
            # wjapp
            # +-------------+------------+------------------------------------------------------------------------+
            # |professionCod|professionId|name                                                                    |
            # +-------------+------------+------------------------------------------------------------------------+
            # |            1|str         |A researcher in S&T studies, science communication or neighbouring field|
            # |            2|stp         |A practitioner in S&T (e.g. journalist, museum staff, writer, ...)      |
            # |            3|sci         |An active scientist                                                     |
            # |            4|oth         |Other                                                                   |
            # +-------------+------------+------------------------------------------------------------------------+
            #
            # profession map: wjapp professionCod -1

            author_data = self.read_document_author_data(document_cod)

            # main author profession from article
            if author_data:
                main_author.jcomprofile.profession = author_data["professionCod"] - 1
                main_author.jcomprofile.save()
                main_author.save()

                # main author country from article
                set_author_country(
                    author=main_author,
                    data={
                        "userCod": f"Account {main_author.id}",
                        "countryName": author_data["country_name"],
                    },
                )

            # article section
            if section == "paper":
                section = "article"
                logger.debug(
                    f"for {preprintid} / {article.id} section 'paper' not existing in wjs, mapped to 'article'"
                )
            self.set_section(article, section)

            # article language
            self.set_language(article, language)

            # article keywords
            keywords = self.read_article_keywords(version_cod)
            self.set_keywords(article, keywords)

            # article special issue
            special_issue = self.read_article_special_issue(document_cod)
            self.set_article_special_issue(article, special_issue, preprintid)

            # article notes
            document_notes = self.read_article_document_notes(document_cod)
            self.set_article_document_notes(article, document_notes)

            # read all version versionNum, versionCod
            versions = self.read_versions_data(document_cod)

            # In wjapp, the concept of version is paramount. All actions revolve around versions.
            # Here we cycle through each version and manage the data that need.
            for v in versions:
                imported_version_cod = v["versionCod"]
                imported_version_num = v["versionNumber"]
                imported_version_state_cod = v["stateCod"]
                # author bios no more imported #imported_version_bios_text = v["authorsBio"]

                # read actions history from wjapp preprint
                history = self.read_history_data(imported_version_cod)

                # Note: editor selection is done with actions of the history

                for action in history:
                    # TODO: move the update of imported_document_layer_cod_list out of the action manager
                    # using as return value of each action manager run() the partial list
                    logger.debug(f"Looking at action {action['actionID']} ({action['actHistCod']})")

                    # in case of editor reassign during the production the first editor acceptance is changed
                    # in ED_ACC_DOC_WMC to permit the author resubmission.
                    # These wjapp actions are skipped TYP_UPLOADS_FOR_PM PM_REQ_ED_CHECK ED_RESTARTS_REV
                    # and afterwards the author resubmission continues the workflow
                    special_cases = {
                        ("JCOM_003A_0417", 2, 265550),
                        ("JCOM_012A_0615", 2, 260964),
                    }
                    if (preprintid, imported_version_num, action["actHistCod"]) in special_cases:
                        logger.warning(
                            f"change action {action['actionID']} to ED_ACC_DOC_WMC "
                            f"for {preprintid}/{imported_version_num} (manage editor restart - known case)."
                        )
                        action["actionID"] = "ED_ACC_DOC_WMC"

                    if action_manager := globals().get(action["actionID"]):
                        # "actionID" is something like SYS_ASS_ED, that is also
                        # the name of a class defined in this module
                        #
                        # To find papers when some action has been executed, try something like:
                        # set @action = 'ED_REF_DOC';
                        # select d.prePrintID from Action a
                        #   left join Action_History ah using (actionCod)
                        #   left join Version v using (versionCod)
                        #   left join Document d using (documentCod)
                        # where a.actionID = @action
                        # order by d.submissionDate desc
                        # limit 3;
                        action_manager(
                            action=action,
                            connection=self.connection,
                            session=session,
                            journal=self.journal,
                            preprintid=preprintid,
                            publicationid=publicationid,
                            document_revision_dead_line=document_revision_dead_line,
                            article=article,
                            imported_version_num=imported_version_num,
                            imported_version_cod=imported_version_cod,
                            imported_version_state_cod=imported_version_state_cod,
                            importfiles=self.options["importfiles"],
                            imported_document_layer_cod_list=self.imported_document_layer_cod_list,
                            action_triggers_import_files=False,
                            imported_doclayer_check_visibility=self.imported_doclayer_check_visibility,
                            url_base=getattr(settings, f"WJAPP_{self.journal.code.upper()}_BASE_URL", None),
                        ).run()
                    else:
                        # ADMIN_RESETS_ED is skipped, only loaded the message as correspondence
                        # the following action to reassign a new editor is managed with wjs logic
                        # The "fake" new version of wjapp is not created on wjs.
                        # It is managed like it was a reassign editor ADMIN_ASS_N_ED in the same version
                        # E.g. JCOM_010A_1123 has the 3 actions: ED_SEL_N_ED ADMIN_RESETS_ED ADMIN_ASS_N_ED

                        # PSTPN_REV_DEADLN is skipped because wjapp has only one date "revision deadline"
                        # for the document, therefore it is set at the moment of the editor decision.
                        # The related message is imported as correspondence
                        # E.g. JCOM_001A_0419
                        # (the date can be wrong if there are more editor decisions)
                        managed_in_import_correspondence = [
                            "ED_REMINDS_REF",
                            "ADMIN_REMINDS_AUTH",
                            "ED_REMINDS_AUTH",
                            "ADMIN_REMINDS_REF",
                            "ADMIN_REMINDS_ED",
                            "SYS_REMINDS_DIR",
                            "SYS_REMINDS_ED",
                            "SYS_REMINDS_REF",
                            "SYS_REMINDS_AUT",
                            "PM_REMINDS_AUTH",  # i.e. JCOM_016A_1124
                            "ADMIN_RESETS_ED",
                            "PSTPN_REV_DEADLN",  # <someone> postpones revision deadline
                            "PM_REMINDS_TYP",
                        ]
                        if action["actionID"] in managed_in_import_correspondence:
                            logger.debug(f"Action {action['actionID']} managed in import correspondence.")
                        else:
                            logger.warning(f"Action {action['actionID']} not yet managed.")

                # set_authors bios(..)
                # arguments: imported_version_bios_text, article, imported_version_num
                # no more called because the source data are not reliable

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

            ImportPermissionsManager(
                connection=self.connection,
                session=session,
                journal=self.journal,
                preprintid=preprintid,
                article=article,
                imported_doclayer_check_visibility=self.imported_doclayer_check_visibility,
            ).run()

            self.clean_deleted_coauthors(article, document_cod)

            # verify added editor roles
            self.new_editor_roles = Account.objects.filter(
                accountrole__journal=self.journal, accountrole__role__slug=constants.SECTION_EDITOR_ROLE
            )

            for a in self.new_editor_roles:
                if a.id not in self.ids_orig_list:
                    a.remove_account_role(constants.SECTION_EDITOR_ROLE, self.journal)
                    logger.warning(f"editor role added by the import removed: {a.id} {a.full_name()}")

        except Exception as e:
            traceback.print_exc()
            logger.error(
                f"""An exception {type(e).__name__} occurred for {article.id} / {preprintid}
                 {str(e)},

                The preprintid {article.id} / {preprintid} must be imported again
                """
            )

        # fix forced manual withdrawn appeal on wjapp without action in the history
        if preprintid in ("JCOM_015Y_0515", "JCOM_008A_1120", "JCOMAL_002Y_0921"):
            if article.stage != submission_models.STAGE_REJECTED:
                logger.warning(f"stage is '{article.stage}' for withdrawn appeal article {preprintid} / {article.id}")
                article.stage = submission_models.STAGE_REJECTED
                article.save()
                logger.warning(
                    f"forced fixed stage to '{article.stage}' for withdrawn appeal article {preprintid} / {article.id}"
                )
            if ArticleWorkflow.objects.filter(article=article).exists():
                if article.articleworkflow.state != ArticleWorkflow.ReviewStates.REJECTED:
                    logger.warning(
                        f"review state is '{article.articleworkflow.state}' "
                        f"for rejected article {preprintid} / {article.id}"
                    )
                    article.articleworkflow.state = ArticleWorkflow.ReviewStates.REJECTED
                    article.articleworkflow.save()
                    article.save()
                    logger.warning(
                        f"forced fixed review state to '{article.articleworkflow.state}' "
                        f"for withdrawn appeal article {preprintid} / {article.id}"
                    )
        elif article_expected_final_state == "REJECTED":
            if article.stage != submission_models.STAGE_REJECTED:
                logger.error(f"stage not rejected: {article.stage=}")
            if (
                ArticleWorkflow.objects.filter(article=article).exists()
                and article.articleworkflow.state != ArticleWorkflow.ReviewStates.REJECTED
            ):
                logger.error(f"state not rejected: {article.articleworkflow.state=}")
        else:
            # TODO: verify cases where an error should be logged
            logger.debug(f"state: {article_expected_final_state=} {article.stage=}")
            if ArticleWorkflow.objects.filter(article=article).exists():
                logger.debug(f"{article.articleworkflow.state=}")

        # for published import restore the article status
        if publicationid:
            self.restore_article_status(article)

            # for published import set the article stage to Published
            if article.stage != submission_models.STAGE_PUBLISHED:
                logger.warning(f"stage is '{article.stage}' for published article {preprintid} / {article.id}")
                article.stage = submission_models.STAGE_PUBLISHED
                article.save()
                logger.warning(
                    f"forced fixed stage to '{article.stage}' for published article {preprintid} / {article.id}"
                )

            # for published import set the article review state to Published.
            # There are paper in wjapp which are published, but have incomplete history
            # then the review state must be forced to published. e.g.JCOM_015A_0215
            if ArticleWorkflow.objects.filter(article=article).exists():
                if article.articleworkflow.state != ArticleWorkflow.ReviewStates.PUBLISHED:
                    logger.warning(
                        f"review state is '{article.articleworkflow.state}' "
                        f"for published article {preprintid} / {article.id}"
                    )
                    article.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
                    article.articleworkflow.save()
                    article.save()
                    logger.warning(
                        f"forced fixed review state to '{article.articleworkflow.state}' "
                        f"for published article {preprintid} / {article.id}"
                    )

        if ArticleWorkflow.objects.filter(article=article).exists():
            for r in Reminder.objects.filter(disabled=False):
                if article == r.get_related_article():
                    if article.articleworkflow.state in (
                        ArticleWorkflow.ReviewStates.PUBLISHED,
                        ArticleWorkflow.ReviewStates.REJECTED,
                        ArticleWorkflow.ReviewStates.WITHDRAWN,
                        ArticleWorkflow.ReviewStates.NOT_SUITABLE,
                    ):
                        r.disabled = True
                        r.save()
                        logger.debug(
                            f"forced disabled reminder for {article.id}/{preprintid}"
                            f" in state {article.articleworkflow.state}"
                        )

        self.debug_list_article_files_imported(article)
        self.debug_list_reminder(article)

        self.connection.close()

    #
    # http login to wjapp
    #

    def wjapp_login(self, username, passwd, login_base_url, http_ba_username, http_ba_password):
        """Login to wjapp to download files."""

        # TODO: add login successful check (verify reponse.content)
        payload = {
            "userid": f"{username}",
            "password": f"{passwd}",
            "orcidid": "",
            "loginOkRedUrl": f"{login_base_url}index.jsp",
            "loginFailRedUrl": f"{login_base_url}index.jsp",
            "submit": "Sign in",
        }

        with requests.Session() as session:
            session.auth = (http_ba_username, http_ba_password)
            session.login_base_url = login_base_url
            # login
            p = session.post(f"{login_base_url}authentication/authenticate", data=payload)
            assert p.status_code == 200, f"Got {p.status_code}!"

        return session

    #
    # functions to save and restore published article status
    #

    def store_article_status(self, article):
        self.store_article_language = article.language

        self.store_article_title = article.title

        if hasattr(article, "title_en") and article.title_en:
            self.store_article_title_en = article.title_en
        if hasattr(article, "title_es") and article.title_es:
            self.store_article_title_es = article.title_es
        if hasattr(article, "title_pt") and article.title_pt:
            self.store_article_title_pt = article.title_pt

        self.store_article_abstract = article.abstract

        if hasattr(article, "abstract_en") and article.abstract_en:
            self.store_article_abstract_en = article.abstract_en
        if hasattr(article, "abstract_es") and article.abstract_es:
            self.store_article_abstract_es = article.abstract_es
        if hasattr(article, "abstract_pt") and article.abstract_pt:
            self.store_article_abstract_pt = article.abstract_pt

        self.store_article_date_submitted = article.date_submitted
        self.store_article_date_published = article.date_published
        self.store_article_date_accepted = article.date_accepted

        self.store_article_doi = article.get_doi()
        self.store_article_pubid = article.get_pubid()

        # date revision ?

        self.store_article_authors = {}
        for a in article.authors.all():
            order = submission_models.ArticleAuthorOrder.objects.filter(
                article=article,
                author=a,
            )[0].order
            self.store_article_authors[a] = order
        self.store_article_owner = article.owner
        self.store_article_correspondence_author = article.correspondence_author

        self.store_article_license_id = article.license_id

        self.store_article_keywords = []
        for k in article.keywords.all():
            self.store_article_keywords.append(k)

        # article.articleworkflow.eo_in_charge is not defined for only published article

        # we store authors bios but author professions not, they are re-imported
        self.store_authors_bios = {}
        for a in article.authors.all():
            self.store_authors_bios[a] = a.biography

        self.store_article_section = article.section

        # the special issue ?

        self.store_article_primary_issue = article.primary_issue

        self.article_page_numbers = article.page_numbers

        # to not loose the render galley for an unexpected problem
        self.store_article_render_galley = article.render_galley

        self.stored_status = True

    def restore_article_status(self, article):
        if not self.stored_status:
            logger.error("no article status stored")
            return

        article.language = self.store_article_language
        logger.warning(f"{self.store_article_language=}")

        article.title = self.store_article_title
        logger.warning(f"{self.store_article_title=}")

        if hasattr(self, "store_article_title_en"):
            article.title_en = self.store_article_title_en
            logger.warning(f"{self.store_article_title_en=}")

        if hasattr(self, "store_article_title_es"):
            article.title_es = self.store_article_title_es
            logger.warning(f"{self.store_article_title_es=}")

        if hasattr(self, "store_article_title_pt"):
            article.title_pt = self.store_article_title_pt
            logger.warning(f"{self.store_article_title_pt=}")

        article.abstract = self.store_article_abstract
        logger.warning(f"{self.store_article_abstract=}")

        if hasattr(self, "store_article_abstract_en"):
            article.abstract_en = self.store_article_abstract_en
            logger.warning(f"{self.store_article_abstract_en=}")

        if hasattr(self, "store_article_abstract_es"):
            article.abstract_es = self.store_article_abstract_es
            logger.warning(f"{self.store_article_abstract_es=}")

        if hasattr(self, "store_article_abstract_pt"):
            article.abstract_pt = self.store_article_abstract_pt
            logger.warning(f"{self.store_article_abstract_pt=}")

        article.date_submitted = self.store_article_date_submitted
        logger.warning(f"{self.store_article_date_submitted=}")

        article.date_accepted = self.store_article_date_accepted
        logger.warning(f"{self.store_article_date_accepted=}")

        article.date_published = self.store_article_date_published
        logger.warning(f"{self.store_article_date_published=}")

        # depending on settings enabled during import the doi can be modified
        # so we drop any DOI an re-create the correct one
        identifiers_models.Identifier.objects.filter(
            id_type="doi",
            article=article,
        ).delete()

        identifiers_models.Identifier.objects.create(
            id_type="doi",
            article=article,
            identifier=self.store_article_doi,
        )
        logger.warning(f"{self.store_article_doi=}")

        # depending on settings enabled during import the pubid can be modified
        try:
            pubid_identifier = identifiers_models.Identifier.objects.get(
                id_type="pubid",
                article=article,
            )
        except identifiers_models.Identifier.DoesNotExist:
            logger.error(f"No pubid identifier found for {article.id}.")
            pubid_identifier = identifiers_models.Identifier.objects.create(
                id_type="pubid",
                article=article,
                enabled=True,
            )
        except identifiers_models.Identifier.MultipleObjectsReturned:
            logger.error(f"More than one pubid identifier found for {article.id}, taken the first.")
            pubid_identifier = identifiers_models.Identifier.objects.filter(
                id_type="pubid",
                article=article,
            )[0]
        if pubid_identifier.identifier != self.store_article_pubid:
            logger.info(
                f"Resetting old DOI {self.store_article_pubid} onto generated {pubid_identifier.identifier} "
                f"for {article.id}"
            )
            pubid_identifier.identifier = self.store_article_pubid
            pubid_identifier.save()

        # date revision ?

        article.authors.clear()
        logger.warning(f"{self.store_article_authors=}")
        for a in self.store_article_authors.keys():
            article.authors.add(a)
            ao, created = submission_models.ArticleAuthorOrder.objects.get_or_create(
                article=article,
                author=a,
                defaults={
                    "order": self.store_article_authors[a],
                },
            )
            if created:
                ao.order = self.store_article_authors[a]
                ao.save()
            logger.warning(f"author: {a}")

        article.owner = self.store_article_owner
        logger.warning(f"{self.store_article_owner=}")

        article.correspondence_author = self.store_article_correspondence_author
        logger.warning(f"{self.store_article_correspondence_author=}")

        article.license_id = self.store_article_license_id
        logger.warning(f"{self.store_article_license_id=}")

        article.keywords.clear()
        for k in self.store_article_keywords:
            article.keywords.add(k)
            logger.warning(f"keyword {k.id} {k}")

        # published paper has no articleworkflow therefore no eo_in_charge}")

        for a in self.store_article_authors:
            logger.warning(f"{a} {self.store_authors_bios.get(a, None)}")

        article.section = self.store_article_section
        logger.warning(f"{self.store_article_section=}")

        # the special issue ?

        article.primary_issue = self.store_article_primary_issue
        logger.warning(f"{self.store_article_primary_issue=}")

        article.page_numbers = self.article_page_numbers
        logger.warning(f"{self.article_page_numbers=}")

        # to not loose the render galley for an unexpected problem
        if not article.render_galley:
            logger.warning(f"missing render galley {self.store_article_render_galley} restored")
            article.render_galley = self.store_article_render_galley

        article.save()
        article.refresh_from_db()

        logger.warning("article status restored")

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
d.publicationId,
d.documentType,
d.submissionDate,
d.authorCod,
d.eoInChargeCod,
d.revisionDeadline,
d.language,
u2.lastname AS eoInCharge_lastname,
u2.firstname AS eoInCharge_firstname,
u2.email AS eoInCharge_email,
u2.privacy AS eoInCharge_privacy,
u1.lastname AS author_lastname,
u1.firstname AS author_firstname,
u1.email AS author_email,
u1.privacy AS author_privacy,
v.versionCod,
v.versionNumber,
v.versionTitle,
v.versionAbstract,
v.stateCod,
s.stateID
FROM Document d
LEFT JOIN User u1 ON (d.authorCod=u1.userCod)
LEFT JOIN Version v ON (v.documentCod=d.documentCod)
LEFT JOIN User u2 ON (d.eoInChargeCod=u2.userCod)
LEFT JOIN State s ON (v.stateCod=s.stateCod)
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

    def read_document_author_data(self, document_cod):
        """Read profession from document author data.

        Warning: always return data of the latest correspondence author,
        even for earlier versions where the corresponding-author was someone else
        """

        cursor_author_data = self.connection.cursor(dictionary=True)
        query_author_data = """
SELECT
da.professionCod,
da.countryCod,
c.name AS country_name
FROM
Document_Author da
LEFT JOIN Country c using (countryCod)
WHERE
        documentCod=%(document_cod)s
ORDER BY documentAuthorCod DESC
LIMIT 1
"""
        cursor_author_data.execute(query_author_data, {"document_cod": document_cod})
        author_data = cursor_author_data.fetchone()
        cursor_author_data.close()
        return author_data

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

    def read_article_special_issue(self, document_cod):
        """Read article special issue."""
        cursor_special_issue = self.connection.cursor(dictionary=True)
        query_special_issue = """
SELECT
si.issueCod,
si.name,
si.description,
si.longname,
si.editorCod AS editor_cod,
si.enabled,
si.visible,
u.firstname AS editor_firstname,
u.lastname AS editor_lastname,
u.email AS editor_email,
u.privacy AS editor_privacy
FROM Document d
LEFT JOIN Special_Issue si USING (issueCod)
LEFT JOIN User u ON (si.editorCod=u.userCod)
WHERE documentCod=%(document_cod)s
"""
        cursor_special_issue.execute(query_special_issue, {"document_cod": document_cod})
        special_issue = cursor_special_issue.fetchone()
        cursor_special_issue.close()
        return special_issue

    def read_article_document_notes(self, document_cod):
        """Read article notes."""
        cursor_document_notes = self.connection.cursor(dictionary=True)
        query_document_notes = """
SELECT
dn.documentNoteCod,
dn.documentNoteID,
dn.documentNoteContent,
dn.submissionDate,
dn.authorCod AS note_author_cod,
u.firstname AS note_author_firstname,
u.lastname AS note_author_lastname,
u.email AS note_author_email,
u.privacy AS note_author_privacy
FROM Document_Note dn
LEFT JOIN User u ON (dn.authorCod=u.userCod)
WHERE documentCod=%(document_cod)s
"""
        cursor_document_notes.execute(query_document_notes, {"document_cod": document_cod})
        document_notes = cursor_document_notes.fetchall()
        cursor_document_notes.close()
        return document_notes

    def read_versions_data(self, document_cod):
        """Read article versions data."""
        cursor_versions = self.connection.cursor(dictionary=True)
        query_versions = """
SELECT
versionCod,
versionNumber,
stateCod,
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
u1.privacy AS agentPrivacy,
ah.userCod AS targetCod,
u2.lastname AS targetLastname,
u2.firstname AS targetFirstname,
u2.email AS targetEmail,
u2.editorWorkload AS targetEditorWorkload,
u2.privacy AS targetPrivacy,
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

    def read_all_coauthors(self, document_cod):
        """Read article current coauthors."""

        cursor_coauthors = self.connection.cursor(dictionary=True)
        query_coauthors = """
SELECT
c.coauthorCod,
u.lastname,
u.firstname,
u.email,
u.privacy
FROM Coauthors c
LEFT JOIN User u ON (userCod=coauthorCod)
WHERE
    documentCod=%(document_cod)s
"""
        cursor_coauthors.execute(query_coauthors, {"document_cod": document_cod})
        coauthors = cursor_coauthors.fetchall()
        return coauthors

    #
    # functions to set data in wjs
    #

    def reset_article_data(self, article, publicationid):
        """Reset article data for re-import of the article."""

        for reminder in Reminder.objects.all():
            if reminder.get_related_article() == article:
                reminder.delete()

        # clean some data related to the article
        for err in EditorRevisionRequest.objects.filter(article=article):
            if err.cover_letter_file:
                err.cover_letter_file.unlink_file()
                err.cover_letter_file.delete()

            for f in err.manuscript_files.all():
                f.unlink_file()
                f.delete()

            for f in err.data_figure_files.all():
                f.unlink_file()
                f.delete()

            for f in err.source_files.all():
                f.unlink_file()
                f.delete()

            for f in err.article.supplementary_files.all():
                f.file.unlink_file()
                f.file.delete()
                f.delete()

            # delete PA for CVLETT EDREP
            PermissionAssignment.objects.filter(
                object_id=err.editor_decision.pk,
            ).delete()
            PermissionAssignment.objects.filter(
                object_id=err.pk,
            ).delete()
            err.delete()

        # delete WorkflowReviewAssignment's files (reviewers' report files)
        for wra in WorkflowReviewAssignment.objects.filter(article=article):
            if wra.review_file:
                wra.review_file.unlink_file()
                wra.review_file.delete()
            # delete PA for REREP PA
            PermissionAssignment.objects.filter(
                object_id=wra.pk,
            ).delete()

        WorkflowReviewAssignment.objects.filter(article=article).delete()

        for rr in ReviewRound.objects.filter(article=article):
            EditorDecision.objects.filter(review_round=rr).delete()

        ReviewRound.objects.filter(article__id=article.id).delete()

        # necessary to delete
        Message.objects.filter(object_id=article.id).delete()

        # NOTE: the galley must never be deleted: they are never created by the import
        # and if already present (published article) must remain

        # TODO: supplementary_files are similar, but different:
        # - if paper is in review (not yet accepted), do nothing
        # - if paper is in production (after acceptance, before publication)
        #   - attachments of latest version be moved to `A.supplementary_files` (this simulates the typesetter
        #     uploading them and ensures they will be "published")
        # - (optional) if paper is published, we can add attachments of the accepted version as
        #   A.AW.supplementary_files_at_acceptance

        for f in article.manuscript_files.all():
            f.unlink_file()
            f.delete()

        for f in article.data_figure_files.all():
            f.unlink_file()
            f.delete()

        for f in article.source_files.all():
            f.unlink_file()
            f.delete()

        # TODO: the article supplementary files must be deleted only for NOT published
        if not publicationid:
            for f in article.supplementary_files.all():
                f.unlink_file()
                f.delete()

        if not ArticleWorkflow.objects.filter(article=article).exists():
            ArticleWorkflow.objects.create(article=article)
            logger.warning(
                f"created ArticleWorkflow during reset article {article}, "
                f"probably missing due to a crashed import, please check"
            )

        # delete  ArticleWorkflow's files
        for f in article.articleworkflow.supplementary_files_at_acceptance.all():
            logger.warning(f"reset Supplementary {f.id} {f} {f.file}")
            f.file.unlink_file()
            f.file.delete()
            f.delete()

        # delete TypesettingAssignment's files
        for ta in TypesettingAssignment.objects.filter(round__article=article):
            for f in ta.files_to_typeset.all():
                f.unlink_file()
                f.delete()
            for g in ta.galleys_created.all():
                g.file.unlink_file()
                g.file.delete()

        TypesettingAssignment.objects.filter(round__article=article).delete()
        TypesettingRound.objects.filter(article=article).delete()

        RevisionRequest.objects.filter(article__id=article.id).delete()
        ReviewAssignment.objects.filter(article__id=article.id).delete()
        EditorAssignment.objects.filter(article__id=article.id).delete()
        PastEditorAssignment.objects.filter(article__id=article.id).delete()
        submission_models.KeywordArticle.objects.filter(article__id=article.id).delete()
        PermissionAssignment.objects.filter(
            object_id=article.articleworkflow.id,
        ).delete()
        article.articleworkflow.delete()

        submission_models.ArticleAuthorOrder.objects.filter(article__id=article.id).delete()
        article.authors.clear()

        submission_models.ArticleStageLog.objects.filter(article__id=article.id).delete()
        article.stage = submission_models.STAGE_UNASSIGNED

        # for NOT published the identifier doi must be removed (temporary doi)
        if not publicationid:
            identifiers_models.Identifier.objects.filter(
                article=article,
                id_type="doi",
            ).delete()

        # Note: the article object is not deleted, only the data are reset
        #
        # There are objects related to the article that are not deleted
        # because are reused:
        #
        # Frozen authors: not to delete
        # Galleys: not to delete
        # identifiers: not to delete
        # ProphyCandidates: not to delete. if resent to prophy they will be updated.

    def create_main_article_data(self, row, article):
        """Create the article."""

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
            row["author_privacy"],
            self.connection,
        )

        if not row["eoInChargeCod"]:
            eo_in_charge = None
        else:
            eo_in_charge = account_get_or_create_check_correspondence(
                self.journal.code.lower(),
                row["eoInChargeCod"],
                row["eoInCharge_lastname"],
                row["eoInCharge_firstname"],
                row["eoInCharge_email"],
                row["eoInCharge_privacy"],
                self.connection,
            )

        if not main_author.check_role(self.journal, "author", staff_override=False):
            main_author.add_account_role("author", self.journal)
        article.owner = main_author
        article.authors.add(main_author)
        article.correspondence_author = main_author
        article.license_id = Licence.objects.get(journal=self.journal, short_name="CC BY-NC-ND 4.0").id
        article.save()

        # authors order is set to order=author.pk, but for corresponding where is order=0
        submission_models.ArticleAuthorOrder.objects.create(
            article=article,
            author=main_author,
            order=0,
        )

        article.articleworkflow.eo_in_charge = eo_in_charge

        article.articleworkflow.production_flag_no_queries = False
        article.articleworkflow.production_flag_no_checks_needed = False
        article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_FAILED

        article.articleworkflow.save()

        article.refresh_from_db()
        return (article, main_author)

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
        # There are versions e.g. JCOM_002N_0324/5 (current version published) without bio
        #
        # TODO: if there are maintenance on wjapp with changes in coauthors
        # without actions in the history, better to check also the db with
        # a direct query? The actions must be managed the same for the timeline.
        #
        # NOTE: decison from jcom-eo: import only bios with name at the beginning. In any case
        #       the authors will check/correct/enter their bio after migration
        # TODO: name match can be improved
        if not bios_text:
            logger.warning(f"No author bios found in wjapp for version: {imported_version_num} {article} ")
            return

        authors_bios = bios_text.split("\r\n\r\n")

        if len(authors_bios) != article.authors.count():
            logger.warning(f"Authors bios paragraphs: {len(authors_bios)}, article authors: {article.authors.count()}")

        for author in article.authors.all():
            bios_found = self.get_author_bio_by_name(authors_bios, author)
            if len(bios_found) != 1:
                logger.debug(
                    f"Found {len(bios_found)} bios for author {author.full_name()} version: {imported_version_num}."
                )

            # saved first bio found or let unchanged
            if bios_found:
                # TODO: add always to article frozen author frozen biography

                logger.debug(f"Updated bio for author {author.full_name()} version: {imported_version_num}.")
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

        # missing match case:
        # JCOM_001A_0924 Maria Magdalena Rosu (hyphen problem)
        bios_found = []
        for bio in authors_bios:
            if bio.startswith(author.full_name()):
                bios_found.append(bio)
            elif author.first_name in bio and author.last_name in bio:
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
                f'Created section "{section_name}" for {article.get_identifier("preprintid")}. Please check!'
            )

        article.section = section
        if article.section.name in NON_PEER_REVIEWED:
            article.peer_reviewed = False

        article.save()

    def set_language(self, article, language):
        """Set the article language from the wjapp text field"""

        # in wjapp Document.language is a free text field also with empty sting and null values
        # i.e.:
        # JCOM_003A_0415 NULL
        # JCOM_002Y_0523 English--CANADA
        # JCOM_007A_0623 ING
        # JCOM_024A_0923 anglais
        # JCOM_001BR_0324 WALKER_2024_BOOK_REVIEW_Amplifying.docx
        # JCOM_001V_1224 Indonesian
        # JCOM_002V_1224 Indonesia
        # JCOM_004A_0325 Portuguese
        # JCOM_002A_0325 English
        # JCOM_001L_0325 ""
        # JCOM_004N_1024 ---

        # default for jcom when not defined
        if not language and article.journal.code == "JCOM":
            article.language = "eng"
            article.save()
            logger.debug(f"jcom {article.id} undefined language saved as 'eng'")
            return

        # default for jcomal when not defined
        if not language and article.journal.code == "JCOMAL":
            logger.warning(f"jcomal {article.id} undefined language let None")
            return

        if language:
            language = language.strip()
        else:
            logger.warning(f"{article.id} undefined language let None")
            return

        # for the management of not normalized or wrong wjapp laguages used these choices with the
        # the same format of submission models LANGUAGE_CHOICES.
        # The actual distinct values from jcomal are: Spanish, Portuguese, Português, Español, Espanhol, NULL
        # the others are from jcom
        wjapp_not_normalized_languages_by_code = {
            "fra": ["french"],
            "spa": ["Spain", "Español", "Spanish (Español)", "Espanhol"],
            "por": ["Brazilian Portuguese", "Português"],
            "ita": ["Italiano"],
            "eng": [
                "English--CANADA",
                "ING",
                "anglais",
                "English and Spanish",
                "ENG",
                "Italian (and English)",
                "English",
            ],
            "ind": ["Indonesia", "Bahasa Indonesia"],
        }

        for lang_code, funny_names in wjapp_not_normalized_languages_by_code.items():
            if language in funny_names:
                logger.warning(f"""Wjapp's "{language}" mapped to "{lang_code}" for {article.id}""")
                article.language = lang_code
                article.save()
                return

        for lang_code in JANEWAY_LANGUAGES_BY_CODE.keys():
            trimmed_lang_name_list = [x.strip() for x in JANEWAY_LANGUAGES_BY_CODE[lang_code].split(";")]
            if language in trimmed_lang_name_list:
                logger.warning(f"""Wjapp's "{language}" mapped to "{lang_code}" for {article.id}""")
                article.language = lang_code
                article.save()
                return

        # default for jcom when not matched
        if not article.language and article.journal.code == "JCOM":
            article.language = "eng"
            article.save()
            logger.warning(f"jcom {article.id} not matched language <{language}> saved as 'eng'")
            return

        # default for jcomal when not matched
        if not language and article.journal.code == "JCOMAL":
            logger.warning(f"jcomal {article.id} not matched language {language} let None")
            return

        # wjapp language not null, but not found in the choices defined above
        if not article.language:
            logger.warning(f"{article.id} language {language} not matched let undefined")

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
                    f'Created keyword "{kwd_word}" for {article.get_identifier("preprintid")}. Please check!'
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

    def set_article_special_issue(self, article, special_issue, preprintid):
        """Set article special issue"""

        # in wjapp this means that is a normal article without special issue
        if "Normal" == special_issue["name"]:
            return

        if self.journal.code == "JCOMAL":
            issue = Issue.objects.get(pk=jcomal_map_si[special_issue["issueCod"]])
            issue.articles.add(article)
            article.primary_issue = issue
            article.save()
            # primary issue saved directly article not refreshed
            #
            # Note from: wjs/jcom_profile/tests/conftest.py
            # we must reload article from db as Article.primary_issue is set by a signal triggered by
            # m2m save, and thus our in memory article object has no knowledge of that change
            logger.debug(
                f"article {preprintid}/{article.id} from {special_issue['longname']}"
                f" added to issue: {issue.issue_title}"
            )
            return

        if self.journal.code == "JCOM":
            issue = Issue.objects.get(pk=jcom_map_si[special_issue["issueCod"]])
            issue.articles.add(article)
            article.primary_issue = issue
            article.save()
            # primary issue saved directly article not refreshed
            #
            # Note from: wjs/jcom_profile/tests/conftest.py
            # we must reload article from db as Article.primary_issue is set by a signal triggered by
            # m2m save, and thus our in memory article object has no knowledge of that change
            logger.debug(
                f"article {preprintid}/{article.id} from {special_issue['longname']}"
                f" added to issue: {issue.issue_title}"
            )
            return

        editor_special_issue = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            special_issue["editor_cod"],
            special_issue["editor_lastname"],
            special_issue["editor_firstname"],
            special_issue["editor_email"],
            special_issue["editor_privacy"],
            self.connection,
        )

        issue_short_name = special_issue["name"]
        issue_title = special_issue["longname"]
        issue_description = special_issue["description"]

        issue_enabled = special_issue["enabled"]
        issue_visible = special_issue["visible"]

        issue_type__code = "collection"
        issue_type = IssueType.objects.get(
            code=issue_type__code,
            journal=self.journal,
        )

        issue, _ = Issue.objects.get_or_create(
            journal=self.journal,
            issue_type=issue_type,
            short_name=issue_short_name,
            defaults={
                "issue_description": issue_description,
                "date_open": article.date_submitted,
                "issue_title": issue_title,
            },
        )
        issue.managing_editors.add(editor_special_issue)

        # if title, description have been changed we want to keep the last
        issue.issue_title = issue_title
        issue.issue_description = issue_description

        # date_open is set on the older submission date imported
        if issue.date_open > article.date_submitted:
            issue.date_open = article.date_submitted

        # Define a publication date in the future
        # if the issue is enabled and visible
        if issue_enabled and issue_visible:
            issue_publication_date = datetime.datetime(2025, 5, 30, 12, 0)
            issue.date = rome_timezone.localize(issue_publication_date)

        issue.articles.add(article)
        issue.save()
        # Note from: wjs/jcom_profile/tests/conftest.py
        # we must reload article from db as Article.primary_issue is set by a signal triggered by
        # m2m save, and thus our in memory article object has no knowledge of that change
        article.refresh_from_db()
        return issue

    def set_article_document_notes(self, article, document_notes):
        """Set article notes from wjapp document notes"""

        for dn in document_notes:
            note_author = account_get_or_create_check_correspondence(
                self.journal.code.lower(),
                dn["note_author_cod"],
                dn["note_author_lastname"],
                dn["note_author_firstname"],
                dn["note_author_email"],
                dn["note_author_privacy"],
                self.connection,
            )
            with freezegun.freeze_time(
                rome_timezone.localize(dn["submissionDate"]),
            ):
                document_note = Message.objects.create(
                    actor=note_author,
                    body=newlines_text_to_html(dn["documentNoteContent"]),
                    content_type=ContentType.objects.get_for_model(article),
                    object_id=article.id,
                    message_type=Message.MessageTypes.NOTE,
                )
                document_note.recipients.add(note_author)

    def clean_deleted_coauthors(self, article, document_cod):
        """Clean wjapp coauthors removed by EO or by wjapp maintenance"""

        current_coauthors_rows = self.read_all_coauthors(document_cod)
        current_coauthors = []
        for c in current_coauthors_rows:
            account = account_get_or_create_check_correspondence(
                self.journal.code.lower(),
                c["coauthorCod"],
                c["lastname"],
                c["firstname"],
                c["email"],
                c["privacy"],
                self.connection,
            )
            current_coauthors.append(account)

        assert article.correspondence_author == article.owner
        authors_to_check = [a for a in article.authors.all() if a != article.owner]
        authors_modified = False
        for author in authors_to_check:
            if author not in current_coauthors:
                # Remember that there is a pre-delete signal on FrozenAuthors that removes all authors/authors-order
                # however, we prefer to delete them explicitly because it's more clear
                submission_models.ArticleAuthorOrder.objects.get(author=author, article=article).delete()
                # filter insted of get because frozen author could not exist i.e. JCOM_007A_0516
                submission_models.FrozenAuthor.objects.filter(article=article, author=author).delete()
                article.authors.remove(author)
                authors_modified = True
                logger.debug(f"cleaned coauthor {author}")

        if authors_modified:
            sync_frozen_authors_with_authors(article)
            article.save()

    def debug_list_article_files_imported(self, article):
        """Log debug of all files imported also historical"""

        article = submission_models.Article.objects.get(id=article.pk)

        for f in article.manuscript_files.all():
            logger.debug(f"imported: {article.id} manuscript: {f.id} {f}")
        for f in article.source_files.all():
            logger.debug(f"imported: {article.id} source: {f.label} {f.id} {f}")
        for f in article.data_figure_files.all():
            logger.debug(f"imported: {article.id} data figure: {f.id} {f}")

        for f in article.articleworkflow.supplementary_files_at_acceptance.all():
            logger.debug(f"imported: {article.id} suppl. file at acceptance: {f.id} {f}")

        from plugins.wjs_review.models import EditorRevisionRequest

        err_list = EditorRevisionRequest.objects.filter(article=article)
        for e in err_list:
            for m in e.manuscript_files.all():
                logger.debug(f"imported: {article.id} rev round {e.review_round.round_number} manuscript: {m.id} {m}")
            for s in e.source_files.all():
                logger.debug(
                    f"imported: {article.id} rev round {e.review_round.round_number} source: {s.label} {s.id} {s}"
                )
            for d in e.data_figure_files.all():
                logger.debug(f"imported: {article.id} rev round {e.review_round.round_number} data figure: {d.id} {d}")

        for tr in article.typesettinground_set.all():
            ta = tr.typesettingassignment
            for f in ta.files_to_typeset.all():
                logger.debug(f"imported: {article.id} TA: {ta.round} FTT: {f.id} {f}")
            for g in ta.galleys_created.all():
                logger.debug(f"imported: {article.id} TA: {ta.round} GC: {g.id} {g} public: {g.public}")
            for gp in ta.round.galleyproofing_set.all():
                logger.debug(f"imported: {article.id} TA: {ta.round} GP: {gp.id} {gp.proofed_files.all()}")

        if pgs := article.articleworkflow.publication_galleys_source_file:
            logger.debug(f"imported: {article.id} publication galleys source uploaded: {pgs.id} {pgs}")

    def debug_list_reminder(self, article):
        """List all reminder set on the article"""

        # TBV: in JCOM_001N_0424 appear the reminders of version1 (AUMJR1 AUMJR2) should be deleted?
        for r in Reminder.objects.all():
            if r.get_related_article() == article:
                logger.debug(
                    f"reminder on {article.id}: {r.code} created:{r.date_created.date()} due:{r.date_due} "
                    f"sent:{r.date_sent} {r.disabled=} recipient:{r.recipient}"
                )


#
# Begin global variables and functions section
#
# In this section are defined global functions and global variables used by the global funcions

# global variables

# to avoid the search and the load of the user notes more times
# for the same user for the same article
# the import of the user notes is done when is imported the user account
user_notes_already_managed_in_this_article = []

# global variable which contains the association wjapp usercod, and wjs account object
# already imported: {...("usercod1": "account1" ), ...}
already_imported_users = {}


#
# SI maps of wjapp (key) and wjs (value) for each journal
#


# JCOMAL
jcomal_map_si = {
    2: 115,  # Medioambiente -- Medioambiente y divulgación
}


# JCOM
jcom_map_si = {
    105: 55,  # Special Issue on citizen science -- Special Issue: Citizen Science, Part I, 2016
    # 105: 57, # Special Issue on citizen science -- Special Issue: Citizen Science, Part II, 2016
    106: 63,  # History of Science Communication -- Special Issue: History of Science Communication, 2017
    107: 70,  # Special Issue on User Experience of Digital... -- Special Issue: User Experience of Digital...
    108: 72,  # Special Issue on Communication at the Intersect.. -- Special Issue: Communication at the Intersect...
    109: 74,  # Special Issue on Stories in Science Communication -- Special Issue: Stories in Science Communication..
    110: 88,  # Third International ECSA Conference -- Special Issue: Third International ECSA Conference, Trieste..
    111: 80,  # COVID-19 and science communication -- Special Issue: COVID-19 and science communication, Part I, 2020
    # 111: 82, # COVID-19 and science communication -- Special Issue: COVID-19 and science communication, Part II, 2020
    112: 85,  # Re-examining Science Communication -- Special Issue: Re-examining Science Communication: models, ...
    113: 91,  # Participatory Science Communication for... -- Special Issue Participatory science communication for...
    114: 93,  # Responsible Science Communication acro... -- Special Issue: Responsible science communication acro...
    115: 109,  # Living Labs Under Construction: Paradigms, ... -- Special Issue: Living labs under construction: ...
    116: 113,  # Science Communication in Higher Educati.. -- Special Issue: Science communication in higher educati..
    117: 117,  # Connecting Science Communication... -- Special Issue: Connecting science communication...
    118: 120,  # Science communication for social justice -- Special Issue: Science communication for social justice
    119: 126,  # Public (dis)trust in science... -- Special Issue: Public (dis)trust in science...
    120: 124,  # Communicating Discovery Science -- Special Issue: Communicating Discovery Science
    121: 129,  # Science Communication in the Age of Artificial... -- Science Communication in the Age of Artificial...
    122: 130,  # Emotions and Science Communication -- Emotions and Science Communication
    123: 131,  # Science in unexpected places -- Science in unexpected places
}

#
# global functions
#


def read_usernotes(target_user_cod, connection):
    """Reads the usernotes from wjapp."""

    cursor_usernotes = connection.cursor(dictionary=True)
    query_usernotes = """
SELECT
un.userNoteCod,
un.userNoteID,
un.userNoteContent,
un.submissionDate,
un.authorCod,
u1.firstname AS author_firstname,
u1.lastname AS author_lastname,
un.targetUserCod,
u2.firstname AS target_firstname,
u2.lastname AS target_lastname
FROM
User_Note un
LEFT JOIN User u1 ON (un.authorCod=u1.userCod)
LEFT JOIN User u2 ON (un.targetUserCod=u2.userCod)
WHERE
targetUserCod = %(user_cod)s
ORDER BY submissionDate DESC
"""
    cursor_usernotes.execute(
        query_usernotes,
        {
            "user_cod": target_user_cod,
        },
    )
    usernotes_rows = cursor_usernotes.fetchall()
    cursor_usernotes.close()
    return usernotes_rows


def get_usernotes(account, usernotes_rows):
    """Concatenate the usernotes in one string."""

    if not usernotes_rows:
        return None

    usernotes = ""
    for note in usernotes_rows:
        usernotes += f"""
{note.get("userNoteContent").strip()}

{note.get("submissionDate")} by {note.get("author_firstname")} {note.get("author_lastname")}


"""
    return usernotes


def account_get_or_create_check_correspondence(
    source, user_cod, last_name, first_name, imported_email, privacy, connection
):
    """Get a user account - check Correspondence and eventually create new account."""

    # ex: source: jcom, jcomal, prophy, ...
    # Check if we know this person form some other journal or by email

    # uses the global variable as "cache"
    # if the wjapp user_cod is in this global variable, it is already
    # been checked and found by this funcion for this article (the journal is fixed for the
    # current imported article). The execution of this management command only imports one article.
    if user_cod in already_imported_users.keys():
        logger.debug(f"found cached user: {already_imported_users[user_cod]}")
        return already_imported_users[user_cod]

    if imported_email == f"{source}_hidden_user@{source}.sissa.it":
        # using the wjapp userCod in the email the same hidden user is identified
        # in Correspondence in unique way after the hiding action on wjapp.
        # Searching in Correspondence with source and user_cod only can find
        # more than one match (email changes)
        imported_email = f"{user_cod}_{source}_hidden_user@invalid.com"
        logger.debug(f"found wjapp hidden user {user_cod=} {imported_email}")

    account_created = False
    mappings = wjs_models.Correspondence.objects.filter(
        Q(user_cod=user_cod, source=source) | Q(email__iexact=imported_email),
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
            f"wjs mapping exists ({mappings.count()} correspondences) for {user_cod}/{source} or {imported_email}"
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

    # Refresh the account to ensure the corresponding JCOMProfile exists,
    # otherwise we can get duplicated-email key-error when the cached object is saved.
    # As site notes, remember that Account.email is a case-insensitive field
    # (see https://www.postgresql.org/docs/current/citext.html)
    # while Correspondence.email is a simple EmailField.
    # Also, emails are normalized in Account.clean(),
    # but clean() is called by a ModelForm.is_valid() and not by Model.save().
    account.refresh_from_db()

    # set gdpr checkbox. privacy in wjapp can be 'Y', 'N' or empty string
    if not account.jcomprofile.gdpr_checkbox:
        if privacy == "Y":
            account.jcomprofile.gdpr_checkbox = True
        elif privacy == "N":
            account.jcomprofile.gdpr_checkbox = False
        else:
            # if we don't have a clear value we do nothing (which generally result in
            # the checkbox to be false, which is safe)
            pass
        account.jcomprofile.save()
        account.save()

    # import of user notes
    # E.g. JCOM_008A_0324
    if user_cod not in user_notes_already_managed_in_this_article:
        user_notes_already_managed_in_this_article.append(user_cod)
        if usernotes_rows := read_usernotes(user_cod, connection):
            usernotes = get_usernotes(account, usernotes_rows)
            # We silently overwrite any pre-existing user-note.
            # This is safe, because, before import, there are not notes
            # and new imports are supposed to overwrite previous ones.
            account.jcomprofile.usernotes = usernotes
            account.jcomprofile.save()
            logger.debug(f"saved {len(usernotes_rows)} usernotes for {account.id} {account.full_name()}")
            account.save()

    # saves the account in the global variable used as cache
    already_imported_users[user_cod] = account
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


def file_from_response(response: requests.Response, name: str) -> DjangoFile:
    """Extract a (django) File object from the given response, and give it the given name."""
    return DjangoFile(BytesIO(response.content), name)


def build_targz_archive_from_tex_response(response: requests.Response, name: str) -> DjangoFile:
    """Extract a tex File object from the response, and returns a dj file tar.gz."""

    tar_buffer = BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        tar_info = tarfile.TarInfo(f"{name}.tex")
        tar_info.size = len(response.content)
        tar.addfile(tar_info, BytesIO(response.content))

    tar_buffer.seek(0)
    logger.debug(f"generated tar.gz archive from tex production submission for {name}")

    return DjangoFile(tar_buffer, f"{name}.tar.gz")


def noop(*args, **kwdargs):
    """Do nothing.

    Used to disabled WJS automated messages.
    """
    pass


def noop_true(*args, **kwdargs):
    """return True

    Used to disabled WJS conditions
    """
    return True


def truncate_with_ellipsis(s):
    return textwrap.shorten(s, width=6, placeholder="...")


#
# End global variables and functions section
#


@dataclass
class ImportPermissionsManager:
    """Data class that manages the import of all the visibility permissions from wjapp.

    In wjapp when EDREP, REREP or CVLETT are created,
    several rows are added to User_Rights:
    - one for the "author" for the document (userType=author; e.g. for a REREP, the reviewer)
    - one for the "recipient" of the document (userType=recipient; e.g. for a REREP, the editor)
    - several other rows with userType=reader for other actors
      (e.g., for CVLETT, one row for each reviewer)

    User_Right thus links a user, a document(layer), and his visibility permissions.

    Mapping with wjs permissions types:
    in wjapp User_Rights:
    - when userType != "reader" for document-layers of type EDREP|REREP -> default permissions - nothing to do
    - when userType="reader", authorNameVisibility=0  (author name not visible) -> NO_NAMES
    - when userType="reader", authorNameVisibility=1  (author name visible) -> ALL
    - NB: for CVLETT, when the row userType="reader" is _missing_ -> DENY

    Objects interested:
    - CVLETT
      - data stored in EditorRevisionRequest.author_note/cover_letter_file
      - non-default permissions stored:
        - for reviewers as PermissionAssignment; see specs#1305
        - for (new) editors, as PermissionAssignment

    - EDREP
      - data stored in EditorDecision.decision_editor_report (referenced by ERR.editor_decision)
      - non-default permissions stored as PermissionAssignments with target=EditorDecision

    - REREP
      - data stored in WorflowReviewAssignment.report_form_answers
        which is a json field, and contains:
        - editor_cover_letter (cover letter REREP)
        - author_review (text REREP)
      - non-default permissions stored as PermissionAssignments

     Implementation:

     a) during the action import, when EDREP REREP CVLETT are saved, we keep a global nested dict with
        (documentLayerCod, {wjs_obj, type})
     b) at the end of article import:
        1. we read from wjapp the list of the extra permissions for the EDREP REREP CVLETT of the article
        2. the extra permission is set for each type
           CVLETT needs also to be set as DENY if the referee does not appear in the visibility rights of wjapp
           because this case means that the visibility right has been removed
     c) during the article import we also keep a global dict with (userCod, account_obj) to optimize the article
        users check. This dict is updated (and used as cache) by the global
        function account_get_or_create_check_correspondence()
    """

    connection: mariadb.Connection
    session: requests.sessions.Session
    journal: Journal
    preprintid: str
    article: submission_models.Article
    imported_doclayer_check_visibility: dict

    def run(self):
        """Import wjapp permissions for the imported article."""

        # paper that can be used as import test: JCOM_001A_0924

        for key, value in self.imported_doclayer_check_visibility.items():
            logger.debug(
                f"import permission documentLayerCod {key} class: {value['obj'].__class__} type: {value['type']}"
            )

            # from obj define content_type_id
            content_type = ContentType.objects.get_for_model(value["obj"])

            # read existing wjapp permission "reader" for documentLayerCod
            readers_rows = self.read_wjapp_permissions(key)
            logger.debug(f"found wjapp user rights readers: {readers_rows}")
            readers = []
            for r in readers_rows:
                # get reader account
                account = account_get_or_create_check_correspondence(
                    self.journal.code.lower(),
                    r["userCod"],
                    r["lastname"],
                    r["firstname"],
                    r["email"],
                    r["privacy"],
                    self.connection,
                )
                readers.append(account)

            # Manage reviewers which do not have visibility rights on cover letter
            # in wjapp the visibility right for this user has been removed
            # (i.e. it is a "missing" row in User_Rights)
            #
            # the ERR contains the review round of the version before the author revision.
            # The next review round is created by the logic of AuthorHandleRevision.
            #
            # Foreach wjs reviewer of the wra related to the new author version, we set
            # permission to DENY if in wjapp is not reader of the imported CVLETT
            if value["type"] in ("CVLETT"):
                revision_review_round = value["obj"].review_round.round_number + 1
                rr = ReviewRound.objects.get(article=self.article, round_number=revision_review_round)
                for wra in WorkflowReviewAssignment.objects.filter(article=self.article, review_round=rr):
                    if wra.reviewer not in readers:
                        # deny permission for cvlett
                        permission_type = PermissionAssignment.PermissionType.DENY
                        permission_secondary_type = PermissionAssignment.PermissionType.DENY
                        pa, created = PermissionAssignment.objects.get_or_create(
                            content_type_id=content_type.pk,
                            object_id=value["obj"].pk,
                            user=wra.reviewer,
                            defaults={
                                "permission": permission_type.value,
                                "permission_secondary": permission_secondary_type.value,
                            },
                        )
                        if not created:
                            # if existing only the permission secondary is changed
                            pa.permission_secondary = permission_secondary_type.value
                            pa.save()

            # add rights from wjapp present in User_Rights
            for reader_account in readers:
                # set wjs permission for the reader with PermissionAssignment get or create
                # i.e. JCOM_001A_0924 for CVLETT reader
                if value["type"] in ("REREP", "EDREP"):
                    if r["authorNameVisibility"]:
                        permission_type = PermissionAssignment.PermissionType.ALL
                    else:
                        permission_type = PermissionAssignment.PermissionType.NO_NAMES

                    # sets only primary permission if existing or not
                    pa, created = PermissionAssignment.objects.get_or_create(
                        content_type_id=content_type.pk,
                        object_id=value["obj"].pk,
                        user=reader_account,
                        defaults={
                            "permission": permission_type.value,
                        },
                    )
                    if not created:
                        pa = permission_type.value
                        pa.save()

                elif value["type"] in ("CVLETT"):
                    # NOTE: in wjapp the first versions has no cover letter, not necessary
                    # to manage the case (the cover letter in first version would be related
                    # to Article/ArticleWorkflow object)

                    # NOTE: this does not change the editor report PA for the user which are
                    # defined on the object EditorRevisionRequest.editor_decision

                    # set permission only for cvlett
                    permission_type = PermissionAssignment.PermissionType.DENY

                    # EditorRevisionRequest secondary permission manages the author cover letter
                    permission_secondary_type = PermissionAssignment.PermissionType.ALL

                    # TBV: the permission is created both for editor or reviewer user because if already exists
                    # is only updated, otherwise is created, but coherently with the permissions logic.
                    # In the case that the user is the reviewer and not the new editor, could be *not* necessary
                    # to set the permission (set by deafult).
                    pa, created = PermissionAssignment.objects.get_or_create(
                        content_type_id=content_type.pk,
                        object_id=value["obj"].pk,
                        user=reader_account,
                        defaults={
                            "permission": permission_type.value,
                            "permission_secondary": permission_secondary_type.value,
                        },
                    )
                    if not created:
                        # if existing only the permission secondary is changed
                        pa.permission_secondary = permission_secondary_type.value
                        pa.save()

                # check which permissions have been set for the object
                custom_permission = PermissionAssignment.objects.filter(
                    user=reader_account,
                    content_type=content_type,
                    object_id=value["obj"].pk,
                )
                for cp in custom_permission:
                    logger.debug(
                        f"PA existing - user: {cp.user} CT: {cp.content_type} obj: {cp.object_id} "
                        f"primary: {cp.permission} secondary: {cp.permission_secondary}"
                    )

    def read_wjapp_permissions(self, document_layer_cod):
        """Read the "reader" rows for the document_layer_cod."""

        cursor_all_reader_rights = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )
        query_all_reader_rights = """
SELECT
ur.documentLayerCod,
ur.userCod,
ur.userType,
ur.authorNameVisibility,
u.lastname,
u.firstname,
u.email,
u.privacy
FROM User_Rights ur
LEFT JOIN User u USING (userCod)
WHERE
    ur.documentLayerCod = (%(document_layer_cod)s)
AND ur.userType='reader'
"""
        cursor_all_reader_rights.execute(
            query_all_reader_rights,
            {
                "document_layer_cod": document_layer_cod,
            },
        )

        all_reader_rights = cursor_all_reader_rights.fetchall()
        cursor_all_reader_rights.close()
        return all_reader_rights


@dataclass
class ImportCorrespondenceManager:
    """Data class that manages the import of all the correspondence of the wjapp imported version."""

    # short description of all the correspondence types (all the wjapp journals)
    #
    # DOC LAYER EMAIL: EMAIL
    # DOC LAYER COVERLETTER: CVLETT
    # DOC LAYER REFEREE REPORT:REREP
    # DOC LAYER EDITOR REPORT:EDREP
    # DOC LAYER ANNOTATION:ANNOT
    # DOC LAYER TPS ANNOTATION:TPSAN
    # DOC LAYER PM ANNOTATION:PMANN
    # DOC LAYER PUM ANNOTATION: PUMANN
    # DOC LAYER AU ANNOTATION: AUANN
    # DOC LAYER ED ANNOTATION: EDANN
    #
    # TO ADMIN EMAIL: TOADE
    # FROM ADMIN EMAIL: FRADE
    # ED TO AUT EMAIL: ETOAE
    # AUT TO ED EMAIL: ATOEE
    # TO PM EMAIL: TOPME
    # TO PUM EMAIL: TOPUM
    # FROM PM EMAIL: FRPME
    # FROM PUM EMAIL: FRPUM
    # TO DIR EMAIL: TODIE
    # FROM DIR EMAIL: FRDIE
    # TO ASSISTDIR EMAIL: TOASDIE
    # FROM ASSISTDIR EMAIL: FRASDIE
    # FROM SUD EMAIL: FRSUD
    # TO SUD EMAIL: TOSUD
    # FROM MOB EMAIL: FRMOB
    # TO MOB EMAIL: TOMOB
    #
    # DOC LAYER REMINDER: REMIN
    #
    # First distintion for reminders
    # DOC LAYER EDITOR REMINDER: EDREM
    # DOC LAYER REFEREE REMINDER: REREM
    # DOC LAYER AUTHOR REMINDER: AUREM
    #
    # Second distintion for reminders
    # DOC LAYER EDITOR REFEREE REMINDER: EREMR
    # DOC LAYER EDITOR AUTHOR REMINDER : EREMA
    # DOC LAYER ADMIN EDITOR REMINDER: AREME
    # DOC LAYER ADMIN REFEREE REMINDER: AREMR
    # DOC LAYER ADMIN AUTHOR REMINDER: AREMA
    # DOC LAYER PM AUTHOR REMINDER: PREMA
    # DOC LAYER PM TYPESETTER REMINDER: PREMT

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
        # FRPME: e.g. JCOM_002A_1216, JCOM_016A_0418
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
            "TOASDIE",
            "TODIE",
        ]

        # PUMANN can have no authors: JCOM_002N_0724
        self.types_with_auth_OR_recipient = [
            "PUMANN",
            "FRPUM",
        ]

        self.managed_types_list = (
            self.types_with_auth_and_recipient
            + self.types_with_auth_OR_recipient
            + self.types_from_admin_list
            + self.types_to_admin_list
            + self.types_skipped
        )

        for m in self.read_all_messages_of_imported_version():
            # as wjs msg.recipient added from wjapp: recipient, reader, readerCC
            # readerBCC are excluded by the query on wjapp
            (author_from_wjapp, message_recipients_no_bcc) = self.read_message_author_recipients(m["documentLayerCod"])

            if (
                author_from_wjapp
                and message_recipients_no_bcc
                and (m["documentLayerType"] in self.types_with_auth_and_recipient)
            ):
                # the messages "Referee confirms assignment for" has to appear as system messages in wjs
                if m["documentLayerType"] == "EMAIL" and any(
                    (m["documentLayerSubject"] or "").startswith(sub)
                    for sub in [
                        "Referee confirms assignment for",
                        "Author selects coauthor for document",
                        "Referee declines assignment for",
                    ]
                ):
                    message_type = Message.MessageTypes.SYSTEM
                else:
                    message_type = Message.MessageTypes.USER
            elif m["documentLayerType"] in ["TPSAN", "PMANN", "AUANN", "AREMA", "AREME", "AREMR", "PREMA", "PREMT"]:
                message_type = Message.MessageTypes.SYSTEM
            else:
                message_type = Message.MessageTypes.USER

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

            if m["documentLayerType"] in self.types_from_admin_list:
                # author set directly, not read from wjapp
                author = get_eo_user(self.journal)
            elif not author_from_wjapp and (m["documentLayerType"] in self.types_with_auth_OR_recipient):
                # case like JCOM_002N_0724
                author = get_eo_user(self.journal)
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
                    author_from_wjapp[0]["privacy"],
                    self.connection,
                )

            document_layer_subject = (
                m["documentLayerSubject"]
                if m["documentLayerSubject"]
                else truncate_with_ellipsis(m["documentLayerText"])
            )
            with freezegun.freeze_time(
                rome_timezone.localize(m["submissionDate"]),
            ):
                msg = Message.objects.create(
                    actor=author,
                    subject=document_layer_subject,
                    body=newlines_text_to_html(m["documentLayerText"]),
                    content_type=ContentType.objects.get_for_model(self.article),
                    object_id=self.article.id,
                    message_type=message_type,
                )
                logger.debug(f"msg {m['documentLayerCod']} imported: {document_layer_subject}")

                # recipient management

                # if not message_recipients_no_bcc or "recipient" not in message_recipients_no_bcc.values():
                if m["documentLayerType"] in self.types_to_admin_list:
                    msg.recipients.add(get_eo_user(self.journal))
                    logger.debug(f"msg {m['documentLayerCod']} add recipient eo_user {document_layer_subject}")

                for msg_rec in message_recipients_no_bcc:
                    recipient = account_get_or_create_check_correspondence(
                        self.journal.code.lower(),
                        msg_rec["userCod"],
                        msg_rec["lastname"],
                        msg_rec["firstname"],
                        msg_rec["email"],
                        msg_rec["privacy"],
                        self.connection,
                    )
                    if recipient not in msg.recipients.all():
                        msg.recipients.add(recipient)

                # management of special cases
                # TBV: correct management of this type?
                if not msg.recipients.exists() and m["documentLayerType"] in ("FRASDIE", "FRDIE", "TOPUM"):
                    msg.recipients.add(get_eo_user(self.journal))

                if not msg.recipients.exists() and m["documentLayerType"] in self.types_with_auth_OR_recipient:
                    msg.recipients.add(get_eo_user(self.journal))

                special_cases_no_recipients = (255874, 253593, 254104, 253252, 253253, 249057, 250742, 251797)
                if m["documentLayerCod"] in special_cases_no_recipients:
                    msg.recipients.add(get_eo_user(self.journal))
                    logger.debug(f"add recipient eo user for special case {m['documentLayerCod']}")

                # error if no recipients at all from wjapp and not added eo_user
                if not msg.recipients.all():
                    raise RuntimeError(
                        f"msg {m['documentLayerCod']} without recipients: {self.article.id}"
                        f" {document_layer_subject} {m['documentLayerType']} {message_recipients_no_bcc=}"
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
        #
        # 'AUANN' is the author annotation, i.e. the corrections sent by the author to the typesetter.
        # 'AUANN' text is contained in Document_Layer (documentLayerText), but can be associated
        # to a file (zip, pdf, jpg, ...), which is saved in the wjapp archive in
        # "preprintid/version/AUANN/documentLayerID/documentLayerID.file_extension".
        # The file extension is not saved in the wjapp database.
        #
        # E.g. in JCOM_017A_0624/4/
        #
        # AUANN/JCOM_017A_0624_AUANN004381124/JCOM_017A_0624_AUANN004381124.zip
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
AND dl.documentLayerType NOT IN ('REREP', 'EDREP', 'CVLETT', 'AUANN')
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
                logger.debug(
                    f"msg already imported: {m['documentLayerCod']} {m['documentLayerType']} "
                    f"{m['documentLayerSubject']}"
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
u.email,
u.privacy
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
    publicationid: str
    document_revision_dead_line: datetime
    article: submission_models.Article
    imported_version_num: int
    imported_version_cod: int
    imported_version_state_cod: int
    importfiles: bool
    imported_document_layer_cod_list: list
    action_triggers_import_files: bool
    """flag used when not all actions of a family have to import files"""
    imported_doclayer_check_visibility: dict
    url_base: str  # = getattr(settings, "WJAPP_JCOM_BASE_URL", None)

    def run(self):
        raise NotImplementedError

    def get_current_editor(self):
        return WjsEditorAssignment.objects.get_current(self.article).editor

    def check_editor_set(self):
        if not self.get_current_editor():
            logger.error(f"editor not set for {self.preprintid} {self.article.id}")
            self.connection.close()
            raise Exception

    def import_files(self, production_version=False):
        """Downloads and save files for imported version."""

        # TBV: action admin resets editor will be skip in the import. New version is created when
        #     the editor is assigned. Verify that the import of the files is coerent in wjapp
        # TODO: import files is different for imported_version_state_cod published
        #       pubid.pdf instead of preprintid.pdf
        #
        # pdf published version pubid.pdf (no source): "PDF", "pdf",
        #   JCOM_003N_0623/8/JCOM_2305_2024_N03.pdf&fileType=pdf
        #
        # published version: download tar.gz loaded by typesetter from previous version
        #       JCOM_003N_0623/7/submission/JCOM_003N_0623.tar.gz
        # TODO: also download and add (to be decided how) to the tar.gz from current_hidden
        #       JCOM_003N_0623.tex which has the placeholders replaced
        #
        # download preprintid.docx, preprintid.pdf for not published version: "ZIP", "zip"
        #   JCOM_003N_0623/7/JCOM_003N_0623.docx&fileType=docx
        #   JCOM_003N_0623/7/JCOM_003N_0623.pdf&fileType=pdf
        #
        #   Note: to not loose other files like Figure1.docx submitted by the author
        #         imported also as source file produduction/JCOM_003N_0623.zip which
        #         contains submission/
        #
        # attachments read data from db for each attachment (no source file name)
        #   JCOM_003N_0623/1/attachments/JCOM_011A_0623_ATTACH00060623.pdf&fileType=Table

        # wjapp version state 22 is the published version
        if self.imported_version_state_cod == 22:
            # we don't want to import files for the published version because the final
            # galleys and supplementary are already imported when the published paper
            # has been imported
            return

        elif production_version:
            logger.debug(f"production version: {production_version}")
            response_pdf_galley_prod = self.download_pdf_galley_prod()

            if response_pdf_galley_prod.headers["Content-Length"] != "0":
                pdf_galley_prod_dj = file_from_response(response_pdf_galley_prod, f"{self.preprintid}.pdf")
                self.save_pdf_galley(pdf_galley_prod_dj)

        else:
            known_missing_manuscript = {("JCOM_001E_0422", 1), ("JCOM_002A_0717", 1), ("JCOM_018A_0923", 1)}
            if (self.preprintid, self.imported_version_num) in known_missing_manuscript:
                logger.debug(f"{self.preprintid} / {self.imported_version_num} (missing manuscript - known case)")
            else:
                response_manuscript = self.download_manuscript()
                if response_manuscript.headers["Content-Length"] != "0":
                    manuscript_dj = file_from_response(response_manuscript, f"{self.preprintid}.pdf")
                    self.save_manuscript(manuscript_dj)

            # remove previous source files relations already saved as historical files
            # necessary to clear() when the version import files starts because
            # there are two files preprintid.docx and submit.zip
            self.article.source_files.clear()

            (response_source, doc_type) = self.download_source()
            if response_source.headers["Content-Length"] != "0":
                source_dj = file_from_response(response_source, f"{self.preprintid}.{doc_type}")
                self.save_source(source_dj, doc_type)

            # we want to download all the source files sent by the author,
            # e.g. JCOM_008A_0125: Figure1.docx, Figure2.docx ...
            (response_source_compressed, doc_type_compressed) = self.download_source_compressed_archive()
            if response_source_compressed.headers["Content-Length"] != "0":
                # returns a zip "submit.zip" containing only the  subdirectory "submission/" of the production zip
                # with the originals files sent by the author
                submit_source_compressed_dj = self.extract_subdirectory_to_zip(
                    response_source_compressed, f"{self.preprintid}/submission/", f"submit.{doc_type_compressed}"
                )
                self.save_source(submit_source_compressed_dj, doc_type_compressed)

        # read attachments data from wjapp and save each esm with the same format, pdf, zip, ...
        # the original name of the attachment file is not imported but it is not relevant

        if not production_version:
            self.article.data_figure_files.clear()
        wjapp_attachments = self.read_attachments_data()
        wjapp_prod_attachments = []
        for dff_data in wjapp_attachments:
            dff_response = self.download_wjapp_attachment(dff_data)
            if dff_response.headers["Content-Length"] != "0":
                dff_dj = file_from_response(dff_response, f"{dff_data['attachID']}.{dff_data['attachFormat']}")
                if production_version:
                    ta_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(
                        only_completed=False,
                    )
                    dff_file = files.save_file_to_article(
                        dff_dj,
                        self.article,
                        ta_assignment.typesetter,
                    )
                    dff_file.label = dff_data["attachType"]
                    dff_file.description = f"{dff_data['attachTitle']} {dff_data['attachDescription']}"
                    dff_file.save()
                    # for a production version, each attachment is as SF in a list
                    wjapp_prod_attachments.append(SupplementaryFile.objects.create(file=dff_file))
                else:
                    # in review version each attachments is saved as DFF
                    self.save_data_figure_file(dff_dj, dff_data)

        # if the production version is not of a published paper
        # the prepared list is saved as SF list
        if production_version and not self.publicationid and wjapp_prod_attachments:
            self.article.supplementary_files.set(wjapp_prod_attachments)

    def extract_subdirectory_to_zip(self, response, subdirectory, name):
        """return a DF zip containing only the subdirectory"""

        memory_zip = BytesIO()

        with zipfile.ZipFile(BytesIO(response.content), "r") as zip_ref:
            # Create a new ZIP file in memory
            with zipfile.ZipFile(memory_zip, "w") as new_zip:
                # List all files in the zip file
                for file_info in zip_ref.infolist():
                    # Check if the file is in the specified subdirectory i.e. JCOM_002N_1122/submission/
                    logger.debug(f"file name: {file_info} {subdirectory}")
                    if file_info.filename.startswith(subdirectory):
                        try:
                            # Read the file into memory
                            with zip_ref.open(file_info.filename) as file:
                                # Read the content safely
                                content = file.read()

                                # Write the file to the new ZIP archive in memory
                                new_zip.writestr(file_info.filename, content)

                        except Exception as e:
                            logger.error(f"Error processing file {file_info.filename}: {e}")

        return DjangoFile(memory_zip, name)

    # manuscript
    def download_manuscript(self):
        """Download pdf manuscript for imported version."""

        file_url = f"{self.url_base}{self.preprintid}/{self.imported_version_num}/{self.preprintid}.pdf&fileType=pdf"
        logger.debug(f"{file_url=}")
        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(
                f"check wjapp login credentials empty file pdf downloaded: {response.headers['Content-Length']}"
            )
        return response

    # production version files

    def download_pdf_galley_prod(self):
        """Download pdf galley for production imported version."""

        file_url = f"{self.url_base}{self.preprintid}/{self.imported_version_num}/{self.preprintid}.pdf&fileType=pdf"

        if self.preprintid == "JCOM_003A_0615" and self.imported_version_num == 5:
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}"
                f"/publication/currentHidden/{self.preprintid}.pdf&fileType=pdf"
            )
            logger.warning(
                f"used currentHidden/JCOM_003A_0615.pdf for {self.preprintid}/"
                f"{self.imported_version_num} (fix file name - known case)."
            )

        logger.debug(f"{file_url=}")
        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(
                f"check wjapp login credentials empty pdf galley downloaded: {response.headers['Content-Length']}"
            )
        return response

    def save_pdf_galley(self, pdf_galley_dj):
        """Save PDF production version in TA.galleys_created"""

        assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)

        # necessary to avoid errors until action "back to typesetter"
        # is implemented, if action TYP_UPLOADS_FOR_PM happens twice like
        # in JCOM_017A_0624
        assignment.galleys_created.clear()

        assert not assignment.galleys_created.exists(), (
            f"We have {assignment.galleys_created.count()} galleys on the TA "
            f"for round {assignment.round.round_number}. Expected none!"
        )

        pdf_galley_file = files.save_file_to_article(
            pdf_galley_dj,
            self.article,
            assignment.typesetter,
        )
        pdf_galley = Galley.objects.create(
            file=pdf_galley_file, label="PDF", type="pdf", article=self.article, public=False
        )
        assignment.galleys_created.add(pdf_galley)

        assignment.round.article.articleworkflow.save()

        return

    # production version source TARGZ
    def file_source_prod(self):

        # typesetter uploaded a docx replaced with empty tar.gz
        missing_cases = {
            ("JCOM_004C_1020", 3),
        }
        if (self.preprintid, self.imported_version_num) in missing_cases:
            logger.warning(f"for {self.preprintid}/{self.imported_version_num} - docx source prod tar.gz known case")
            empty_source_prod_dj = DjangoFile(BytesIO(b""), f"{self.preprintid}.tar.gz")
            return empty_source_prod_dj

        (response_source_prod, file_type) = self.download_source_prod()
        if response_source_prod.headers["Content-Length"] != "0":
            if file_type == "tar.gz":
                source_prod_dj = file_from_response(response_source_prod, f"{self.preprintid}.{file_type}")
            elif file_type == "tex":
                source_prod_dj = build_targz_archive_from_tex_response(response_source_prod, f"{self.preprintid}")
        return source_prod_dj

    def download_source_prod(self):
        """Download tar gz source from production imported version."""

        file_url = (
            f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
            f"submission/{self.preprintid}.tar.gz&fileType=gz"
        )

        # preprintid of file different from the preprintid of article due to
        # wjapp maintenance change of article type or other type of manual maintenance

        if self.preprintid == "JCOM_003C_0622" and self.imported_version_num in (2, 3):
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/JCOM_002E_0622.tar.gz&fileType=gz"
            )
            logger.warning(
                f"used JCOM_002E_0622.tar.gz for {self.preprintid}/"
                f"{self.imported_version_num} (change preprint type - known case)."
            )
        if self.preprintid == "JCOM_001C_0922" and self.imported_version_num == 3:
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/JCOM_001E_0922.tar.gz&fileType=gz"
            )
            logger.warning(
                f"used JCOM_001E_0922.tar.gz for {self.preprintid}/"
                f"{self.imported_version_num} (change preprint type - known case)."
            )
        if self.preprintid == "JCOM_004N_0918" and self.imported_version_num in (3, 4):
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/JCOM_001C_0918.tar.gz&fileType=gz"
            )
            logger.warning(
                f"used JCOM_001C_0918.tar.gz for {self.preprintid}/"
                f"{self.imported_version_num} (change preprint type - known case)."
            )

        if self.preprintid == "JCOM_005A_1116" and self.imported_version_num == 3:
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/JCOM_002A_1216.tar.gz&fileType=gz"
            )
            logger.warning(
                f"used JCOM_002A_1216.tar.gz for {self.preprintid}/"
                f"{self.imported_version_num} (change preprint type - known case)."
            )

        if self.preprintid == "JCOM_013A_0215" and self.imported_version_num == 3:
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/JCOM_1402_2015_A-king-proof.tar.gz&fileType=gz"
            )
            logger.warning(
                f"used JCOM_1402_2015_A-king-proof.tar.gz for {self.preprintid}/"
                f"{self.imported_version_num} (change preprint type - known case)."
            )

        if self.preprintid == "JCOM_008A_0215" and self.imported_version_num == 4:
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/JCOM_402_2015_A03.tar.gz&fileType=gz"
            )
            logger.warning(
                f"used JCOM_402_2015_A03.tar.gz for {self.preprintid}/" f"{self.imported_version_num} (- known case)."
            )

        special_cases = {
            ("JCOMAL_003A_0520", 4): "JCOMAL_001N_0520.tar.gz",
            ("JCOMAL_003A_0520", 5): "JCOMAL_001N_0520.tar.gz",
            ("JCOMAL_001A_1118", 3): "JCOMAL-Orozco.v2.tar.gz",
            ("JCOMAL_001A_1118", 4): "JCOMAL-Orozco.v3.tar.gz",
            ("JCOMAL_002A_1118", 3): "JCOMAL-Marandino_et_al.v2.1.tar.gz",
            ("JCOMAL_002A_1118", 4): "JCOMAL-Marandino_et_al.v3.tar.gz",
            ("JCOMAL_003A_1118", 4): "JCOMAL_003A_1118-proof.tar.gz",
            ("JCOMAL_003A_1118", 5): "JCOMAL-Da_Silva_Lima_Moschem.v3.tar.gz",
            ("JCOMAL_004A_1118", 4): "JCOMAL_003A_1118-proof.tar.gz",
            ("JCOMAL_004A_1118", 5): "JCOMAL-Costa-v3.tar.gz",
            ("JCOMAL_001Y_1118", 2): "JCOMAL_001Y_1118-proof.tar.gz",
            ("JCOMAL_001Y_1118", 3): "JCOMAL-CastilhosAlmeida-v3.tar.gz",
            ("JCOMAL_002Y_1118", 3): "JCOMAL-Cortassa.v2.tar.gz",
            ("JCOMAL_002Y_1118", 4): "JCOMAL-Cortassa.v3.tar.gz",
            ("JCOMAL_001R_1118", 2): "JCOMAL-Poenaru.v2.tar.gz",
            ("JCOMAL_005A_1118", 3): "JCOMAL-NegreteRosenblatt.v2.tar.gz",
            ("JCOMAL_006A_1118", 4): "JCOMAL_006A_1118-proof.tar.gz",
            ("JCOMAL_006A_1118", 5): "JCOMAL-Lima.v3.tar.gz",
            ("JCOMAL_004A_0320", 4): "JCOMAL_001N_0320.tar.gz",
            ("JCOMAL_004A_0320", 5): "JCOMAL_001N_0320.tar.gz",
            ("JCOMAL_001A_1018", 4): "JCOMAL_001A_1018-proof.tar.gz",
            ("JCOMAL_001A_1018", 5): "JCOMAL-Massarani-v4.tar.gz",
        }
        if (self.preprintid, self.imported_version_num) in special_cases.keys():
            fixed_filename = special_cases[(self.preprintid, self.imported_version_num)]
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/{fixed_filename}&fileType=gz"
            )
            logger.warning(
                f"used {fixed_filename} for {self.preprintid}/" f"{self.imported_version_num} (- known case)."
            )

        file_type = "tar.gz"
        logger.debug(f"production source: {file_url=}")
        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.warning(
                f"production submission tar.gz empty file: {self.article.id} / {self.preprintid} try tex format"
            )
            file_url = (
                f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
                f"submission/{self.preprintid}.tex&fileType=tex"
            )
            response = self.session.get(file_url)
            assert response.status_code == 200, f"Got {response.status_code}!"
            file_type = "tex"
            logger.debug(f"production source: {file_url=}")
            if response.headers["Content-Length"] == "0":
                logger.error(
                    f"production submission: also tex empty file downloaded: {self.article.id} / {self.preprintid}"
                )

        return (response, file_type)

    # review files

    def download_source(self):
        """Download docx/doc source for imported version."""

        doc_type = "docx"
        url_first_part = f"{self.url_base}{self.preprintid}/{self.imported_version_num}/{self.preprintid}"
        file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"

        special_cases = {("JCOMAL_001A_1218", 1)}
        if (self.preprintid, self.imported_version_num) in special_cases:
            url_first_part = f"{self.url_base}{self.preprintid}/{self.imported_version_num}/submission/00-art_div_vf"
            file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"
            logger.warning(
                f"used 00-art_div_vf.docx for {self.preprintid}/"
                f"{self.imported_version_num} (data problem on wjapp- known case)."
            )

        logger.debug(f"{file_url=}")
        response = self.session.get(file_url)

        if response.status_code == 200:
            if response.headers["Content-Length"] == "0":
                logger.debug(
                    f"check wjapp login data empty file {doc_type} downloaded: {response.headers['Content-Length']}"
                )
            else:
                return (response, doc_type)
        else:
            logger.error(f"With docx got {response.status_code}!")

        # try with doc instead of docx
        doc_type = "doc"
        file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"
        logger.debug(f"retry with {doc_type} {file_url=}")
        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"

        if response.headers["Content-Length"] == "0":
            logger.error(
                f"check wjapp login credentials empty file {doc_type} downloaded: {response.headers['Content-Length']}"
            )

        return (response, doc_type)

    def regenerate_production_archives(self):
        """Necessary to regenerate the production archives."""

        # Before to download the production zip it could be necessary to regenerate it.
        # It is done doing a request to the preprint wjapp "All versions" page
        # without to read the response, e.g. url:
        # https://jcom.sissa.it/jcom/admin/docPage.jsp?docPgType=versions&docId=JCOM_001A_0823
        #
        # To read the response is not important, but wjapp server seems randomly
        # to "chunck" it, therefore has been except ChunkedEncodingError and only logged it as ERROR.
        # This exception does not block the regeneration of the production archives, which is the
        # reason of the request.

        # i.e. JCOM_014A_0524: broken wjapp version page repaired on wjapp side.
        # This problem is independent by the management of the exception ChunkedEncodingError

        file_url_versions_page = (
            f"{self.session.login_base_url}/admin/docPage.jsp?docPgType=versions&docId={self.preprintid}"
        )
        try:
            response = self.session.get(file_url_versions_page)
            assert response.status_code == 200, f"Got {response.status_code}!"
        except requests.exceptions.ChunkedEncodingError:
            # known cases: JCOM_003A_0724 JCOM_004N_1024 JCOM_008A_0125 JCOM_014A_0524
            logger.error(f"Exception: ChunkedEncodingError {file_url_versions_page}")

    def download_source_compressed_archive(self):
        """Download compressed zip with submission folder for imported version."""

        doc_type = "zip"
        url_first_part = f"{self.url_base}{self.preprintid}/{self.imported_version_num}/production/{self.preprintid}"
        file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"
        logger.debug(f"{file_url=}")
        response = self.session.get(file_url)

        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.warning(
                f"check data empty file {doc_type} downloaded: {response.headers['Content-Length']} try regeneration "
            )
            # the regeneration of the zip archive could be necessary also after the
            # first wjapp version, if for example the zip file is missing only in version 2
            # if the zip files have already been regenerated this if branch is not executed
            # in the next versions
            self.regenerate_production_archives()
            logger.debug(f"production archives regenerated during import of version {self.imported_version_num}")
            response = self.session.get(file_url)
            if response.headers["Content-Length"] == "0":
                logger.error(
                    f"check wjapp login data empty file {doc_type}downloaded: {response.headers['Content-Length']}"
                )

        return (response, doc_type)

    def save_manuscript(self, manuscript_dj):
        """Save PDF manuscript"""

        # remove previous files relations already saved as historical files
        # the manuscript has only the current files
        self.article.manuscript_files.clear()

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

    def save_source(self, source_dj, doc_type):
        """Save docx/doc as source file"""

        # there is more than one source file for version, therefore
        # they are not clear()

        source_file = files.save_file_to_article(
            source_dj,
            self.article,
            self.article.correspondence_author,
        )
        self.article.source_files.add(source_file)
        source_file.label = doc_type.upper()
        source_file.description = ""
        source_file.save()
        self.article.save()

        return

    # wjapp attachments - data figurs files
    def download_wjapp_attachment(self, dff_data):
        """Download one wjapp attachment for imported version."""

        # url ex: JCOM_001A_0524/2/attachments/JCOM_001A_0524_ATTACH00360924.docx&fileType=Attachment

        url_end_part = f"{dff_data['attachID']}.{dff_data['attachFormat']}&fileType={dff_data['attachType']}"
        file_url = f"{self.url_base}{self.preprintid}/{self.imported_version_num}/attachments/{url_end_part}"
        logger.debug(f"{file_url=}")
        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(f"check wjapp login credentials empty DFF downloaded: {response.headers['Content-Length']}")
        return response

    def save_data_figure_file(self, dff_dj, dff_data):
        """Save wjapp attachment as data figure file"""

        dff_file = files.save_file_to_article(
            dff_dj,
            self.article,
            self.article.correspondence_author,
        )
        self.article.data_figure_files.add(dff_file)
        dff_file.label = dff_data["attachType"]
        dff_file.description = f"{dff_data['attachTitle']} {dff_data['attachDescription']}"
        dff_file.save()
        self.article.save()

        return

    def read_attachments_data(self):
        """Read wjapp attachments data for the imported version."""

        cursor_attachments = self.connection.cursor(dictionary=True)
        query_attachments = """
SELECT
attachID,
attachTitle,
attachDescription,
attachType,
attachFormat,
submissionDate
FROM Attachment
WHERE
    versioncod=%(imported_version_cod)s
ORDER BY submissionDate
"""
        cursor_attachments.execute(
            query_attachments,
            {
                "imported_version_cod": self.imported_version_cod,
            },
        )
        attachments_data = cursor_attachments.fetchall()
        cursor_attachments.close()
        return attachments_data

    def read_reminder_data(self):
        """Read wjapp reminder data for the imported action.
        Wjapp reminders data are related to the single actHistCod"""

        cursor_reminder = self.connection.cursor(dictionary=True)
        query_reminder = """
SELECT
reminderCod,
reminderDate,
actHistCod,
performerCod,
availabilityChecker,
type,
enabled,
sent,
disableAllowedRoles
FROM Reminder
WHERE
    actHistCod=%(actHistCod)s
ORDER BY reminderDate
"""
        cursor_reminder.execute(
            query_reminder,
            {
                "actHistCod": self.action["actHistCod"],
            },
        )
        reminder_data = cursor_reminder.fetchall()
        cursor_reminder.close()
        return reminder_data

    def check_and_fix_action_reminder(self, wjapp_type, wjs_type, reminder_number):
        """Check if wjapp reminder for an action is present and fix due date if necessary.

        Wjapp permits to set 2 or 3 reminder, depending on the action type,
        but the single configuration of a wjapp system can use less reminders.
        For example, wjapp allows up to 3 "New_submission" reminders
        but the jcom cofiguration sets only 2 "New_submission" reminders,
        while the configuration of jhep sets 3 New_submission submission reminders.

        Therefore calling this funcion is used the maximum reminder_number to manage
        the reminder type independently from the configuration of the single instance
        (the loop iteration happens only if the reminder is found on wjapp).

        In any case the reminder date is changed only if the reminder is present for
        the specific action in wjapp and if the logic of wjs creates the reminder for
        the corresponding wjs action

        Associations in import among wjapp reminder and wjs reminder:

        wjapp reminder <-> wjs reminder

        - pending_minor_revision <-> AUMIR
        - pending_revision <-> AUMJR
        - EditorAssignment
            - New_submission <-> EDSR
            - ed_selected_by_ed <-> EDSR
            - ed_selected_by_EO <-> EDSR
            - major_revision <-> EDSR
            - minor_revision <-> EDSR
        - REF_SEND_REP
            - only_ref_sent_rep <-> EDMD
            - last_ref_sent_rep <-> EDMD
        - ReviewerDeclineAction
            - ref_declined_ed_decision  <-> EDMD
            - Ref_decline <-> EDSR
        - DeselectReviewerAction
            - ed_removed_ed_decision <-> EDMD
        - ED_ACT_AS_REF
            - i_will_review <-> REWR

        not imported:
            - Ref_removed -> it means that the editor is still waiting for reports
            - appeal_confirm_ed_revised
            - appeal_confirm_ed_confirm
            - appeal_reset_ed
            - pending_appeal
            - wait_copyright
        """

        # TBV: when wjs sets 3 reminder of type YYYY and wjapp has 2 corresponding reminder with fixed date,
        # YYYY3 due_date can be earlier than YYYY1 and YYY2.
        # Chosen to remove YYYY3. Better to shift it with some criteria?
        # i.e. JCOM_028A_0724
        # TODO: FIXME for JHEP: (e.g. YYYY3.due_date = YYYY2.due_date + 2days)
        # TODO: ATM the code ignores the case when we have more reminders in wjapp that in wjs
        #       this is because the configuration of JCOM does not allow it.

        wjapp_reminder_list = [
            r_wjapp for r_wjapp in self.read_reminder_data() if r_wjapp["type"].startswith(wjapp_type)
        ]
        if not wjapp_reminder_list:
            return

        wjs_reminder_list = []
        for r_wjs in Reminder.objects.filter(code__startswith=f"{wjs_type}"):
            if r_wjs.get_related_article() == self.article:
                wjs_reminder_list.append(r_wjs)
        if not wjs_reminder_list:
            return

        # This flag indicates if any reminder of the type under examination has been changed.
        # This is used to decide if we should delete reminders that exists in wjs but did not exist in wjapp.
        wjs_reminder_type_changed = False
        for i in range(1, reminder_number + 1):
            for reminder in wjs_reminder_list:
                if reminder.code == f"{wjs_type}{i}":
                    logger.debug(f"exists wjs reminder for {self.article.id}: {reminder} {reminder.date_due}")
                    wjs_reminder_match = False
                    for wjapp_reminder in wjapp_reminder_list:
                        if wjapp_reminder["type"] == f"{wjapp_type}_{i}":
                            logger.debug(
                                f"found wjapp reminder for {self.preprintid} action {self.action['actionID']} "
                                f" {self.action['actHistCod']} {wjapp_reminder['type']} "
                                f"{wjapp_reminder['reminderDate'].date()}"
                            )
                            if reminder.date_due != wjapp_reminder["reminderDate"].date():
                                logger.debug(
                                    f"reminder {reminder.code} due date changed from {reminder.date_due} "
                                    f"to {wjapp_reminder['reminderDate'].date()}"
                                )
                                reminder.date_due = wjapp_reminder["reminderDate"].date()
                                reminder.save()
                                wjs_reminder_type_changed = True
                                if reminder.code == "REWR1":
                                    wra = WorkflowReviewAssignment.objects.get(pk=reminder.object_id)
                                    wra.date_due = wjapp_reminder["reminderDate"].date()
                                    wra.save()
                                    logger.warning(f"changed RA.date_due as REWR1: {wra.date_due} for RA {wra}")

                            else:
                                logger.debug(
                                    f"reminder {reminder.code} due date NOT changed because equal {reminder.date_due} "
                                    f"{wjapp_reminder['reminderDate'].date()}"
                                )
                            if wjapp_reminder["sent"]:
                                reminder.date_sent = wjapp_reminder["reminderDate"]
                                logger.debug(f"reminder date_sent added: {reminder}")
                                reminder.save()
                            if not wjapp_reminder["enabled"]:
                                reminder.disable = True
                                logger.warning(f"reminder disabled: {reminder}")
                                reminder.save()
                            wjs_reminder_match = True

                    if not wjs_reminder_match and wjs_reminder_type_changed:
                        reminder.delete()
                        logger.warning(
                            f"DELETED wjs reminder {self.article.id}: {reminder.code} {reminder.date_due} "
                            f"not found wjapp reminder: {wjapp_type}_{i} for {self.preprintid} action "
                            f"{self.action['actionID']} because changed wjs reminder {wjs_type}"
                        )
                    else:
                        logger.debug(
                            f"NOT deleted {not wjs_reminder_match=} {wjs_reminder_type_changed=} "
                            f"{reminder.code=} {wjapp_reminder['type']=} {wjapp_type=}"
                        )

    def check_and_fix_all_reminder(self):
        # this method has to be be overriden, if necessary to check specific reminders
        pass


@dataclass
class EditorAssignmentAction(BaseActionManager):
    """Manages editor assignment action."""

    def run(self):
        """Editor assignment management."""

        # these map (roughly) to EditorAssignment
        editor_cod = self.action["targetCod"]
        editor_lastname = self.action["targetLastname"]
        editor_firstname = self.action["targetFirstname"]
        editor_email = self.action["targetEmail"]
        editor_privacy = self.action["targetPrivacy"]
        editor_assign_date = self.action["actionDate"]
        editor_maxworkload = self.action["targetEditorWorkload"]

        # TO BE FIXED:
        #
        # JCOM_003Y_0821 (version 1 ed null), gives exception because the ADMIN_ASS_N_ED
        # (SupervisorChangeEditorAssignment) happens when the editor is not assigned

        # In wjapp the admin ADMIN_ASS_N_ED is done with two acions:
        # 1. "admin" resets editor "editor" for "preprintid" -> creates new version copying the files
        # 2. "admin selects editor "new editor" for "preprintid"
        #
        # This case in the import does not generate a new version in wjs, only a
        # SupervisorChangeEditorAssignment for action 2
        #
        # there are wjapp actions SYS_ASS_ED with editor assigned None
        # example: JCOM_003A_0424 version 2

        # TO BE VERIFIED: JCOM_001E_0318 - only 1 strange case in jcom of reset editor with editor directly assigned
        if editor_cod:
            # attribute editor added
            self.set_editor(
                editor_cod,
                editor_lastname,
                editor_firstname,
                editor_email,
                editor_assign_date,
                editor_privacy,
            )

            # added attribute editor parameters
            editor_parameters = self.read_editor_parameters(editor_cod)
            self.set_editor_parameters(editor_parameters, editor_maxworkload)

            # TO BE FIXED: if in the first version the SYS_ASS_ED does not assign the editor
            # the files are not imported (i.e.: JCOM_003Y_0821).
            #
            # TO BE FIXED: general problem with wjapp reset editor (new version): in this case we should
            # import all the files of the new version, not of the base version, because the new version can have
            # for example more attachments of the base version,or the files of the new version could have been
            # replaced with a maintenance operation.
            if self.action_triggers_import_files and self.importfiles:
                self.import_files()

    # TODO: check why new review_round is not created
    def set_editor(
        self, editor_cod, editor_lastname, editor_firstname, editor_email, editor_assign_date, editor_privacy
    ):
        """Assign the editor.

        Also create the editor's Account if necessary.
        """
        self.editor_to_assign = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            editor_cod,
            editor_lastname,
            editor_firstname,
            editor_email,
            editor_privacy,
            self.connection,
        )

        # An account must have the "section-editor" role on the journal to be able to be assigned as editor of an
        # article.
        if not self.editor_to_assign.check_role(self.journal, "section-editor", staff_override=False):
            self.editor_to_assign.add_account_role("section-editor", self.journal)

        logger.debug(
            f"Assigning {self.editor_to_assign.last_name} {self.editor_to_assign.first_name} onto {self.article.pk}"
        )

        self.ed_assign_request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        GlobalRequestMiddleware.process_request(self.ed_assign_request)

        with freezegun.freeze_time(
            rome_timezone.localize(editor_assign_date),
        ):
            self.run_business_logic()

        self.article.refresh_from_db()
        return self.editor_to_assign

    def run_business_logic(self):
        """Default editor-assignment logic.

        This method can be overridden by derived classes to tweak the assignment operation.
        """

        # Manually move into a state where editor assignment can take place
        # TODO: check if this is not the case already...
        self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        self.article.articleworkflow.save()

        AssignToEditor(
            article=self.article,
            editor=self.editor_to_assign,
            request=self.ed_assign_request,
        ).run()
        self.article.save()
        self.check_and_fix_all_reminder()

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

        - max-workload (StaffWorkloadParameters workload)
        - keyword      (EditorKeyword into StaffWorkloadParameters keywords)
        - kwd weight   (EditorKeyword weight)
        """

        if editor_parameters:
            assignment_parameters, eap_created = wjs_models.StaffWorkloadParameters.objects.get_or_create(
                user=self.get_current_editor(),
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
        wjs_models.StaffKeyword.objects.filter(parameters=assignment_parameters).delete()

        # create all new editor kwds
        for ep in editor_parameters:
            kwd_word = ep["keywordName"]
            # in wjapp-JCOMAL, the keyword string contains all three
            # languages separated by ";". The first is English.
            if self.journal.code.upper() == "JCOMAL":
                kwd_word = kwd_word.split(";")[0].strip()
            kwd_weight = ep["keywordWeight"]
            logger.debug(f"Editor parameter: {kwd_word} {kwd_weight}")
            keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_word)
            if created:
                logger.warning(f'Created keyword "{kwd_word}" for editor {self.get_current_editor()}. Please check!')
            wjs_models.StaffKeyword.objects.create(
                parameters=assignment_parameters,
                keyword=keyword,
                weight=kwd_weight,
            )

        return

    def deselect_editor_as_reviewer(self):
        """Deselects old editor from reviewer if exist."""

        # otherwise failes in case first submission not assigned to editor
        # i.e.: JCOM_001CR_0821
        current_editor_review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=self.get_current_editor(),
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()

        if current_editor_review_assignment:
            request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
            request.user = self.get_current_editor()

            # the automated message is disabled
            DeselectReviewer._log_operation = noop
            DeselectReviewer(
                assignment=current_editor_review_assignment,
                actor=current_editor_review_assignment.editor,
                request=request,
                send_reviewer_notification=False,
                form_data={
                    "notification_subject": "",
                    "notification_body": "",
                },
            ).run()
            current_editor_review_assignment.date_accepted = None
            current_editor_review_assignment.date_declined = None
            current_editor_review_assignment.save()


@dataclass
class SYS_ASS_ED(EditorAssignmentAction):  # noqa N801
    """Manages action "system assigns to editor"."""

    # this action in wjapp has two different meaning.
    # In the first version the agent is the author and the target is the editor which can be defined or not
    # in this case we want to add the author to all authors

    # In next versions the same action has been "reused" after a reset editor by admin action which is done by
    # an administrator and generates a new version copying the same files.
    # In this case the agent is the admin and the target is null, e.g. JCOM_010A_1123
    # and we do not want to add the agent (admin) to all authors.

    def __post_init__(self):
        """Enables attribute to import files for this action"""
        self.action_triggers_import_files = True

        if self.imported_version_num == 1:
            author = account_get_or_create_check_correspondence(
                self.journal.code.lower(),
                self.action["agentCod"],
                self.action["agentLastname"],
                self.action["agentFirstname"],
                self.action["agentEmail"],
                self.action["agentPrivacy"],
                self.connection,
            )

            # The action SYS_ASS_ED in wjapp "version 1" happens when the author submits the contribution.
            # It has always the agentCod (the author) also if sometimes the target can be null (editor not found).
            #
            # This is the first point where can be added to the authors list the author of the first submission
            # of the contribution (version 1) when the agent is not the corresponding author at
            # the moment of the import.
            #
            # The problem has to be managed only for the author of the version 1 because the other coauthors are
            # added with the actions SelectCoauthorAction which are already managed
            #
            # example of SYS_ASS_ED used after reset editor but not in version 1
            # where the agent is not an author but an eo in charge and then must not
            # be added to all authors: JCOM_028A_1024

            if author != self.article.correspondence_author:
                order = len(self.article.authors.all()) + 1

                submission_models.ArticleAuthorOrder.objects.create(
                    article=self.article,
                    author=author,
                    order=order,
                )
                if not author.check_role(self.journal, "author", staff_override=False):
                    author.add_account_role("author", self.journal)
                self.article.authors.add(author)
                self.article.save()

            with freezegun.freeze_time(
                self.article.date_submitted,
            ):
                request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
                request.user = author
                context = {
                    "article": self.article,
                    "request": request,
                }
                message_subject = render_template_from_setting(
                    setting_group_name="email_subject",
                    setting_name="subject_submission_acknowledgement",
                    journal=self.journal,
                    request=request,
                    context=context,
                    template_is_setting=True,
                )
                communication_utils.log_operation(
                    article=self.article,
                    message_subject=message_subject,
                    message_body="",
                    recipients=[author],
                    flag_as_read=True,
                    flag_as_read_by_eo=True,
                )

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for SYS_ASS_ED if exists in wjs"""

        self.check_and_fix_action_reminder("New_submission", "EDSR", 3)


class ED_SEL_N_ED(EditorAssignmentAction):  # noqa N801
    """Manages action "editor selects new editor"."""

    def run_business_logic(self):
        """Editor selects new editor."""

        self.deselect_editor_as_reviewer()

        # e.g. JCOM_010A_1123
        SupervisorChangeEditorAssignment._log_past_editor = noop
        AssignToEditor._log_operation = noop
        SupervisorChangeEditorAssignment(
            article=self.article,
            assignment=WjsEditorAssignment.objects.filter(article=self.article).latest(),
            new_editor=self.editor_to_assign,
            request=self.ed_assign_request,
        ).run()
        self.article.save()
        self.check_and_fix_all_reminder()

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminder for ED_SEL_N_ED if exists in wjs"""

        self.check_and_fix_action_reminder("ed_selected_by_ed", "EDSR", 3)


class ADMIN_ASS_N_ED(EditorAssignmentAction):  # noqa N801
    """Manages action "admin assigns new editor"."""

    def run_business_logic(self):
        """Admin selects new editor."""

        # TODO: problem with paper JCOM_2205_2023_A01 JCOM_005A_0523

        # TBV: problem with JCOM_008A_0125 reset editor after decision
        #       possible solution: create new review round
        # code taken from:
        # wjs/plugins/wjs_review/events/handlers.py -> restart_review_process_after_revision_submission
        #
        # Note:
        # the reset ed ADMIN_RESETS_ED happens in wjapp JCOM_008A_0125/1 history,
        # but the current action ADMIN_ASS_N_ED happens in JCOM_008A_0125/2 history.
        # In the normal paper import ADMIN_ASS_N_ED does not create in wjs a new version,
        # but for this case, decision already taken, it is necessary to create a new review
        # round before SupervisorChangeEditorAssignment

        special_cases = {
            ("JCOM_008A_0125", 2, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
            ("JCOM_004N_1021", 2, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
        }
        if (self.preprintid, self.imported_version_num, self.article.articleworkflow.state) in special_cases:

            logger.warning(
                f"fix of broken wjapp data state not compatible with ADMIN_ASS_N_ED: {self.preprintid} "
                f"AW.state: {self.article.articleworkflow.state}"
            )

            # Solved adding a AuthorHandleRevision, so a new review round is created following the logic.
            # After the action the files are reimported, so wjs version 1 and 2 are equal.
            # A problem can be that in the paper results a major revision submission not existing
            # in wjapp, but it is like a "confirm version". The editorial office can add a note for this paper.
            request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
            request.user = self.article.correspondence_author

            revision_request = EditorRevisionRequest.objects.get(article=self.article)
            form_data = {"author_note": revision_request.author_note}
            service = AuthorHandleRevision(
                revision=revision_request,
                form_data=form_data,
                user=self.article.correspondence_author,
                request=request,
            )
            service.run()

            # do again import_files because there is a new review round
            if self.import_files:
                self.import_files()

        if not WjsEditorAssignment.objects.filter(article=self.article).exists():
            # i.e. JCOM_005A_0523 editor decline
            # i.e: JCOM_001CR_0821: case admin chooses editor after first submission without editor assingment

            # Manually move into a state where editor assignment can take place
            if self.article.articleworkflow.state != ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED:
                logger.warning(
                    f"Changing article {self.article.id}/{self.preprintid} state "
                    f"from {self.article.articleworkflow.state} to "
                    f"{ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED} during ADMIN_ASS_N_ED. Please check"
                )
                self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
            self.article.articleworkflow.save()

            AssignToEditor(
                article=self.article,
                editor=self.editor_to_assign,
                request=self.ed_assign_request,
            ).run()
            self.article.save()
            self.check_and_fix_all_reminder()
            logger.warning(
                f"{self.preprintid} {self.action['actionID']} admin assigns to "
                f"new editor {self.editor_to_assign} after the first submission without editor assignment "
                "or after editor declines",
            )

        else:
            # normal case, admin assigns to a new editor and exists an WjsEditorAssignment

            # special case: co-author chosen as editor
            special_cases = {
                ("JCOM_005C_0819", 2, 272888),
                ("JCOM_006C_0819", 2, 272894),
            }
            if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in special_cases:
                logger.warning(
                    f"fix for {self.preprintid}/{self.imported_version_num} "
                    f" removed author {self.editor_to_assign} (coauthor as editor known case)"
                )
                self.article.authors.remove(self.editor_to_assign)
                self.article.save()

            self.deselect_editor_as_reviewer()

            # e.g. JCOM_010A_1123
            SupervisorChangeEditorAssignment._log_past_editor = noop
            AssignToEditor._log_operation = noop
            SupervisorChangeEditorAssignment(
                article=self.article,
                assignment=WjsEditorAssignment.objects.filter(article=self.article).latest(),
                new_editor=self.editor_to_assign,
                request=self.ed_assign_request,
            ).run()
            self.article.save()
            self.check_and_fix_all_reminder()

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminder for ADMIN_ASS_N_ED if exists in wjs"""

        self.check_and_fix_action_reminder("ed_selected_by_EO", "EDSR", 3)


class AdminOpensAppealAction(BaseActionManager):  # noqa N801
    """Admin opens appeal: wjapp action "admin accepts appeal"."""

    def run(self):
        """Admin opens appeal."""
        self.check_editor_set()

        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_appeal_revision_days",
            self.journal,
        ).process_value()

        # we need to get the admin user who did the action and the open-appeal action is done by this user
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]
        admin_opens_appeal_date = self.action["actionDate"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = admin

        with freezegun.freeze_time(
            rome_timezone.localize(admin_opens_appeal_date),
        ):
            logger.debug(f"Admin {admin} opens appeal {self.article.pk}")

            # the related message is imported with the general correspondence
            # the automated message is disabled
            OpenAppeal._log_author = noop
            OpenAppeal(
                new_editor=self.get_current_editor(),
                article=self.article,
                request=request,
            ).run()


class ADMIN_ACC_APP(AdminOpensAppealAction):  # noqa N801
    """Manages wjapp action "admin accepts appeal"."""


class ADMIN_ACC_APP_NEW_ED(AdminOpensAppealAction):  # noqa N801
    """Manages wjapp action "admin accepts appeal with new editor"."""


class AU_WITHD_DOC(BaseActionManager):  # noqa N801
    """Author withdraws paper: wjapp action "author withdraws document"."""

    def run(self):
        # Author withdraws paper

        noop_cases = {
            ("JCOM_006A_0623", 1, 291617),
            ("JCOM_004N_0923", 2, 295231),
            ("JCOM_016A_0924", 1, 299629),
            ("JCOM_003N_1224", 1, 300878),
            ("JCOM_003N_1224", 1, 300879),
            ("JCOMAL_001R_1223", 1, 4664),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} AU_WITHD_DOC "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        self.check_editor_set()
        self.author_withdrawn(self.read_author_withdrawn_message())

    def author_withdrawn(self, author_withdrawn_message):
        """Author withdraws preprint."""

        author_cod = self.action["agentCod"]
        author_lastname = self.action["agentLastname"]
        author_firstname = self.action["agentFirstname"]
        author_email = self.action["agentEmail"]
        author_privacy = self.action["agentPrivacy"]
        author_withdrawn_date = self.action["actionDate"]

        # note: the "agentEmail" could be different from correspondence_author.email
        # because in general the agentEmail could be present in Correspondence but not
        # in the user account
        author = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            author_cod,
            author_lastname,
            author_firstname,
            author_email,
            author_privacy,
            self.connection,
        )

        # account_get_or_create_check_correspondence returns the cached value for the corr auth.
        # the assert assures that the wjapp data are consistent
        assert author.id == self.article.correspondence_author.id

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = author

        with freezegun.freeze_time(
            rome_timezone.localize(author_withdrawn_date),
        ):
            logger.debug(f"Author {author} withdraws {self.article}")

            # it is saved in TextField therefore not converted to html
            withdrawn_subject = author_withdrawn_message.get("documentLayerSubject")
            withdrawn_message = newlines_text_to_html(author_withdrawn_message.get("documentLayerText"))

            self.imported_document_layer_cod_list.append(author_withdrawn_message.get("documentLayerCod"))

            WithdrawPreprint(
                workflow=self.article.articleworkflow,
                request=request,
                form_data={
                    "notification_subject": withdrawn_subject,
                    "notification_body": withdrawn_message,
                },
            ).run()

    def read_author_withdrawn_message(self):
        """Read author withdraws message."""

        cursor_author_withdrawn_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (5") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        query_author_withdrawn_message = """
SELECT
dl.documentLayerSubject,
dl.documentLayerCod,
dl.documentLayerText,
dl.documentLayerType
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType IN ('ATOEE', 'TOADE')
AND ur.userType='author'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 11 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_author_withdrawn_message.execute(
            query_author_withdrawn_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_author_withdrawn_message.rowcount != 1:

            noop_cases = {
                ("JCOM_006A_0623", 57586, 12533),
                ("JCOM_004N_0923", 58000, 12876),
                ("JCOM_016A_0924", 58626, 12188),
                ("JCOM_003N_1224", 58899, 13787),
            }
            if (self.preprintid, self.imported_version_cod, self.action["agentCod"]) in noop_cases:
                logger.warning(
                    f"Skipped duplicated author withdrawn message for agent {self.action['agentCod']} "
                    f"and {self.preprintid} version cod: {self.imported_version_cod} (duplicated - known case)."
                )
                author_withdrawn_message = cursor_author_withdrawn_message.fetchone()
            else:
                logger.error(
                    f"Found {cursor_author_withdrawn_message.rowcount} author withdrawn messages"
                    f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
                )
                author_withdrawn_message = None
        else:
            author_withdrawn_message = cursor_author_withdrawn_message.fetchone()
            logger.debug(
                f"Found auth withdrawn msg doclayerType: {author_withdrawn_message.get('documentLayerType')} "
                f"{self.preprintid}/{self.imported_version_num}"
            )
        cursor_author_withdrawn_message.close()
        return author_withdrawn_message


class AdminWithdrawn(BaseActionManager):  # noqa N801
    """Admin withdraws paper."""

    def run(self):
        # Admin withdraws paper e.g. JCOM_005A_0224

        noop_cases = {("JCOM_001Y_0821")}
        if self.preprintid in noop_cases:
            logger.debug(f"missing editor for {self.preprintid} / {self.article.id} (known case)")
            self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        else:
            self.check_editor_set()

        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]
        admin_withdrawn_date = self.action["actionDate"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)

        # admin can not withdrawn in wjs
        request.user = self.article.correspondence_author

        with freezegun.freeze_time(
            rome_timezone.localize(admin_withdrawn_date),
        ):
            logger.debug(f"Admin {admin} withdraws {self.article}")

            # message imported as correspondence because this action is executed by the author, not the admin
            WithdrawPreprint._log_supervisor = noop
            WithdrawPreprint._log_typesetter = noop
            DeselectReviewer._log_operation = noop
            WithdrawPreprint(
                workflow=self.article.articleworkflow,
                request=request,
                form_data={
                    "notification_subject": "NOT IMPORTED",
                    "notification_body": "NOT IMPORTED",
                },
            ).run()

            communication_utils.log_operation(
                article=self.article,
                message_subject="Withdrawn",
                message_body="",
                actor=admin,
                recipients=[admin],
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )


class ADMIN_WITHD_DOC(AdminWithdrawn):  # noqa N801
    """Admin withdraws paper: wjapp action "admin withdraws document"."""


class ADMIN_WITHR_APP(AdminWithdrawn):  # noqa N801
    """Admin withdraws appeal: wjapp action "admin withdraw appeal"."""


class ED_REF_DOC(BaseActionManager):  # noqa N801
    """Editor declines assignment: wjapp action "editor refuses document"."""

    def run(self):
        # Editor declines assignment

        # Known cases of "dirty data" on wjapp, when this action had no effect.
        # We identify them by a triplet made of preprint id, version number and action-history code
        # (the first two are just human-friendly pointers 🙂).
        # related to a correction of double editor decline message
        noop_cases = {
            ("JCOM_009A_0123", 1, 290536),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ED_REF_DOC "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        self.check_editor_set()
        self.editor_declines(self.read_editor_decline_message())

    def editor_declines(self, editor_decline_message):
        """Editor declines."""

        editor_declines_date = self.action["actionDate"]
        editor = self.get_current_editor()

        # i.e. JCOM_012A_0920, a direct assert equal on agentLastname == editor.last_name
        # "Reynoso-Haynes" is different from "Reynoso Haynes"
        # (same person) is too strict it is necessary to check the accounts
        agent_cod = self.action["agentCod"]
        agent_lastname = self.action["agentLastname"]
        agent_firstname = self.action["agentFirstname"]
        agent_email = self.action["agentEmail"]
        agent_privacy = self.action["agentPrivacy"]

        agent = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            agent_cod,
            agent_lastname,
            agent_firstname,
            agent_email,
            agent_privacy,
            self.connection,
        )
        assert agent.id == editor.id

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = editor

        with freezegun.freeze_time(
            rome_timezone.localize(editor_declines_date),
        ):
            logger.debug(f"Editor {editor} declines {self.article.pk}")

            # it is saved in TextField therefore not converted to html
            decline_message = editor_decline_message.get("documentLayerText")

            self.imported_document_layer_cod_list.append(editor_decline_message.get("documentLayerCod"))

            HandleEditorDeclinesAssignment(
                assignment=WjsEditorAssignment.objects.get_all(self.article.articleworkflow).get(editor=editor),
                editor=editor,
                request=request,
                form_data={
                    "decline_reason": PastEditorAssignment.DeclineReasons.BUSY.label,
                    "decline_text": decline_message,
                },
            ).run()

    def read_editor_decline_message(self):
        """Read Editor decline assignment message."""

        cursor_editor_decline_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (5") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        query_editor_decline_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='TOADE'
AND ur.userType='author'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 5 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_008A_0622"
            and self.imported_version_cod == 56883
            and self.action["agentCod"] == 8502
        ):
            action_date = "20220623140311"
            logger.debug(f"fixed editor decline msg timing {self.preprintid}")

        cursor_editor_decline_message.execute(
            query_editor_decline_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
            },
        )
        if cursor_editor_decline_message.rowcount != 1:
            noop_cases = {
                ("JCOM_009A_0123", 57327, 8475),
            }
            if (self.preprintid, self.imported_version_cod, self.action["agentCod"]) in noop_cases:
                logger.warning(
                    f"Skipped duplicated editor decline message for agent {self.action['agentCod']} "
                    f"and {self.preprintid} version cod: {self.imported_version_cod} (duplicated - known case)."
                )
                editor_decline_message = cursor_editor_decline_message.fetchone()
            else:
                logger.error(
                    f"Found {cursor_editor_decline_message.rowcount} editor decline messages"
                    f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
                )
                editor_decline_message = None
        else:
            editor_decline_message = cursor_editor_decline_message.fetchone()
        cursor_editor_decline_message.close()
        return editor_decline_message


class ED_ACT_AS_REF(BaseActionManager):  # noqa N801
    """Editor assigns her/him self as reviewer "editor acts as referee"."""

    def run(self):

        # note:
        # - JCOM_009A_0123/2
        #   has two immediately successive actions: i will review
        #   - 08 May 2023 07:31 Editor Elaine Reynoso-Haynes will review JCOM_009A_0123 by him/herself.
        #   - 08 May 2023 07:26 Editor Elaine Reynoso-Haynes will review JCOM_009A_0123 by him/herself.
        #   also if this is not blocked by transition conditions, the first action is skipped because would
        #   cause the first RA to be visible and "deselected"
        noop_cases = {
            ("JCOM_002Y_0216", 1, 261767),  # assigned to ed and ed did I-will-review, but ed is also author
            ("JCOM_001E_0318", 1, 267325),  # assigned to ed and ed did I-will-review, but ed is also author
            ("JCOM_001E_0915", 1, 260742),  # assigned to ed and ed did I-will-review, but ed is also author
            ("JCOM_009A_0123", 2, 291251),
            ("JCOMAL_001A_1018", 2, 10),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ED_ACT_AS_REF for {self.preprintid}/"
                f"{self.imported_version_num} (impossible action edt eq auth/repeated action - known case)."
            )
            return

        self.check_editor_set()
        editor = reviewer = self.get_current_editor()
        logger.debug(f"Creating review assignment of {self.article.id} to editor as reviewer")

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = editor

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            # default message from settings
            # TODO: verify message sent by the logic

            # wjapp does not record a due-date, so we set a fictitious date that simulates what wjs would do
            # using freeze_time now() is refereeAssignDate
            date_due = timezone.now().date() + datetime.timedelta(days=21)

            form_data = {
                "acceptance_due_date": date_due,
                "message": "VALUE NOT USED",
            }
            # the automated message is not disabled otherwise the action does not appear in the timeline
            review_assignment = AssignToReviewer(
                reviewer=reviewer,
                workflow=self.article.articleworkflow,
                editor=editor,
                form_data=form_data,
                request=request,
            ).run()

        self.check_and_fix_all_reminder()
        return review_assignment

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for ED_ACT_AS_REF if exists in wjs"""

        # Note wjs logic adds only reminder REWR1 and REWR2 when the reviewer is the editor
        self.check_and_fix_action_reminder("i_will_review", "REWR", 2)


class ReviewAssignmentAction(BaseActionManager):
    """Review assignment management.

    All actions of this class map (roughly) to ReviewAssignment.
    Review assignments are created onto the current review round; see external loop on versions.
    """

    def run(self):

        noop_cases = {
            ("JCOM_007A_0321", 1, 281937),
            ("JCOMAL_005A_0622", 1, 2990),
            ("JCOMAL_003A_0822", 3, 3121),
            ("JCOMAL_001A_1022", 1, 3175),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ReviewAssignmentAction "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated and wrong logic - known case)."
            )
            return

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
                "refereePrivacy": self.action["targetPrivacy"],
                "refereeAssignDate": self.action["actionDate"],
                "report_due_date": None,
                "refereeAcceptDate": None,
                "reminderNumber": None,
                "refereeReminderDate": None,
                "refereeReminderEnabled": None,
                "refereeReminder2Date": None,
                "refereeReminder2Enabled": None,
                "editorReminderDate": None,
                "editorReminderEnabled": None,
                "refereeReminder1ForRefereeReportDate": None,
                "refereeReminder1ForRefereeReportEnabled": None,
                "refereeReminder2ForRefereeReportDate": None,
                "refereeReminder2ForRefereeReportEnabled": None,
                "editorReminderForRefereeReportEnabled": None,
                "editorReminderForRefereeReportDate": None,
            }

        # select reviewer message

        special_cases = {("JCOM_010A_0615", 1, 260355)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in special_cases:
            # fake reviewer message. Not existing documentLayerCod = 0 does not create problems when
            # inserted in imported_document_layer_cod_list at the end of the action.
            reviewer_message = {
                "documentLayerCod": 0,
                "documentLayerText": "",
            }
            logger.warning(
                f"Fix for missing reviewer msg {self.action['actHistCod']}  "
                f"for {self.preprintid}/{self.imported_version_num} (wjapp data missing - known case)."
            )
        else:
            reviewer_message = self.read_reviewer_message()
        logger.debug(f"Reviewer message: {reviewer_message.get('documentLayerCod')}")

        self.set_reviewer(reviewer_data, reviewer_message)

    def read_reviewer_data(self):
        """Read reviewer data."""

        cursor_reviewer = self.connection.cursor(dictionary=True)
        query_reviewer = """
SELECT
refereeCod,
reminderNumber,
refereeReminderDate,
refereeReminderEnabled,
refereeReminder2Date,
refereeReminder2Enabled,
editorReminderDate,
editorReminderEnabled,
refereeReminder1ForRefereeReportDate,
refereeReminder1ForRefereeReportEnabled,
refereeReminder2ForRefereeReportDate,
refereeReminder2ForRefereeReportEnabled,
editorReminderForRefereeReportEnabled,
editorReminderForRefereeReportDate,
u.lastName  AS refereeLastName,
u.firstName AS refereeFirstName,
u.email     AS refereeEmail,
u.privacy   AS refereePrivacy,
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
        # from editor, in a certain time range respect to the action_date
        # time range fixed from 5" to 7" due to JCOM_002N_1224

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
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 7 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_004A_1016"
            and self.imported_version_cod == 53390
            and self.action["targetCod"] == 8558
        ):
            action_date = "20161017151904"
            logger.debug(f"fixed reviewer assignment msg timing {self.preprintid}")

        if (
            self.preprintid == "JCOM_007A_1216"
            and self.imported_version_cod == 53463
            and self.action["targetCod"] == 8901
        ):
            action_date = "20161214104653"
            logger.debug(f"fixed reviewer assignment msg timing {self.preprintid}")

        if (
            self.preprintid == "JCOM_003A_0517"
            and self.imported_version_cod == 53648
            and self.action["targetCod"] == 9422
        ):
            action_date = "20170508151606"
            logger.debug(f"fixed reviewer assignment msg timing {self.preprintid}")

        cursor_reviewer_message.execute(
            query_reviewer_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
                "action_date": action_date,
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
            reviewer_data["refereePrivacy"],
            self.connection,
        )
        logger.debug(f"Creating review assignment of {self.article.id} to reviewer {reviewer}")

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.get_current_editor()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_data["refereeAssignDate"]),
        ):
            # default message from settings
            # TODO: verify mail subject exists
            # TODO: verify signature in the final message request.user.signature is not missing

            interval_days = get_setting(
                "wjs_review",
                "default_review_acceptance_days",
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
                    setting_group_name="email",
                    setting_name="review_assignment",
                    journal=self.journal,
                    request=request,
                    context={
                        "article": self.article,
                        "reviewer": reviewer,
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
                request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
                request.user = reviewer

                with freezegun.freeze_time(
                    rome_timezone.localize(reviewer_data["refereeAcceptDate"]),
                ):
                    # the automated message is disabled
                    EvaluateReview._log_accept = noop
                    EvaluateReview(
                        assignment=review_assignment,
                        reviewer=reviewer,
                        editor=self.get_current_editor(),
                        form_data={
                            "reviewer_decision": "1",
                            "additional_comments": "",
                            "accept_gdpr": True,
                            "date_due": timezone.now().date() + datetime.timedelta(days=21),
                        },
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

        # TODO: add disable and sent flags to reviewer and editor reminder taken
        # from reviewer_data (add fields)
        self.check_and_fix_reviewer_editor_reminder(review_assignment, reviewer_data)

        return review_assignment

    def check_and_fix_reviewer_editor_reminder(self, review_assignment, reviewer_data):
        """Check and fix sent and disable reminder for review assignment."""

        # data taken from Current_Referees
        #
        # This kind of reminder related to review assignment in wjapp is managed with Current_Referee table only,
        # not with Reminder table like the other reminders. E.g: JCOM_013A_1124
        ra_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.id,
        )
        for r in ra_reminders:
            # from wjapp properties

            next_day_delay = 1

            # REEA
            ref_acc_limit_days = 4
            ref_acc_limit2_days = 2
            ref_acc_limit_editor_reminder_days = 3

            # REWR2
            referee_report_reminder_for_editor_days = 4

            if r.recipient == review_assignment.reviewer and r.code == "REEA1":
                # REEA1 "Reviewer should evaluate assignment" -> refereeReminderEnabled/Date
                if not reviewer_data["refereeReminderEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["refereeReminderDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["refereeReminderDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["refereeReminderDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = (
                    rome_timezone.localize(reviewer_data["refereeAssignDate"])
                    + datetime.timedelta(days=next_day_delay)
                    + datetime.timedelta(days=ref_acc_limit_days)
                ).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()
                review_assignment.date_due = r.date_due
                review_assignment.save()
                logger.debug(
                    f"{r.code} ({r.id}) and RA {review_assignment.id} (article {review_assignment.article.id})"
                    f" due_date change {r.date_due} for {r.recipient}"
                )

            if r.recipient == review_assignment.reviewer and r.code == "REEA2":
                # REEA2 "Reviewer should evaluate assignment" -> refereeReminder2Enabled/Date
                if not reviewer_data["refereeReminder2Enabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["refereeReminder2Date"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["refereeReminder2Date"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["refereeReminder2Date"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = (
                    rome_timezone.localize(reviewer_data["refereeAssignDate"])
                    + datetime.timedelta(days=next_day_delay)
                    + datetime.timedelta(days=ref_acc_limit_days)
                    + datetime.timedelta(days=ref_acc_limit2_days)
                ).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()

            if r.recipient == review_assignment.editor and r.code == "REEA3":
                # REEA3 "Reviewer should evaluate assignment" > editorReminderEnabled/Date
                if not reviewer_data["editorReminderEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["editorReminderDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["editorReminderDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["editorReminderDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = (
                    rome_timezone.localize(reviewer_data["refereeAssignDate"])
                    + datetime.timedelta(days=next_day_delay)
                    + datetime.timedelta(days=ref_acc_limit_days)
                    + datetime.timedelta(days=ref_acc_limit2_days)
                    + datetime.timedelta(days=ref_acc_limit_editor_reminder_days)
                ).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()

            if r.recipient == review_assignment.reviewer and r.code == "REWR1":
                # REWR1 "Reviewer should write review" -> refereeReminder1ForRefereeReportEnabled
                # e.g. JCOM_002N_0125 JCOM_002N_1224 JCOM_009A_0325
                if not reviewer_data["refereeReminder1ForRefereeReportEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["refereeReminder1ForRefereeReportDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["refereeReminder1ForRefereeReportDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["refereeReminder1ForRefereeReportDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = rome_timezone.localize(reviewer_data["report_due_date"]).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()

            if r.recipient == review_assignment.editor and r.code == "REWR2":
                # REWR2 "Reviewer should write review" -> editorReminderForRefereeReportDate
                if not reviewer_data["editorReminderForRefereeReportEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["editorReminderForRefereeReportDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["editorReminderForRefereeReportDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["editorReminderForRefereeReportDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = (
                    rome_timezone.localize(reviewer_data["report_due_date"])
                    + datetime.timedelta(days=referee_report_reminder_for_editor_days)
                ).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()


class ED_ASS_REF(ReviewAssignmentAction):  # noqa N801
    """Manages wjapp action "editor assigns referee"."""


class ED_ADD_REF(ReviewAssignmentAction):  # noqa N801
    """Manages wjapp action "editor adds referee"."""


class DeselectReviewerAction(BaseActionManager):
    """Editor deselect reviewer."""

    def run(self):
        # wjapp actions for editor deselect reviewer (ex:JCOM_005N_0324)

        # EQ1_ED_REM_REF	[#referee=1]/editor removes referee (e.g. JCOM_007A_0724)
        # GT1_ED_REM_REF	[#referee>1]/editor removes referee (e.g. JCOM_013A_0724)
        # ED_REM_REF	editor removes referee (e.g. JCOM_018A_0724)

        noop_cases = {
            ("JCOM_002A_0323", 1, 291112),
            ("JCOM_002C_1216", 1, 263831),
            ("JCOM_010A_0615", 1, 260593),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} deselect reviewer "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        reviewer_cod = self.action["targetCod"]
        reviewer_lastname = self.action["targetLastname"]
        reviewer_firstname = self.action["targetFirstname"]
        reviewer_email = self.action["targetEmail"]
        reviewer_privacy = self.action["targetPrivacy"]
        reviewer_deselection_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
            reviewer_privacy,
            self.connection,
        )

        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.get_current_editor()

        # deselect reviewer message
        deselect_reviewer_message = self.read_deselect_reviewer_message()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_deselection_date),
        ):
            # the automated message is disabled
            DeselectReviewer._log_operation = noop
            DeselectReviewer(
                assignment=review_assignment,
                actor=review_assignment.editor,
                request=request,
                send_reviewer_notification=False,
                form_data={
                    "notification_subject": deselect_reviewer_message.get("documentLayerSubject"),
                    "notification_body": deselect_reviewer_message.get("documentLayerText"),
                },
            ).run()

        self.check_and_fix_all_reminder()

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for DeselectReviewerAction if exists in wjs"""

        self.check_and_fix_action_reminder("ed_removed_ed_decision", "EDMD", 3)

    def read_deselect_reviewer_message(self):
        """Read the message that is sent to the reviewer when he is deselected."""

        cursor_deselect_reviewer_message = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (6") respect to the action_date
        # e.g. 6" delay JCOM_009A_0124  actionDate:  2024-04-01 16:54:02 docLayer submissionDate 2024-04-01 16:54:07

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

        # Because the action date is out of the interval set in the query, it is forced like the
        # documentlayer date. The option to enlarge more the interval in the query risks to bring
        # errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_009A_0718"
            and self.imported_version_cod == 54251
            and self.action["targetCod"] == 9905
        ):
            action_date = "20180831095428"
            logger.debug(f"fixed deselect reviewer msg 6 seconds after action date {self.preprintid}")

        if (
            self.preprintid == "JCOM_005A_0515"
            and self.imported_version_cod == 52998
            and self.action["targetCod"] == 8640
        ):
            action_date = "20151210115632"
            logger.debug(f"fixed deselect reviewer msg 24 seconds after action date {self.preprintid}")

        cursor_deselect_reviewer_message.execute(
            query_deselect_reviewer_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
                "action_date": action_date,
            },
        )
        if cursor_deselect_reviewer_message.rowcount != 1:
            noop_cases = {
                ("JCOM_002C_1216", 53455, 8528),
            }
            if (self.preprintid, self.imported_version_cod, self.action["targetCod"]) in noop_cases:
                logger.warning(
                    f"Skipped duplicated deselect reviewer message for agent {self.action['targetCod']} "
                    f"and {self.preprintid} version cod: {self.imported_version_cod} (duplicated - known case)."
                )
                deselect_reviewer_message = cursor_deselect_reviewer_message.fetchone()
            else:
                logger.error(
                    f"Found {cursor_deselect_reviewer_message.rowcount} deselect reviewer messages: {self.preprintid}"
                )
                deselect_reviewer_message = None
        else:
            deselect_reviewer_message = cursor_deselect_reviewer_message.fetchone()
        cursor_deselect_reviewer_message.close()

        return deselect_reviewer_message


class EQ1_ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action "one referee and editor removes referee"."""


class GT1_ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action "more than one referee and editor removes referee"."""


class ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action "editor removes referee"."""


class ReviewerDeclineAction(BaseActionManager):
    """Reviewer decline management."""

    def run(self):
        # wjapp actions for referee declined assignment for preprintid in wjapp:

        # Known cases
        noop_cases = {
            ("JCOM_002Y_0216", 1, 261768),  # the previous ed act as ref removed due to auth eq ed
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ReviewerDeclineAction "
                f"for {self.preprintid}/{self.imported_version_num} (due to auth as ed- known case)."
            )
            return

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
        # from editor, in a certain time range respect to the action_date
        # time range fixed from 5" to 7" due to JCOM_010A_1224

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
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 7 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_005A_0416"
            and self.imported_version_cod == 53205
            and self.action["agentCod"] == 8572
        ):
            action_date = "20160504155638"
            logger.debug(f"fixed reviewer decline msg 11 seconds after action date {self.preprintid}")

        if (
            self.preprintid == "JCOM_002A_0616"
            and self.imported_version_cod == 53277
            and self.action["agentCod"] == 8474
        ):
            action_date = "20160625193509"
            logger.debug(f"fixed reviewer decline msg 15 seconds after action date {self.preprintid}")

        cursor_reviewer_decline_message.execute(
            query_reviewer_decline_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
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
        reviewer_privacy = self.action["agentPrivacy"]
        reviewer_declines_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
            reviewer_privacy,
            self.connection,
        )

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
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

            additional_comments = newlines_text_to_html(reviewer_decline_message.get("documentLayerText"))

            # TBV: not added to imported document layer list because the decline action does not save the message.
            # therefore the import of the message happens with the general correspondence.

            EvaluateReview(
                assignment=review_assignment,
                reviewer=reviewer,
                editor=self.get_current_editor(),
                form_data={"reviewer_decision": "0", "additional_comments": additional_comments, "accept_gdpr": True},
                request=request,
                token=None,
            ).run()

        self.check_and_fix_all_reminder()
        return

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for ReviewerDeclineAction if exists in wjs"""

        self.check_and_fix_action_reminder("ref_declined_ed_decision", "EDMD", 3)
        self.check_and_fix_action_reminder("Ref_decline", "EDSR", 3)


class EQ1_REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "one referee and referee refuses"."""

    # automated message disabled
    EvaluateReview._log_decline = noop


class GT1_REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "more than one and referee refuses"."""

    # automated message disabled
    EvaluateReview._log_decline = noop


class REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "referee refuses"."""

    # automated message disabled
    EvaluateReview._log_decline = noop


class ED_NEED_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "editor needs referee"."""

    # in wjapp this action means that the editor stops to review her/him self
    # the article and will assign referees (in wjs declines as reviewer)

    # the automated message is not disabled for ED_NEED_REF
    # otherwise the action does not appear in the timeline
    # e.g. JCOM_004A_0924


class REF_ACC(BaseActionManager):  # noqa N801
    """Reviewer accepts the review: wjapp action "referee accepts"."""

    def run(self):
        logger.debug("REF_ACC managed in ReviewAssignmentAction but without reviewer confirmation message")


class REF_SENDS_REP(BaseActionManager):  # noqa N801
    """Reviewer send report management: wjapp action "referee sends report"."""

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

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOMAL_002A_0621"
            and self.imported_version_cod == 390
            and self.action["agentCod"] == 10293
        ):
            action_date = "20210804230010"
            logger.debug(f"fixed reviewer date 20 seconds after action date {self.preprintid}")

        cursor_reviewer_report_message.execute(
            query_reviewer_report_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
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
        reviewer_privacy = self.action["agentPrivacy"]
        reviewer_report_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
            reviewer_privacy,
            self.connection,
        )

        # filter and last() not get() to manage JCOM_008A_0324 with 2 review assignments
        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()
        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = reviewer
        submit_final = True

        # the (current) default review form element "Cover letter (for the Editor in charge)""
        # is a WjsMiniHTMLFormField. Text from wjapp must not be formatted to html.
        formatted_cover_letter_message = wjapp_report.get("documentLayerText")

        # the (current) default review form element "Review (for the Author)"
        # is a WjsMiniHTMLFormField. Text from wjapp must not be formatted to html.
        formatted_report_message = wjapp_report.get("documentLayerOnlyTex")

        # we leave empty those fields that don't exist in wjapp and
        # that do not have a "mandatory" value (such as no-conflict-of-interests).

        report_form = get_report_form(self.journal)

        # with the data in this example the form would be valid, but the imported report
        # have not this data. The Key error on the template report_form_summary.html
        # has been solved in template tag "keyvalue"
        #
        # conflict_of_interest: "no",
        # structure_and_writing_style: "Good",
        # originality: "Good",
        # scope_and_methods: "Good",
        # argument_and_discussion: "Good",
        # recommendation: "publish",
        # editor_cover_letter: formatted_cover_letter_message,
        # author_review: formatted_report_message,

        # Note: form not valid because has empty values
        jcom_report_form_data = {
            "conflict_of_interest": "no",
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

            # the automated message is disabled
            SubmitReview._log_operation = noop
            submit = SubmitReview(
                assignment=review_assignment,
                form=form,
                submit_final=submit_final,
                request=request,
            )
            wra = submit.run()

            editor_message_subject = render_template_from_setting(
                setting_group_name="email_subject",
                setting_name="subject_review_complete_acknowledgement",
                journal=self.journal,
                request=request,
                context={
                    "review_assignment": review_assignment,
                    "article": self.article,
                },
                template_is_setting=True,
            )
            communication_utils.log_operation(
                actor=reviewer,
                article=self.article,
                message_subject=editor_message_subject,
                message_body="",
                recipients=[self.get_current_editor()],
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
                hijacking_actor=get_hijacker(),
                notify_actor=False,
            )

            self.imported_document_layer_cod_list.append(wjapp_report.get("documentLayerCod"))
            logger.debug(f"append referee report message {self.imported_document_layer_cod_list=}")

            # WorflowReviewAssignment.report_form_answers is a json containing:
            # - editor_cover_letter (cover letter REREP)
            # - author_review (text REREP)
            self.imported_doclayer_check_visibility[wjapp_report.get("documentLayerCod")] = {
                "obj": wra,
                "type": "REREP",
            }

        self.check_and_fix_all_reminder()

        return

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for REF_SENDS_REP if exists in wjs"""

        # The referee sends report action could be the first, the second, the last report.
        # The check and fix function changes only the existing reminders EDMD created by the wjs logic,
        # therefore if the wjs logic does not supply reminders for this action, nothing is changed,
        # (also if the reminder is read from wjapp) otherwise the date is fixed if it is different.
        self.check_and_fix_action_reminder("only_ref_sent_rep", "EDMD", 3)
        self.check_and_fix_action_reminder("last_ref_sent_rep", "EDMD", 3)


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

        # special case broken action data on wjapp, missing agentCod:
        #
        # -   actHistCod: 8
        # -   versionCod: 1
        # -    actionCod: 8
        # -     agentCod: NULL
        # -      userCod: 10004
        # - realAgentCod: NULL
        # -   actionDate: 2018-10-31 13:10:43

        if self.action["actHistCod"] == 8 and self.journal.code == "JCOMAL" and self.preprintid == "JCOMAL_001A_1018":
            self.action["agentCod"] = 10004
            logger.warning(
                f"fix action history jcomal/8: {self.imported_version_cod}"
                f" {self.action['agentCod']} {self.action['actionDate']}"
            )

        revision = self.editor_decides()
        if self.requires_revision:
            logger.debug(f"editor decision with revision request EditorRevisionRequest: {revision=}")

    def download_edrep_file(self, document_layer_id):
        """Download EDREP file.

        special case:
        edrep not into the wjapp database report url i.e.: JCOMAL_001A_1018_EDREP000071018

        https://jcomal.sissa.it/jcomal/common/archiveFile?filePath=JCOMAL_001A_1018/1
        /EDREP/JCOMAL_001A_1018_EDREP000071018/JCOMAL_001A_1018_EDREP000071018.txt&fileType=txt
        """

        file_url = (
            f"{self.url_base}{self.preprintid}/{self.imported_version_num}/EDREP/{document_layer_id}/"
            f"{document_layer_id}.txt&fileType=txt"
        )

        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(f"empty {document_layer_id} file downloaded: {response.headers['Content-Length']}")
        return response.text

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
        # e.g. JCOM_027Y_0215 has the cover letter but not the report file

        # special case missing documentLayerOnlyTeX data on wjapp
        if (
            self.journal.code == "JCOMAL"
            and self.preprintid == "JCOMAL_001A_1018"
            and wjapp_editor_report.get("documentLayerCod") == 7
        ):
            wjapp_editor_report_message = newlines_text_to_html(
                str(self.download_edrep_file("JCOMAL_001A_1018_EDREP000071018"))
            )
            logger.warning(
                f"fix missing edrep in documentLayerOnlyTeX: {wjapp_editor_report.get('documentLayerCod')=}"
            )
        else:
            wjapp_editor_report_message = newlines_text_to_html(wjapp_editor_report.get("documentLayerOnlyTex"))

        editor_report_message = "<br><br><br><br>".join(
            filter(None, [wjapp_editor_cover_letter_message, wjapp_editor_report_message])
        )

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.get_current_editor()

        with freezegun.freeze_time(
            rome_timezone.localize(editor_report_date),
        ):
            # If the editor has done i will review, we set the wra of the editor "completed"
            # if it is not, otherwise the wra results as deselect.
            # The editor report is not uploaded as reviewer report, because it seems useless.
            # In wjapp in this case the editor does not upload
            # a report as referee. It would be equal to the editor report.
            # TBV: better to do the action SubmitReview using the same report text?
            review_assignment = WorkflowReviewAssignment.objects.filter(
                reviewer=self.get_current_editor(),
                article=self.article,
                editor=self.get_current_editor(),
                review_round=self.article.current_review_round_object(),
            ).last()
            if review_assignment and not review_assignment.is_complete:
                review_assignment.date_complete = timezone.now()
                review_assignment.is_complete = True
                review_assignment.save()

                # delete reminders related to the review assignment
                Reminder.objects.filter(
                    content_type=ContentType.objects.get_for_model(review_assignment),
                    object_id=review_assignment.id,
                ).delete()

            date_due = timezone.now().date()
            if self.requires_revision:
                date_due = date_due + datetime.timedelta(days=self.revision_interval_days)

            # wjapp has only one document revision dead line therefore
            # this assignment could be not correct if this is for example the first revision request
            # and afterwards there is another one revision request
            # but the same problem can happen if this assignment is done
            # during the action PSTPN_REV_DEADLN (which is managed as correspondence only)

            # added guard for None document_revision_dead_line: JCOM_002N_0824
            if self.document_revision_dead_line:
                if self.document_revision_dead_line.date() >= date_due:
                    date_due = self.document_revision_dead_line

            # TBV: date_due has to be set in the form in the case of rejection?
            form_data = {
                "decision": self.editor_decision,
                "decision_editor_report": editor_report_message,
                "withdraw_notice": "notice",
                "date_due": date_due,
            }

            # TODO: fix JCOM_004A_0424 exception: WjsEditorAssignment matching query does not exist

            # the automated message are disabled
            # TODO: ex: JCOM_002N_0824 Subject: Invite to review withdrawn
            # is not disabled on rejection
            HandleDecision._log_accept = noop
            HandleDecision._log_decline = noop
            HandleDecision._log_not_suitable = noop
            HandleDecision._log_revision_request = noop
            HandleDecision._log_requires_resubmission = noop
            HandleDecision._log_technical_revision_request = noop
            handle = HandleDecision(
                workflow=self.article.articleworkflow,
                form_data=form_data,
                user=self.get_current_editor(),
                request=request,
            )
            handle.run()

            message_timeline = render_template_from_setting(
                setting_group_name=self.setting_group_name,
                setting_name=self.setting_name,
                journal=self.journal,
                request=request,
                context={
                    "article": self.article,
                    "decision": self.editor_decision,
                    "request": request,
                },
            )

            communication_utils.log_operation(
                article=self.article,
                message_subject=f"{message_timeline}",
                message_body="",
                actor=self.get_current_editor(),
                recipients=[self.article.correspondence_author],
                verbosity=Message.MessageVerbosity.TIMELINE,
                hijacking_actor=get_hijacker(),
                notify_actor=communication_utils.should_notify_actor(),
            )

        self.article.refresh_from_db()
        revision = None
        if self.requires_revision:
            revision = EditorRevisionRequest.objects.get(
                article=self.article, review_round=self.article.current_review_round_object()
            )

        self.imported_document_layer_cod_list.append(wjapp_editor_report.get("documentLayerCod"))

        # EditorRevisionRequest.editor_decision contains the editor report
        # (wjapp editor cover letter + wjapp editor report)
        # if revision is not required, no need to check extra permissions in wjapp
        if revision:
            self.imported_doclayer_check_visibility[wjapp_editor_report.get("documentLayerCod")] = {
                "obj": revision.editor_decision,
                "type": "EDREP",
            }

        self.check_and_fix_all_reminder()

        return revision


@dataclass
class ED_REQ_REV(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor requires major revision"."""

    def __post_init__(self):
        """Set the specific data for major revision"""
        self.editor_decision = ArticleWorkflow.Decisions.MAJOR_REVISION
        self.requires_revision = True
        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_major_revision_days",
            self.journal,
        ).process_value()
        self.setting_group_name = "email_subject"
        self.setting_name = "subject_request_revisions"

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminder for ED_REQ_REV if exists in wjs"""

        self.check_and_fix_action_reminder("pending_revision", "AUMJR", 3)


@dataclass
class ED_ACC_DOC_WMC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor requires minor revision"."""

    def __post_init__(self):
        """Set the specific data for minor revision"""
        self.editor_decision = ArticleWorkflow.Decisions.MINOR_REVISION
        self.requires_revision = True
        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_minor_revision_days",
            self.journal,
        ).process_value()
        self.setting_group_name = "wjs_review"
        self.setting_name = "review_decision_requires_resubmission_subject"

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminders for ED_ACC_DOC_WMC if exists in wjs"""

        self.check_and_fix_action_reminder("pending_minor_revision", "AUMIR", 3)


@dataclass
class ED_REJ_DOC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor rejects document"."""

    def __post_init__(self):
        """Set the specific data for rejection"""
        self.editor_decision = ArticleWorkflow.Decisions.REJECT
        self.requires_revision = False
        self.revision_interval_days = 0
        self.setting_group_name = "email_subject"
        self.setting_name = "subject_review_decision_decline"


@dataclass
class ED_CON_NOT_SUIT(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor considers not suitable"."""

    def __post_init__(self):
        """Set the specific data for not suitable"""
        self.editor_decision = ArticleWorkflow.Decisions.NOT_SUITABLE
        self.requires_revision = False
        self.revision_interval_days = 0
        self.setting_group_name = "wjs_review"
        self.setting_name = "review_decision_not_suitable_subject"


# TBV: DEBUG 2024-06-02 11:00:33,000 M:logic: No XML galleys found for crossref citation extraction
# TO FIX: exeception
@dataclass
class ED_ACC_DOC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor accepts document"."""

    def __post_init__(self):
        """Set the specific data for acceptance"""
        self.editor_decision = ArticleWorkflow.Decisions.ACCEPT
        self.requires_revision = False
        self.revision_interval_days = 0
        self.setting_group_name = "email_subject"
        self.setting_name = "subject_review_decision_accept"


@dataclass
class AuthorSubmitRevisionAction(BaseActionManager):
    """Author submit revision management."""

    # action_triggers_import_files not used here because this action always implies new files from the author

    def run(self):
        # TBV: author of the action is the same of main_author?
        #      the author could be switched with coauthor

        # This fix solves a problem in broken wjapp data for JCOM_031A_1024/2 which is
        # a version with only the submission of the contribution and no other actions.
        # The workaround is to not execute AuthorHandleRevision for this version
        # and let only import the correspondence. The files and the author cover letter
        # are not imported because they are not been reviewed. The review has been done
        # on version  JCOM_031A_1024/3.
        if self.preprintid == "JCOM_031A_1024" and self.imported_version_num == 2:
            logger.warning(
                f"fix of broken wjapp data version eliminated: {self.preprintid} {self.imported_version_num=}"
            )

            return

        author_report_date = self.action["actionDate"]

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.article.correspondence_author

        author_note = self.read_author_cover_letter_message()

        form_data = {"author_note": newlines_text_to_html(author_note["documentLayerText"])}

        # TODO: missing for JCOM_031A_1024 data broken on wjapp
        revision_request = EditorRevisionRequest.objects.get(
            article=self.article, review_round=self.article.current_review_round_object()
        )

        with freezegun.freeze_time(
            rome_timezone.localize(author_report_date),
        ):
            # the automated message is not disabled on purpose to have
            # the timeline
            # AuthorHandleRevision._log_operation = noop #noqa
            service = AuthorHandleRevision(
                revision=revision_request,
                form_data=form_data,
                user=self.article.correspondence_author,
                request=request,
            )
            revision = service.run()

        self.article.refresh_from_db()

        self.imported_document_layer_cod_list.append(author_note.get("documentLayerCod"))

        # EditorRevisionRequest.author_note contains the author cover letter
        self.imported_doclayer_check_visibility[author_note.get("documentLayerCod")] = {
            "obj": revision,
            "type": "CVLETT",
        }

        if self.importfiles:
            self.import_files()

        self.check_and_fix_all_reminder()

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
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 6 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])

        if self.preprintid == "JCOM_001A_0520" and self.imported_version_cod == 55227:
            action_date = "20200616233039"
            logger.debug(f"fixed cvlett date 21 seconds after action date {self.preprintid}")

        if self.preprintid == "JCOM_005A_1117" and self.imported_version_cod == 54139:
            action_date = "20180428112442"
            logger.debug(f"fixed cvlett date 8 seconds after action date {self.preprintid}")

        if self.preprintid == "JCOM_006A_1217" and self.imported_version_cod == 54012:
            action_date = "20180215042732"
            logger.debug(f"fixed cvlett date 6 seconds after action date {self.preprintid}")

        if self.preprintid == "JCOM_005A_1216" and self.imported_version_cod == 53625:
            action_date = "20170410161125"
            logger.debug(f"fixed cvlett date 6 seconds after action date {self.preprintid}")

        cursor_cover_letter_message.execute(
            query_cover_letter_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
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


@dataclass
class AU_SUB_REV(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author submits revised version"."""

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for AU_SUB_REV if exists in wjs"""

        self.check_and_fix_action_reminder("major_revision", "EDSR", 3)


@dataclass
class AU_SUB_REV_WMC(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author submits minor revision"."""

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for AU_SUB_REV_WMC if exists in wjs"""

        self.check_and_fix_action_reminder("minor_revision", "EDSR", 3)


@dataclass
class AU_SUB_REV_ED_CH(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "Author submits after ed check"."""

    def __post_init__(self):
        """logs error if not know case"""

        known_cases = {("JCOM_003A_0417", 4, 266570), ("JCOM_012A_0615", 4, 261114)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) not in known_cases:
            logger.error(
                f"case not managed {self.action['actHistCod']} AU_SUB_REV_ED_CH "
                f"for {self.preprintid}/{self.imported_version_num}."
            )
        else:
            logger.warning(
                f"AU_SUB_REV_ED_CH {self.action['actHistCod']} "
                f"for {self.preprintid}/{self.imported_version_num} (known case)."
            )


@dataclass
class AU_SUB_NEW_VER(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author submits new version" (appeal)."""


@dataclass
class AU_CONF_VER(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author confirms version" (appeal)."""


class SelectCoauthorAction(BaseActionManager):
    """Coauthor selection management."""

    def run(self):

        # JCOM_001E_0322 is a published paper. Coauthor remains corrected as published.
        noop_cases = {
            ("JCOM_001E_0322", 1, 285908),
            ("JCOM_007A_0623", 1, 291630),
            ("JCOM_004Y_0623", 1, 292042),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} Select coauthor "
                f"for {self.preprintid}/{self.imported_version_num} (coauthor as reviewer/editor - known case)."
            )
            return

        # coauthor is the target of the wjapp action
        coauthor_cod = self.action["targetCod"]
        coauthor_lastname = self.action["targetLastname"]
        coauthor_firstname = self.action["targetFirstname"]
        coauthor_email = self.action["targetEmail"]
        coauthor_privacy = self.action["targetPrivacy"]
        coauthor_assign_date = self.action["actionDate"]

        # coauthor data
        coauthor = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            coauthor_cod,
            coauthor_lastname,
            coauthor_firstname,
            coauthor_email,
            coauthor_privacy,
            self.connection,
        )
        logger.debug(f"Creating coauthor of {self.article.id} user: {coauthor}")

        # NOTE: the wjapp message related to select coauthor acion is imported to wjs
        # as general correspondence
        with freezegun.freeze_time(
            rome_timezone.localize(coauthor_assign_date),
        ):
            if not coauthor.check_role(self.journal, "author", staff_override=False):
                coauthor.add_account_role("author", self.journal)
            self.article.authors.add(coauthor)
            self.article.save()
            order = len(self.article.authors.all())
            submission_models.ArticleAuthorOrder.objects.get_or_create(
                article=self.article,
                author=coauthor,
                defaults={
                    "order": order,
                },
            )

        return


class AU_SELECTS_COAUT(SelectCoauthorAction):  # noqa N801
    """Manages wjapp action "author selects co-author"."""


class ADMIN_SELECTS_COAUT(SelectCoauthorAction):  # noqa N801
    """Manages wjapp action "editorial office selects co-author"."""


class SwapCorrespondenceAuthor(BaseActionManager):  # noqa N801
    """Manages wjapp action "author changes corresponding author"."""

    def run(self):
        # E.g. JCOM_001A_1115
        # note: in wjapp the new corresponding author must be one of the coauthors

        # This method changes the corresponding author. The related messages are loaded as correspondence
        # for all the subclasses maintaining sender and recipient

        new_author_row = self.read_new_author_data()
        new_author = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            new_author_row["new_author_cod"],
            new_author_row["new_author_lastname"],
            new_author_row["new_author_firstname"],
            new_author_row["new_author_email"],
            new_author_row["new_author_privacy"],
            self.connection,
        )
        logger.debug(f"swap corresponding author, new: {new_author}")
        assert new_author in self.article.authors.all()

        # when the action swaps between the past author of version 1
        # added at first action SYS_ASS_ED and the current correspondence
        # author set at create article, there is nothing to do, also the
        # order is already correct.
        # If the two users are different, also the orders are swapped.
        if new_author != self.article.correspondence_author:
            self.article.owner = new_author
            aao_new_author = submission_models.ArticleAuthorOrder.objects.get(
                article=self.article,
                author=new_author,
            )
            submission_models.ArticleAuthorOrder.objects.filter(
                article=self.article,
                author=self.article.correspondence_author,
            ).update(order=aao_new_author.order)

            aao_new_author.order = 0
            aao_new_author.save()

            self.article.correspondence_author = new_author
            self.article.save()

    def read_new_author_data(self):
        """Read new author data."""

        cursor_new_author = self.connection.cursor(dictionary=True)
        query = """
SELECT
userCod AS new_author_cod,
lastname AS new_author_lastname,
firstname AS new_author_firstname,
email AS new_author_email,
privacy AS new_author_privacy
FROM User
WHERE
userCod = %(new_author_cod)s
"""
        cursor_new_author.execute(
            query,
            {
                "new_author_cod": self.action["targetCod"],
            },
        )
        new_author_row = cursor_new_author.fetchone()
        cursor_new_author.close()
        return new_author_row


class AU_SWAPS_CORR_AU(SwapCorrespondenceAuthor):  # noqa N801
    """Manages wjapp action "author changes corresponding author"."""


class ADMIN_SWAPS_CORR_AU(SwapCorrespondenceAuthor):  # noqa N801
    """Manages wjapp action "editorial office changes corresponding author"."""


# PRODUCTION


class TYP_TAKES_CHARGE(BaseActionManager):  # noqa N801
    """Manages wjapp action "typesetter takes in charge"."""

    def run(self):
        """Assign the typesetter.

        Also create the typesetter's Account if necessary.
        """

        noop_cases = {("JCOM_003A_0417", 2, 265687), ("JCOM_012A_0615", 2, 260995)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} TYP_TAKES_CHARGE "
                f"for {self.preprintid}/{self.imported_version_num} (management ed restart - known case)."
            )
            return

        typesetter_cod = self.action["agentCod"]
        typesetter_lastname = self.action["agentLastname"]
        typesetter_firstname = self.action["agentFirstname"]
        typesetter_email = self.action["agentEmail"]
        typesetter_privacy = self.action["agentPrivacy"]
        typesetter_assign_date = self.action["actionDate"]

        if typesetter_cod:
            typesetter = account_get_or_create_check_correspondence(
                self.journal.code.lower(),
                typesetter_cod,
                typesetter_lastname,
                typesetter_firstname,
                typesetter_email,
                typesetter_privacy,
                self.connection,
            )
            if not typesetter.check_role(self.journal, "typesetter", staff_override=False):
                typesetter.add_account_role("typesetter", self.journal)

            logger.debug(f"Assign typ: {typesetter.last_name} {typesetter.first_name} onto {self.article.pk}")
            request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
            request.user = typesetter

            with freezegun.freeze_time(
                rome_timezone.localize(typesetter_assign_date),
            ):
                typesetting_assignment = AssignTypesetter(
                    article=self.article,
                    typesetter=typesetter,
                    request=request,
                ).run()
                self.article.save()
                logger.debug(f"typesetting assignment {typesetting_assignment=}")

            # for both published or pending paper
            # the A.data_figure_files are copied as SF in AW.supplementary_files_at_acceptance
            sf = []
            for f in self.article.data_figure_files.all():
                sf.append(SupplementaryFile.objects.create(file=f))
            self.article.articleworkflow.supplementary_files_at_acceptance.set(sf)

            self.article.refresh_from_db()
            return typesetter


class TYP_UPLOADS_FOR_PM(BaseActionManager):  # noqa N801
    """Manages wjapp action "typesetter uploads for production manager"."""

    def run(self):

        noop_cases = {
            ("JCOM_003A_0417", 3, 266295),
            ("JCOM_012A_0615", 3, 260996),
            ("JCOM_027Y_0615", 3, 260386),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} TYP_UPLOADS_FOR_PM "
                f"for {self.preprintid}/{self.imported_version_num} (- known case)."
            )
            return

        typesetter_uploads_date = self.action["actionDate"]

        # get typesetter file tar.gz
        if self.importfiles:
            source_prod_dj = self.file_source_prod()
        else:
            # empty tar.gz when importfiles is disabled
            # the logic action requires a file
            source_prod_dj = DjangoFile(BytesIO(b""), f"{self.preprintid}.tar.gz")

        source_prod_dj.content_type = "application/zip"

        fake_request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        ta_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        fake_request.user = ta_assignment.typesetter

        with freezegun.freeze_time(
            rome_timezone.localize(typesetter_uploads_date),
        ):
            UploadFile._check_file_condition = noop_true
            UploadFile._look_for_queries_in_archive = noop_true

            article_with_file = UploadFile(
                typesetter=ta_assignment.typesetter,
                request=fake_request,
                assignment=ta_assignment,
                file_to_upload=source_prod_dj,
                do_create_galleys=False,
            ).run()
            article_with_file.articleworkflow.save()

        # e.g. JCOM_017A_0624
        if self.importfiles:
            self.import_files(production_version=True)


class Requestproofs(BaseActionManager):
    """Manages wjapp actions "production manager sends to author"."""

    def run(self):
        # Known cases of "dirty data" on wjapp, when this action had no effect.
        # We identify them by a triplet made of preprint id, version number and action-history code
        # (the first two are just human-friendly pointers 🙂).
        noop_cases = {
            ("JCOM_007A_0421", 5, 284286),
            ("JCOM_012A_1020", 4, 281589),
            ("JCOM_013A_1020", 3, 282697),
            ("JCOM_003A_1120", 5, 282339),
            ("JCOM_027A_1120", 7, 281985),
            ("JCOM_001BR_0324", 3, 296043),
            ("JCOM_004N_0724", 5, 299101),
            ("JCOM_001Y_0724", 5, 299357),
            ("JCOM_011A_0123", 8, 291803),
            ("JCOM_001N_0923", 6, 297135),
            ("JCOM_005A_0322", 4, 288477),
            ("JCOM_001N_0321", 6, 285416),
            ("JCOM_003A_0821", 5, 286339),
            ("JCOM_003N_1021", 5, 285322),
            ("JCOM_010N_1021", 4, 285108),
            ("JCOM_012A_1021", 4, 285642),
            ("JCOM_004A_1121", 5, 288627),
            ("JCOM_018A_1021", 3, 285170),
            ("JCOM_023A_1020", 3, 282928),
            ("JCOM_007A_1120", 6, 281952),
            ("JCOM_006A_0118", 3, 268513),
            ("JCOM_006A_0418", 6, 270334),
            ("JCOM_010A_0718", 4, 272266),
            ("JCOM_003C_0818", 2, 269513),
            ("JCOM_004C_1118", 5, 270687),
            ("JCOM_002C_0317", 5, 265426),
            ("JCOM_001CR_0517", 3, 265350),
            ("JCOM_001E_0617", 2, 265459),
            ("JCOM_001C_0516", 5, 262715),
            ("JCOM_001BR_0616", 2, 262890),
            ("JCOM_004A_0616", 4, 263179),
            ("JCOM_019A_1216", 6, 265656),
            ("JCOMAL_001N_1220", 4, 2678),
            ("JCOMAL_001A_0122", 7, 3301),
            ("JCOMAL_003A_0923", 4, 4686),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PM_SENDS_FOR_PROOF_R "
                f"for {self.preprintid}/{self.imported_version_num} (not uploaded new version - known case)."
            )
            return

        fake_request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        fake_request.user = typesetting_assignment.typesetter

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            RequestProofs._log_operation = noop
            RequestProofs(
                assignment=typesetting_assignment,
                typesetter=typesetting_assignment.typesetter,
                request=fake_request,
                workflow=self.article.articleworkflow,
            ).run()

            message_subject = get_setting(
                setting_group_name="wjs_review",
                setting_name="proofreading_request_subject",
                journal=self.article.journal,
            ).processed_value
            communication_utils.log_operation(
                article=self.article,
                message_subject=message_subject,
                message_body="",
                recipients=[
                    self.article.correspondence_author,
                ],
                actor=typesetting_assignment.typesetter,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )

            self.article.refresh_from_db()
            assert self.article.articleworkflow.state == ArticleWorkflow.ReviewStates.PROOFREADING


class PM_SENDS_FOR_PROOF_R(Requestproofs):  # noqa N801
    """Manages wjapp action "production manager sends for proof reading"."""


class PM_REQ_INFO(Requestproofs):  # noqa N801
    """Manages wjapp action "production manager requires information"."""


class AU_SENDS_CORRECT(BaseActionManager):  # noqa N801
    """Manages wjapp action "author sends corrections"."""

    def run(self):
        # E.g. JCOM_017A_0624

        # asked eo, authorized to loose this auann:
        # - JCOM_001A_0323_AUANN000690623.comments ASJ for team final.pdf
        # - JCOM_009A_0119_AUANN000710519 (two auann in the same version, kept the second one)
        # - JCOMAL_003A_0923 resent 3 times, loose first and second keep third final version
        #      - loose JCOMAL_003A_0923_AUANN000330124.docx
        #      - loose JCOMAL_003A_0923_AUANN000340124.jpg
        #
        noop_cases = {
            ("JCOM_023A_0623", 3, 294207),
            ("JCOM_018A_1124", 3, 302240),
            ("JCOM_001A_0323", 3, 291581),
            ("JCOM_002Y_0322", 8, 289194),
            ("JCOM_014N_1021", 4, 286002),
            ("JCOM_004A_0920", 5, 282895),
            ("JCOM_009A_0119", 3, 272016),
            ("JCOM_002N_0219", 5, 272910),
            ("JCOMAL_003A_0923", 4, 4701),
            ("JCOMAL_003A_0923", 4, 4702),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} AU_SENDS_CORRECT "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        author_send_corrections_date = self.action["actionDate"]
        author_annotation_data = self.read_author_annotation()

        # remember that TR are ordered by -round_number, so the "first" TR is the most recent one (!)
        current_round: TypesettingRound = self.article.typesettinground_set.first()
        old_typesetting_assignment: TypesettingAssignment = current_round.typesettingassignment
        author_proofs: GalleyProofing = current_round.galleyproofing_set.get()  # we always set at most 1 GP per round
        author_proofs.notes = author_annotation_data.get("documentLayerText")
        author_proofs.save()

        # get author anotation file if it exists
        if self.importfiles:
            (extension, author_annotation_file_dj) = self.get_author_annotation_file(
                author_annotation_data.get("documentLayerID")
            )
            if author_annotation_file_dj and extension:
                logger.debug(f"downloaded not empty annotation file {author_annotation_file_dj=}")
                author_annotation_file_dj.content_type = f"application/{extension}"

            if author_annotation_file_dj:
                author_ann_file = files.save_file_to_article(
                    author_annotation_file_dj,
                    self.article,
                    self.article.correspondence_author,
                )
                author_proofs.annotated_files.add(author_ann_file)

        fake_request = create_rich_fake_request(
            user=self.article.correspondence_author, journal=self.journal, settings=settings
        )
        with freezegun.freeze_time(
            rome_timezone.localize(author_send_corrections_date),
        ):
            AuthorSendsCorrections._log_operation = noop
            AuthorSendsCorrections(
                user=self.article.correspondence_author,
                old_assignment=old_typesetting_assignment,
                request=fake_request,
            ).run()

            message_subject = get_setting(
                setting_group_name="email_subject",
                setting_name="subject_notify_typesetter_proofing_changes",
                journal=self.article.journal,
            ).processed_value
            communication_utils.log_operation(
                article=self.article,
                message_subject=message_subject,
                message_body="",
                actor=self.article.correspondence_author,
                recipients=[
                    old_typesetting_assignment.typesetter,
                ],
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )

            self.article.refresh_from_db()

    def read_author_annotation(self):
        """Read author annotation of the imported version."""

        cursor_author_annotation = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # AUANN is only one in the wjapp version
        query_author_annotation = """
SELECT
dl.documentLayerCod,
dl.documentLayerID,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='AUANN'
AND ur.userType='author'
ORDER BY dl.submissionDate DESC
"""

        cursor_author_annotation.execute(
            query_author_annotation,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_author_annotation.rowcount == 0:
            logger.error(f"Found {cursor_author_annotation.rowcount} author annotation: {self.preprintid}")
            author_annotation = None
        else:
            # JCOM_023A_0623 has two AUANN in the same version (data error) in any case we take the
            # most recent one
            author_annotation = cursor_author_annotation.fetchone()
            logger.debug(f"{self.preprintid} AUANN: {author_annotation.get('documentLayerCod')}")
        cursor_author_annotation.close()

        return author_annotation

    def get_author_annotation_file(self, document_layer_id):
        """Returns annotation file if exists."""

        annotation_extension = self.extract_annotation_file_extension(document_layer_id)
        if not annotation_extension:
            logger.debug(f"AUANN file extension missing for {document_layer_id}")
            return (None, None)
        response_author_ann_file = self.download_author_annotation_file(document_layer_id, annotation_extension)
        if response_author_ann_file.headers["Content-Length"] != "0":
            return (
                annotation_extension,
                file_from_response(response_author_ann_file, f"{document_layer_id}.{annotation_extension}"),
            )
        else:
            logger.error(f"Empty AUANN file downloaded for {document_layer_id=}!")

    def extract_annotation_file_extension(self, document_layer_id):
        """Extract annotation file extension from compressed contribution zip., if exists.

        We either return the extension, meaning that an AUANN file exists and must be download,
        or we return None, meaning that no AUANN file exists and there it no need to download anything.
        """

        # returns the extension of the annotation file, if exists, taken it from the
        # production zip content list. The production zip contains, if it exists, the annotation directory and file.
        # e.g. JCOM_017A_0624:
        # if an AUANN file exists,
        # - it will be called JCOM_017A_0624_AUANN004381124.XXX
        #   where "JCOM_017A_0624_AUANN004381124" is the documentLayerID
        #   (do not confuse with documentLayerCod!)
        # - the path of the file (from the root of the production zip) will be:
        #   AUANN/JCOM_017A_0624_AUANN004381124/JCOM_017A_0624_AUANN004381124.XXX
        # We are interested in the "XXX" extension.
        # The file will be downloaded directly from the main directory, because we prefer not to use, if possible,
        # the production zip for the files, because in case of manual maintenance, the production zip
        # will probably be out of sync (even if we can assume that the extension we are interested in is ok)

        # production zip to download
        file_url = (
            f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
            f"production/{self.preprintid}.zip&fileType=zip"
        )

        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(
                f"check wjapp login credentials empty production zip: {response.headers['Content-Length']} {file_url=}"
            )
        try:
            files_zip = zipfile.ZipFile(BytesIO(response.content))
        except zipfile.BadZipFile:
            logger.error(f"file not open: {file_url}")
            return

        for filepath in files_zip.namelist():
            # We assume that the chance of find any other file in the production zip
            # named something similar to "...document_layer_id. ..." is minimal.
            # Note the ending "." in the f-string below:
            if f"{document_layer_id}." in filepath:
                _, extension = os.path.splitext(filepath)
                return extension.lstrip(".")

    def download_author_annotation_file(self, document_layer_id, annotation_extension):
        """Download author annotation file from AUANN directory using the extension found previously.

        Example url:
        JCOM_017A_0624/4/AUANN/JCOM_017A_0624_AUANN004381124/JCOM_017A_0624_AUANN004381124.zip&fileType=zip
        """

        filename_suffix = ""

        # specific cases correction for AUANN filename
        if document_layer_id == "JCOM_001Y_0624_AUANN006670724":
            filename_suffix = ".BVL proofed"

        if document_layer_id == "JCOM_001Y_0624_AUANN001100824":
            filename_suffix = ".proof 20240805.BVL correction"

        if document_layer_id == "JCOM_003N_0823_AUANN003910224":
            filename_suffix = ".-JCOM - Proofreading - 26-02-2024"

        if document_layer_id == "JCOM_013A_1021_AUANN004140722":
            filename_suffix = ".29.22"

        if document_layer_id == "JCOM_015A_1021_AUANN004030422":
            filename_suffix = ".22.22"

        if document_layer_id == "JCOM_002N_0619_AUANN002740819":
            filename_suffix = ".docx"

        if document_layer_id == "JCOM_003A_1217_AUANN002650518":
            filename_suffix = ".odt"

        if document_layer_id == "JCOM_005A_0916_AUANN001140317":
            filename_suffix = ". Number of views according to the established classification."

        if document_layer_id == "JCOM_002A_1215_AUANN000160816":
            filename_suffix = ".02.16"

        if document_layer_id == "JCOMAL_001A_0524_AUANN000691024":
            filename_suffix = ". Obra_ DAMIANA, créditos Fermín Bongiorno"

        file_url = (
            f"{self.url_base}{self.preprintid}/{self.imported_version_num}/AUANN/{document_layer_id}/"
            f"{document_layer_id}{filename_suffix}.{annotation_extension}&fileType={annotation_extension}"
        )

        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(
                f"check wjapp login credentials empty AUANN file downloaded: {response.headers['Content-Length']}"
            )
        return response


class PUM_SENDS_TO_TYP(BaseActionManager):  # noqa N801
    """Manages wjapp action "publication manager sends back to typesetter"."""

    # NOTE this action happens after ready for publication
    # e.g. JCOM_008A_0324

    def run(self):

        # for (JCOM_001A_0917, 4): the workaround is necessary for a "missing"
        # ready for publication in version 5, which creates a transition error
        # if there are two PUM_SENDS_TO_TYP in sequence

        noop_cases = {
            ("JCOM_001A_0917", 4, 266532),
            ("JCOM_001A_0917", 5, 266534),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PUM_SENDS_TO_TYP "
                f"for {self.preprintid}/{self.imported_version_num} (- known case)."
            )
            return

        # we need to get the admin user who did the action
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        back_to_typ_date = self.action["actionDate"]
        back_to_typesetter = self.read_pub_manager_annotation()

        old_typesetting_assignment = self.article.typesettinground_set.first().typesettingassignment
        assert back_to_typesetter
        body_pub_manager_annotation = back_to_typesetter["documentLayerText"]
        subject_pub_manager_annotation = back_to_typesetter["documentLayerSubject"]

        with freezegun.freeze_time(
            rome_timezone.localize(back_to_typ_date),
        ):
            HandleEOSendBackToTypesetter(
                workflow=self.article.articleworkflow,
                user=admin,
                old_assignment=old_typesetting_assignment,
                body=body_pub_manager_annotation,
                subject=subject_pub_manager_annotation,
            ).run()

    def read_pub_manager_annotation(self):
        """Read publication manager annotation of the imported version."""

        cursor_pub_manager_annotation = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # After wjapp action PUM_SENDS_TO_TYP the paper goes to the typesetter and when the typesetter uploads,
        # a new version is created. Therefore there is only one PUMANN in a version and the condition does not need
        # the agentCod or the actionDate
        query_pub_manager_annotation = """
SELECT
dl.documentLayerCod,
dl.documentLayerID,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
WHERE
    versioncod=%(imported_version_cod)s
AND dl.documentLayerType='PUMANN'
ORDER BY dl.submissionDate
"""

        cursor_pub_manager_annotation.execute(
            query_pub_manager_annotation,
            {
                "imported_version_cod": self.imported_version_cod,
            },
        )
        if cursor_pub_manager_annotation.rowcount != 1:
            logger.error(
                f"Found {cursor_pub_manager_annotation.rowcount} publication manager annotation: "
                f"{self.preprintid}/{self.imported_version_num}"
            )
            pub_manager_annotation = None
        else:
            pub_manager_annotation = cursor_pub_manager_annotation.fetchone()
            logger.debug(f"{self.preprintid} PUMANN: {pub_manager_annotation.get('documentLayerCod')}")
            self.imported_document_layer_cod_list.append(pub_manager_annotation.get("documentLayerCod"))

        cursor_pub_manager_annotation.close()

        return pub_manager_annotation


@dataclass
class DeclareReadyForPublication(BaseActionManager):
    """Manages wjapp action "article declared ready for publication"."""

    ready_for_publication_agent: Account = None

    def run(self):

        noop_cases = {
            ("JCOM_005A_0621", 4, 283684),
            ("JCOM_005A_0621", 4, 283685),
            ("JCOM_001N_0321", 6, 285546),
            ("JCOM_005A_1221", 6, 286343),
            ("JCOM_018A_1021", 3, 285172),
            ("JCOM_023A_1020", 3, 282974),
            ("JCOM_001BR_1018", 3, 270181),
            ("JCOM_001CR_0517", 3, 265365),
            ("JCOM_001BR_0616", 2, 262893),
            ("JCOM_001A_0917", 4, 266531),
            ("JCOM_011A_0123", 6, 291147),
            ("JCOM_005C_1118", 4, 270710),
            ("JCOMAL_001A_0622", 5, 3199),
            ("JCOMAL_001A_1224", 5, 5999),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping repeated action {self.action['actHistCod']} DeclareReadyForPublication "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated/wrong logic - known case)."
            )
            return

        self.article.articleworkflow.production_flag_no_queries = True
        self.article.articleworkflow.production_flag_no_checks_needed = True
        self.article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            # use default timeline message. Wjapp message imported as correspondence
            # e.g. JCOM_017A_0524
            ReadyForPublication(workflow=self.article.articleworkflow, user=self.ready_for_publication_agent).run()
        self.article.refresh_from_db()
        self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION


@dataclass
class PM_PUBLISHES(DeclareReadyForPublication):  # noqa N801
    """Manages wjapp action "eo declares ready for publication"."""

    def __post_init__(self):
        """Set the specific data for eo agent, used typesetter"""
        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        assert is_article_typesetter(self.article.articleworkflow, typesetting_assignment.typesetter)
        self.ready_for_publication_agent = typesetting_assignment.typesetter


@dataclass
class TYP_PUBLISHES(DeclareReadyForPublication):  # noqa N801
    """Manages wjapp action "typesetter declares ready for publication"."""

    def __post_init__(self):
        """Set the specific data typesetter agent"""
        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        assert is_article_typesetter(self.article.articleworkflow, typesetting_assignment.typesetter)
        self.ready_for_publication_agent = typesetting_assignment.typesetter


@dataclass
class AU_PUBLISHES(DeclareReadyForPublication):  # noqa N801
    """Manages wjapp action "author declares ready for publication"."""

    def __post_init__(self):
        """Set the specific data for author agent"""
        self.ready_for_publication_agent = self.article.correspondence_author


@dataclass
class PM_SENDS_TO_TYP(BaseActionManager):  # noqa N801
    """Manages wjapp action eo sends back to typesetter."""

    # this action is not in the logic of wjs. It is added a message in the timeline for the imported papers.
    # other related messages are imported with the general correspondence
    def run(self):
        # we need to get the admin user who did the action
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            communication_utils.log_operation(
                article=self.article,
                message_subject="back to typesetter",
                message_body="",
                recipients=[typesetting_assignment.typesetter],
                actor=admin,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )


@dataclass
class PM_PRE_PUBLISHES(BaseActionManager):  # noqa N801
    """Manages wjapp action Publication Manager prepares for publication.

    this action is not in the logic of wjs. It is added a message in the timeline for the imported papers.
    other related messages are imported with the general correspondence
    """

    def run(self):
        # we need to get the pm user who did the action
        pm_cod = self.action["agentCod"]
        pm_lastname = self.action["agentLastname"]
        pm_firstname = self.action["agentFirstname"]
        pm_email = self.action["agentEmail"]
        pm_privacy = self.action["agentPrivacy"]

        pm = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            pm_cod,
            pm_lastname,
            pm_firstname,
            pm_email,
            pm_privacy,
            self.connection,
        )

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            communication_utils.log_operation(
                article=self.article,
                message_subject="Publication Manager prepares for publication",
                message_body="",
                recipients=[pm],
                actor=pm,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )


@dataclass
class PUB_PUBLISHES(BaseActionManager):  # noqa N801
    """Manages wjapp action publisher publishes."""

    # this action is not in the logic of wjs. It is added a message in the timeline for the imported papers.
    # other related messages are imported with the general correspondence
    def run(self):
        # we need to get the pub user who did the action
        pub_cod = self.action["agentCod"]
        pub_lastname = self.action["agentLastname"]
        pub_firstname = self.action["agentFirstname"]
        pub_email = self.action["agentEmail"]
        pub_privacy = self.action["agentPrivacy"]

        pub = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            pub_cod,
            pub_lastname,
            pub_firstname,
            pub_email,
            pub_privacy,
            self.connection,
        )

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
            self.article.articleworkflow.save()
            self.article.stage = submission_models.STAGE_PUBLISHED
            self.article.save()
            communication_utils.log_operation(
                article=self.article,
                message_subject="publisher publishes",
                message_body="",
                recipients=[pub],
                actor=pub,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )


@dataclass
class ACC_CPRGHT_TRNSFR(BaseActionManager):  # noqa N801
    """Wjapp action to accept copyright after acceptance."""

    # this action which in wjapp is done after acceptance is not in the logic of wjs
    # where the copyright is accepted at submission
    def run(self):
        pass


@dataclass
class PUM_SENDS_TO_EO(BaseActionManager):  # noqa N801
    """Wjapp action to send article from publicaton manager to eo after ready for publication."""

    # PUM_SENDS_TO_EO in wjapp is done after ready for publication but does not
    # require typesetter upload or author sends proof. So in wjs must only reset
    # ready for publication to permit another ready for publication action
    # e.g. JCOM_015A_0224, JCOM_001E_0924
    def run(self):

        noop_cases = {
            ("JCOM_018A_1021", 4, 285245),
            ("JCOM_023A_1020", 4, 283085),
            ("JCOM_001CR_0517", 4, 265366),
            ("JCOM_001BR_0616", 3, 262894),
            ("JCOM_011A_0123", 7, 291170),
            ("JCOM_005C_1118", 5, 270711),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PUM_SENDS_TO_EO "
                f"for {self.preprintid}/{self.imported_version_num} ( - known case)."
            )
            return

        self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
        ta = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        ta.completed = None
        ta.save()


@dataclass
class PUB_REG_CREF_OK(BaseActionManager):  # noqa N801
    """Wjapp action Crossref notification (registration ok/error)."""

    # this action of wjapp is managed in other way in the logic of wjs
    # so is skipped. i.e. JCOM_006A_0418, JCOM_003C_0818
    def run(self):
        pass


@dataclass
class PUB_REG_CREF_REQ(BaseActionManager):  # noqa N801
    """Wjapp action Crossref notification (request)."""

    # this action of wjapp is managed in other way in the logic of wjs
    # so is skipped. i.e. JCOM_006A_1217
    def run(self):
        pass


@dataclass
class PUB_REG_CREF_ERR(BaseActionManager):  # noqa N801
    """Wjapp action Crossref notification (error)."""

    # this action of wjapp is managed in other way in the logic of wjs
    # so is skipped. i.e. JCOM_006A_1217
    def run(self):
        pass


@dataclass
class PM_REQ_ED_CHECK(BaseActionManager):  # noqa N801
    """Wjapp action EO requires editor check."""

    # This action of wjapp is not supported by wjs
    # but we need to deal with each single case,
    # because they may need special treatment in other places
    # (e.g. in the main loop, in ED_RESTARTS_REV, in TYP_UPLOADS_FOR_PM, etc.)
    def run(self):

        noop_cases = {
            ("JCOM_003A_0417", 3, 266298),
            ("JCOM_012A_0615", 3, 260997),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PM_REQ_ED_CHECK "
                f"for {self.preprintid}/{self.imported_version_num} (managed known case)."
            )
            pass
        else:
            logger.error(f"PM_REQ_ED_CHECK not managed for {self.preprintid} {self.article.id}")


@dataclass
class ED_RESTARTS_REV(BaseActionManager):  # noqa N801
    """Wjapp action editor does not approve author`s proof changes. Revision requested to author."""

    # This action of wjapp is not supported by wjs
    # but we need to deal with each single case,
    # because they may need special treatment in other places
    # (e.g. in the main loop, in ED_RESTARTS_REV, in TYP_UPLOADS_FOR_PM, etc.)
    def run(self):

        noop_cases = {("JCOM_003A_0417", 3, 266304), ("JCOM_012A_0615", 3, 261008)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ED_RESTARTS_REV "
                f"for {self.preprintid}/{self.imported_version_num} (managed known case)."
            )
            pass
        else:
            logger.error(f"ED_RESTARTS_REV not managed for {self.preprintid} {self.article.id}")


@dataclass
class PM_UPLOADS_FORMAT_V(BaseActionManager):  # noqa N801
    """Wjapp action PM uploads version."""

    # This action of wjapp is not supported by wjs
    # but we need to deal with each single case,
    # because they may need special treatment in other places
    # (e.g. in the main loop, in ED_RESTARTS_REV, in TYP_UPLOADS_FOR_PM, etc.)
    def run(self):

        noop_cases = {
            ("JCOM_027Y_0615", 4, 260632),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PM_UPLOADS_FORMAT_V "
                f"for {self.preprintid}/{self.imported_version_num} (managed known case)."
            )
            pass
        else:
            logger.error(f"PM_UPLOADS_FORMAT_V not managed for {self.preprintid} {self.article.id}")
