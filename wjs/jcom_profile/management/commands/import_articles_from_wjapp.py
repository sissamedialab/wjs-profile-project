"""Import article from wjapp."""

import datetime
from io import BytesIO

import freezegun
import mariadb
import requests
from core import files
from core.middleware import GlobalRequestMiddleware
from core.models import Account
from django.conf import settings
from django.core.files import File as DjangoFile
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from identifiers import models as identifiers_models
from journal.models import Journal
from plugins.wjs_review.forms import ReportForm, ReviewForm, ReviewFormElement
from plugins.wjs_review.logic import (
    AssignToEditor,
    AssignToReviewer,
    AuthorHandleRevision,
    EditorRevisionRequest,
    EvaluateReview,
    HandleDecision,
    SubmitReview,
    WorkflowReviewAssignment,
    render_template_from_setting,
)
from plugins.wjs_review.models import ArticleWorkflow, EditorDecision
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


class UnknownSection(Exception):
    """Unknown section / article-type."""


logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Connect to wjApp jcom database and read article data."  # NOQA A003

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

        # editor selection is done while reading the history
        editor = None
        editor_maxworkload = None

        # In wjapp, the concept of version is paramount. All actions revolve around versions.
        # Here we cycle through each version and manage the data that need.
        for v in versions:
            imported_version_cod = v["versionCod"]
            imported_version_num = v["versionNumber"]

            # TEST IMPORT: for JCOM_003N_0623
            #             with version 5 accepted editor report not visible on author page
            if preprintid == "JCOM_003N_0623" and v["versionNumber"] > 4:
                logger.error(f"TEST: forced stop before JCOM_003N_0623 version {v['versionNumber']}")
                break

            # read actions history from wjapp preprint
            history = self.read_history_data(imported_version_cod)

            for action in history:
                logger.debug(f"Looking at action {action['actionID']} ({action['actHistCod']})")

                if action["actionID"] in ("SYS_ASS_ED", "ED_SEL_N_ED", "ADMIN_ASS_N_ED"):
                    # these map (roughly) to EditorAssignment
                    editor_cod = action["targetCod"]
                    editor_lastname = action["targetLastname"]
                    editor_firstname = action["targetFirstname"]
                    editor_email = action["targetEmail"]
                    editor_assign_date = action["actionDate"]
                    editor_maxworkload = action["targetEditorWorkload"]

                    # there are wjapp actions SYS_ASS_ED with editor assigned None
                    # example: JCOM_003A_0424 version 2
                    if editor_cod:
                        # editor assignment
                        editor = self.set_editor(
                            article,
                            editor_cod,
                            editor_lastname,
                            editor_firstname,
                            editor_email,
                            editor_assign_date,
                        )

                        # editor parameters
                        editor_parameters = self.read_editor_parameters(editor_cod)
                        self.set_editor_parameters(article, editor, editor_maxworkload, editor_parameters)

                        if action["actionID"] == "SYS_ASS_ED":
                            # TODO: import files must be done not only for this case but for each new wjapp version
                            # TODO: import files must be extended to wjapp source zip/targz file and attachments
                            if self.options["importfiles"]:
                                response = self.download_manuscript_version(session, imported_version_num, preprintid)
                                self.save_manuscript(preprintid, article, response)

                if action["actionID"] in ("ED_ASS_REF", "ED_ADD_REF"):
                    # these map (roughly) to ReviewAssignment
                    # (review assignments are created onto the current review round; see external loop on versions)

                    # TODO: refactor to avoid to repeat this fragment. Check that the editor must already be set
                    if not editor:
                        logger.error(f"editor not set for {preprintid} {article.id}")
                        self.connection.close()
                        return

                    # reviewer data from Current_Referees
                    reviewer_data = self.read_reviewer_data(imported_version_cod, action["targetCod"])

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
                            "refereeCod": action["targetCod"],
                            "refereeLastName": action["targetLastname"],
                            "refereeFirstName": action["targetFirstname"],
                            "refereeEmail": action["targetEmail"],
                            "refereeAssignDate": action["actionDate"],
                            "report_due_date": None,
                            "refereeAcceptDate": None,
                        }

                    # select reviewer message
                    reviewer_message = self.read_reviewer_message(
                        imported_version_cod, action["targetCod"], preprintid, action["actionDate"]
                    )
                    logger.debug(f"Reviewer message: {reviewer_message.get('documentLayerCod')}")

                    self.set_reviewer(article, editor, reviewer_data, reviewer_message)

                if action["actionID"] in ("EQ1_REF_REF", "GT1_REF_REF", "REF_REF"):
                    # wjapp actions for referee declined assignment for preprintid in wjapp:

                    # - EQ1_REF_REF: this action indicates that a referee declined an assignment on a
                    #   paper with exactly one referee (i.e. the paper has no more active review assignments)

                    # - GT1_REF_REF: this action indicates that a referee declined an assignment on a
                    #   paper with more than one referee (i.e. the paper has still active review assignments)

                    # - REF_REF:  this action indicates that a referee declined an assignment.
                    #   It is present in the wjapp code and Action table, but seems not used.
                    #   Probably has been replaced by the two above. Added for completeness

                    # TODO: refactor to avoid to repeat this fragment. Check that the editor must already be set
                    if not editor:
                        logger.error(f"editor not set for {preprintid} {article.id}")
                        self.connection.close()
                        return

                    reviewer_decline_message = self.read_reviewer_decline_message(
                        imported_version_cod, action["agentCod"], preprintid, action["actionDate"]
                    )

                    self.reviewer_declines(
                        article,
                        editor,
                        action["agentCod"],
                        action["agentLastname"],
                        action["agentFirstname"],
                        action["agentEmail"],
                        action["actionDate"],
                        reviewer_decline_message,
                    )

                if action["actionID"] == "REF_SENDS_REP":
                    # Reviewer send report

                    # TODO: refactor to avoid to repeat this fragment. Check that the editor must already be set
                    if not editor:
                        logger.error(f"editor not set for {preprintid} {article.id}")
                        self.connection.close()
                        return

                    wjapp_reviewer_report = self.read_reviewer_report_message(
                        imported_version_cod, action["agentCod"], preprintid, action["actionDate"]
                    )
                    self.reviewer_send_report(
                        article,
                        editor,
                        action["agentCod"],
                        action["agentLastname"],
                        action["agentFirstname"],
                        action["agentEmail"],
                        action["actionDate"],
                        wjapp_reviewer_report,
                    )

                if action["actionID"] in ("ED_REQ_REV", "ED_ACC_DOC_WMC", "ED_REJ_DOC"):
                    # wjs editor report store:
                    #
                    # - for ED_REQ_REV, ED_ACC_DOC_WMC
                    #     the EDREP is visible for the author on revision request page
                    #        the view is ArticleRevisionUpdate based on model EditorRevisionRequest
                    #         the templates are
                    #            "wjs/themes/JCOM-theme/templates/admin/review/revision/do_revision.
                    #            --> wjs_review/elements/revision_author_info.html
                    #
                    # - for ED_REJ_DOC the EDREP is NOT visible for the author
                    #
                    # - all editor reports are stored in EditorDecision.decision_editor_report
                    #
                    # - editor reports with revision request are stored also in EditorRevisionRequest

                    if action["actionID"] == "ED_REQ_REV":
                        # Editor requires major revision
                        editor_decision = ArticleWorkflow.Decisions.MAJOR_REVISION
                        requires_revision = True
                        revision_interval_days = get_setting(
                            "wjs_review",
                            "default_author_major_revision_days",
                            self.journal,
                        ).process_value()

                    if action["actionID"] == "ED_ACC_DOC_WMC":
                        # Editor requires minor revision
                        editor_decision = ArticleWorkflow.Decisions.MINOR_REVISION
                        requires_revision = True
                        revision_interval_days = get_setting(
                            "wjs_review",
                            "default_author_minor_revision_days",
                            self.journal,
                        ).process_value()

                    # when rejected the article is no more visible in pending
                    if action["actionID"] == "ED_REJ_DOC":
                        # Editor requires minor revision
                        editor_decision = ArticleWorkflow.Decisions.REJECT
                        requires_revision = False
                        revision_interval_days = 0

                    # TODO: refactor to avoid to repeat this fragment. Check that the editor must already be set
                    if not editor:
                        logger.error(f"editor not set for {preprintid} {article.id}")
                        self.connection.close()
                        return

                    wjapp_editor_report = self.read_editor_report_message(
                        imported_version_cod, action["agentCod"], preprintid, action["actionDate"]
                    )

                    revision = self.editor_decides(
                        article,
                        editor,
                        editor_decision,
                        action["actionDate"],
                        wjapp_editor_report,
                        requires_revision,
                        revision_interval_days,
                    )
                    if requires_revision:
                        logger.debug(f"editor decision with revision request EditorRevisionRequest: {revision=}")

                if action["actionID"] in ("AU_SUB_REV", "AU_SUB_REV_WMC"):
                    # TBV: author of the action is the same of main_author?
                    #      the author could be switched with coauthor
                    self.author_submit_revision(
                        main_author,
                        article,
                        action["actionDate"],
                    )

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
versionNumber
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

    def read_editor_parameters(self, editor_cod):
        """Read editor parameters."""
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

    def read_reviewer_data(self, imported_version_cod, user_cod):
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
                "imported_version_cod": imported_version_cod,
                "user_cod": user_cod,
            },
        )
        reviewer_data = cursor_reviewer.fetchone()
        cursor_reviewer.close()
        return reviewer_data

    def read_reviewer_message(self, imported_version_cod, user_cod, preprintid, action_date):
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
                "imported_version_cod": imported_version_cod,
                "user_cod": user_cod,
                "action_date": str(action_date),
            },
        )
        if cursor_reviewer_message.rowcount != 1:
            logger.error(f"Found {cursor_reviewer_message.rowcount} reviewer assignment messages: {preprintid}")
            reviewer_message = None
        else:
            reviewer_message = cursor_reviewer_message.fetchone()
        cursor_reviewer_message.close()
        return reviewer_message

    def read_reviewer_decline_message(self, imported_version_cod, agent_cod, preprintid, action_date):
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
                "imported_version_cod": imported_version_cod,
                "agent_cod": agent_cod,
                "action_date": str(action_date),
            },
        )
        if cursor_reviewer_decline_message.rowcount != 1:
            logger.error(f"Found {cursor_reviewer_decline_message.rowcount} reviewer decline messages: {preprintid}")
            reviewer_decline_message = None
        else:
            reviewer_decline_message = cursor_reviewer_decline_message.fetchone()
        cursor_reviewer_decline_message.close()
        return reviewer_decline_message

    def read_reviewer_report_message(self, imported_version_cod, agent_cod, preprintid, action_date):
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
                "imported_version_cod": imported_version_cod,
                "agent_cod": agent_cod,
                "action_date": str(action_date),
            },
        )
        if cursor_reviewer_report_message.rowcount != 1:
            logger.error(f"Found {cursor_reviewer_report_message.rowcount} reviewer report: {preprintid}")
            reviewer_report_message = None
        else:
            reviewer_report_message = cursor_reviewer_report_message.fetchone()
        cursor_reviewer_report_message.close()
        return reviewer_report_message

    def read_editor_report_message(self, imported_version_cod, agent_cod, preprintid, action_date):
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
                "imported_version_cod": imported_version_cod,
                "agent_cod": agent_cod,
                "action_date": str(action_date),
            },
        )
        if cursor_editor_report_message.rowcount != 1:
            logger.error(f"Found {cursor_editor_report_message.rowcount} reviewer report: {preprintid}")
            editor_report_message = None
        else:
            editor_report_message = cursor_editor_report_message.fetchone()
            logger.debug(f"{preprintid} EDREP: {editor_report_message.get('documentLayerCod')}")
        cursor_editor_report_message.close()
        return editor_report_message

    def download_manuscript_version(self, session, imported_version_num, preprintid):
        """Download pdf manuscript for imported version."""
        # TODO: move url to plugin settings (depends on journal)?
        # authorised request.
        # ex: https://jcom.sissa.it/jcom/common/archiveFile?
        #    filePath=JCOM_003N_0623/5/JCOM_003N_0623.pdf&fileType=pdf
        url_base = "https://jcom.sissa.it/jcom/common/archiveFile?filePath="
        file_url = f"{url_base}{preprintid}/{imported_version_num}/{preprintid}.pdf&fileType=pdf"
        logger.debug(f"{file_url=}")

        response = session.get(file_url)

        assert response.status_code == 200, f"Got {response.status_code}!"

        if response.headers["Content-Length"] == "0":
            logger.error(f"check wjapp login credentials empty file downloaded: {response.headers['Content-Length']}")

        return response

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

            article.manuscript_files.all().delete()
            article.data_figure_files.all().delete()
            article.supplementary_files.all().delete()
            article.source_files.all().delete()
            article.galley_set.all().delete()
            article.delete()

            assert RevisionRequest.objects.filter(article=article).count() == 0
            assert ReviewAssignment.objects.filter(article=article).count() == 0
            assert ReviewRound.objects.filter(article=article).count() == 0

        article = submission_models.Article.objects.create(
            journal=self.journal,
        )
        article.title = row["versionTitle"]
        article.abstract = row["versionAbstract"]
        article.imported = True
        article.date_submitted = rome_timezone.localize(row["submissionDate"])
        article.save()
        main_author = self.account_get_or_create_check_correspondence(
            row["authorCod"],
            row["author_lastname"],
            row["author_firstname"],
            row["author_email"],
        )

        if not main_author.check_role(self.journal, "author"):
            main_author.add_account_role("author", self.journal)
        article.owner = main_author
        article.authors.add(main_author)
        article.correspondence_author = main_author
        article.save()

        identifiers_models.Identifier.objects.get_or_create(
            identifier=preprintid,
            article=article,
            id_type="preprintid",  # NOT a member of the set identifiers_models.IDENTIFIER_TYPES
            enabled=True,
        )
        logger.debug(f"Set preprintid {preprintid} onto {article.pk}")
        article.refresh_from_db()
        return (article, preprintid, main_author)

    # TODO: check why new review_round is not created
    def set_editor(self, article, editor_cod, editor_lastname, editor_firstname, editor_email, editor_assign_date):
        """Assign the editor.

        Also create the editor's Account if necessary.
        """
        editor = self.account_get_or_create_check_correspondence(
            editor_cod,
            editor_lastname,
            editor_firstname,
            editor_email,
        )

        # An account must have the "section-editor" role on the journal to be able to be assigned as editor of an
        # article.
        if not editor.check_role(self.journal, "section-editor"):
            editor.add_account_role("section-editor", self.journal)

        logger.debug(f"Assigning {editor.last_name} {editor.first_name} onto {article.pk}")

        # TODO: we need a function in the logic to reassign a new editor to the article.
        #       As temporary replacement we delete the editor assignments for the article
        EditorAssignment.objects.filter(article=article).delete()

        # Manually move into a state where editor assignment can take place
        # TODO: check if this is not the case already...
        article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        article.articleworkflow.save()

        request = create_fake_request(user=None, journal=self.journal)
        GlobalRequestMiddleware.process_request(request)

        with freezegun.freeze_time(
            rome_timezone.localize(editor_assign_date),
        ):
            AssignToEditor(
                article=article,
                editor=editor,
                request=request,
            ).run()
            article.save()
        article.refresh_from_db()
        return editor

    def account_get_or_create_check_correspondence(self, user_cod, last_name, first_name, imported_email):
        """Get a user account - check Correspondence and eventually create new account."""
        # Check if we know this person form some other journal or by email
        source = self.journal.code.lower()
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
                f"WJS mapping exists ({mappings.count()} correspondences)"
                f" for {user_cod}/{source} or {imported_email}",
            )
            mapping = check_mappings(mappings, imported_email, user_cod, source)

        account = mapping.account

        # `used` indicates that this usercod from this source
        # has been used to create the core.Account record
        if account_created:
            mapping.used = True
            mapping.save()

        return account

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

    def set_editor_parameters(self, article: submission_models.Article, editor, editor_maxworkload, editor_parameters):
        """Set the editor parameters.

        - max-workload (EditorAssignmentParameters workload)
        - keyword      (EditorKeyword into EditorAssignmentParameters keywords)
        - kwd weight   (EditorKeyword weight)
        """
        if editor_parameters:
            assignment_parameters, eap_created = wjs_models.EditorAssignmentParameters.objects.get_or_create(
                editor=editor,
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
                    f'Created keyword "{kwd_word}" for editor {editor}. Please check!',
                )
            wjs_models.EditorKeyword.objects.create(
                editor_parameters=assignment_parameters,
                keyword=keyword,
                weight=kwd_weight,
            )

        return

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

    def set_reviewer(self, article, editor, reviewer_data, reviewer_message):
        """Set a reviewer."""
        reviewer = self.account_get_or_create_check_correspondence(
            reviewer_data["refereeCod"],
            reviewer_data["refereeLastName"],
            reviewer_data["refereeFirstName"],
            reviewer_data["refereeEmail"],
        )
        logger.debug(f"Creating review assignment of {article.id} to reviewer {reviewer}")

        request = create_fake_request(user=None, journal=self.journal)
        request.user = editor

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
                    setting_name="review_invitation_message",
                    journal=self.journal,
                    request=request,
                    context={
                        "article": article,
                        "request": request,
                    },
                    template_is_setting=True,
                )
                logger.warning(f"used default reviewer message {reviewer=} {article=} {editor=}")

            form_data = {
                "acceptance_due_date": date_due,
                "message": message,
            }
            review_assignment = AssignToReviewer(
                reviewer=reviewer,
                workflow=article.articleworkflow,
                editor=editor,
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
                        editor=editor,
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

    def reviewer_declines(
        self,
        article,
        editor,
        reviewer_cod,
        reviewer_lastname,
        reviewer_firstname,
        reviewer_email,
        reviewer_declines_date,
        reviewer_decline_message,
    ):
        """Reviewer declines."""
        reviewer = self.account_get_or_create_check_correspondence(
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
        review_assignment = WorkflowReviewAssignment.objects.get(
            reviewer=reviewer, article=article, editor=editor, review_round=article.current_review_round_object()
        )

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_declines_date),
        ):
            logger.debug(f"Reviewer {reviewer} declines {article}")

            decline_reason = newlines_text_to_html(reviewer_decline_message.get("documentLayerText"))
            self.imported_document_layer_cod_list.append(reviewer_decline_message.get("documentLayerCod"))
            logger.debug(f"append reviewer decline message: {self.imported_document_layer_cod_list=}")

            # TODO: add in the logic the management of decline_reason
            EvaluateReview(
                assignment=review_assignment,
                reviewer=reviewer,
                editor=editor,
                form_data={"reviewer_decision": "0", "decline_reason": decline_reason, "accept_gdpr": True},
                request=request,
                token=None,
            ).run()

        return

    def reviewer_send_report(
        self,
        article,
        editor,
        reviewer_cod,
        reviewer_lastname,
        reviewer_firstname,
        reviewer_email,
        reviewer_report_date,
        wjapp_report,
    ):
        """Reviewer sends report."""
        reviewer = self.account_get_or_create_check_correspondence(
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
        )

        review_assignment = WorkflowReviewAssignment.objects.get(
            reviewer=reviewer,
            article=article,
            editor=editor,
            review_round=article.current_review_round_object(),
        )
        request = create_fake_request(user=None, journal=self.journal)
        request.user = reviewer
        submit_final = True

        cover_letter_review_form_element = ReviewFormElement.objects.get(
            name="Cover letter for the Editor (confidential)",
        )

        report_review_form_element = ReviewFormElement.objects.get(
            name="Report text (to be sent to authors)",
        )

        # the (current) default review form element "Cover letter for the Editor (confidential)"
        # has a rich-text/html widget.  Text from wjapp formatted to html
        formatted_cover_letter_message = newlines_text_to_html(wjapp_report.get("documentLayerText"))

        # the (current) default review form element "Report text (to be sent to authors)"
        # has a rich-text/html widget. Text from wjapp formatted to html
        formatted_report_message = newlines_text_to_html(wjapp_report.get("documentLayerOnlyTex"))

        current_setting = get_setting(
            "general",
            "default_review_form",
            self.journal,
        ).value
        review_form = ReviewForm.objects.get(pk=current_setting)
        required = review_form.elements.filter(required=True)
        for e in required:
            logger.debug(f"review form element not set: {e.name}")

        form = ReportForm(
            data={
                str(cover_letter_review_form_element.pk): formatted_cover_letter_message,
                str(report_review_form_element.pk): formatted_report_message,
            },
            review_assignment=review_assignment,
            fields_required=True,
            submit_final=submit_final,
            request=request,
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

    def editor_decides(
        self,
        article,
        editor,
        editor_decision,
        editor_report_date,
        wjapp_editor_report,
        requires_revision,
        revision_interval_days,
    ):
        """Editor decides on article."""

        # the (current) default review form element for editor cover letter
        # has a rich-text/html widget.  Text from wjapp formatted to html
        wjapp_editor_cover_letter_message = newlines_text_to_html(wjapp_editor_report.get("documentLayerText"))

        # the (current) default review form element for editor report
        # has a rich-text/html widget.  Text from wjapp formatted to html
        wjapp_editor_report_message = newlines_text_to_html(wjapp_editor_report.get("documentLayerOnlyTex"))

        request = create_fake_request(user=None, journal=self.journal)
        request.user = editor

        with freezegun.freeze_time(
            rome_timezone.localize(editor_report_date),
        ):
            date_due = timezone.now().date()
            if requires_revision:
                date_due = date_due + datetime.timedelta(days=revision_interval_days)

            # TBV: date_due has to be set in the form in the case of rejection?
            form_data = {
                "decision": editor_decision,
                "decision_editor_report": wjapp_editor_report_message,
                "decision_internal_note": wjapp_editor_cover_letter_message,
                "withdraw_notice": "notice",
                "date_due": date_due,
            }

            handle = HandleDecision(
                workflow=article.articleworkflow,
                form_data=form_data,
                user=editor,
                request=request,
            )
            handle.run()
        article.refresh_from_db()
        revision = None
        if requires_revision:
            revision = EditorRevisionRequest.objects.get(
                article=article, review_round=article.current_review_round_object()
            )
        return revision

    def author_submit_revision(
        self,
        main_author,
        article,
        author_report_date,
    ):
        # TBV: necessary to mark_all_messages_read  ??

        request = create_fake_request(user=None, journal=self.journal)
        request.user = main_author

        # TODO: to be read from wjapp jcom
        form_data = {"author_note": "covering letter from the author (the text-field, not the file!)"}

        revision_request = EditorRevisionRequest.objects.get(
            article=article, review_round=article.current_review_round_object()
        )

        with freezegun.freeze_time(
            rome_timezone.localize(author_report_date),
        ):
            service = AuthorHandleRevision(
                revision=revision_request,
                form_data=form_data,
                user=main_author,
                request=request,
            )
            service.run()

        article.refresh_from_db()

        return

    def save_manuscript(self, preprintid, article, response):
        """Save manuscript from response"""
        manuscript_dj = DjangoFile(BytesIO(response.content), f"{preprintid}.pdf")

        manuscript_file = files.save_file_to_article(
            manuscript_dj,
            article,
            article.correspondence_author,
        )
        article.manuscript_files.add(manuscript_file)
        manuscript_file.label = "PDF"
        manuscript_file.description = ""
        manuscript_file.save()
        article.save()
        return


#
# utility
#


def newlines_text_to_html(message: str) -> str:
    """Format Document_Layer message read from wjapp."""
    # TBV: format other new-line styles?
    #      Document_Layer messages/report from jcom jcomal are text message
    return message.replace("\r\n", "<br/>")
