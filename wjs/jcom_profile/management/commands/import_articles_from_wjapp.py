"""Import article from wjapp."""

import datetime

import freezegun
import mariadb
from core.middleware import GlobalRequestMiddleware
from core.models import Account
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from identifiers import models as identifiers_models
from journal.models import Journal
from plugins.wjs_review.logic import (
    AssignToEditor,
    AssignToReviewer,
    EvaluateReview,
    render_template_from_setting,
)
from plugins.wjs_review.models import ArticleWorkflow
from review.models import EditorAssignment, ReviewRound
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

    def import_data_article(self, **options):
        """Process one article."""
        preprintid = self.options["preprintid"]
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

        connection = mariadb.connect(**connection_parameters)

        row = self.read_article_data(connection, preprintid)

        if not row:
            connection.close()
            logger.debug(f"Article not found {self.journal.code} {preprintid}.")
            return

        document_cod = row["documentCod"]
        preprintid = row["preprintId"]
        section = row["documentType"]
        version_cod = row["versionCod"]

        logger.debug(f"""Importing {preprintid}""")

        # create article and section
        article, preprintid = self.create_article(row)
        self.set_section(article, section)

        # article keywords
        keywords = self.read_article_keywords(connection, version_cod)
        self.set_keywords(article, keywords)

        # read all version versionNum, versionCod
        versions = self.read_versions_data(connection, document_cod)

        # editor selection is done while reading the history
        editor = None
        editor_maxworkload = None

        # In wjapp, the concept of version is paramount. All actions revolve around versions.
        # Here we cycle through each version and manage the data that need.
        for v in versions:
            imported_version_cod = v["versionCod"]
            imported_version_num = v["versionNumber"]
            # create review round (we are mapping version to review-round)
            review_round, _ = ReviewRound.objects.get_or_create(article=article, round_number=imported_version_num)
            # TBD: in general, should we set review_round.date_started? can we do it?
            # TODO: can we set review_round.date_started = a[actionDate] if review_round.number == 1 ?
            logger.debug(f"Creating {review_round.round_number=} for {imported_version_num=}")

            # read actions history from wjapp preprint
            history = self.read_history_data(connection, imported_version_cod)

            for action in history:
                logger.debug(f"Looking at action {action['actionID']} ({action['actHistCod']})")

                if action["actionID"] == "SYS_ASS_ED" or action["actionID"] == "ED_SEL_N_ED":
                    # these map (roughly) to EditorAssignment
                    editor_cod = action["userCod"]
                    editor_lastname = action["lastname"]
                    editor_firstname = action["firstname"]
                    editor_email = action["email"]
                    editor_assign_date = action["actionDate"]
                    editor_maxworkload = action["editorWorkload"]

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
                    editor_parameters = self.read_editor_parameters(connection, editor_cod)
                    self.set_editor_parameters(article, editor, editor_maxworkload, editor_parameters)

                if action["actionID"] == "ED_ASS_REF" or action["actionID"] == "ED_ADD_REF":
                    # these map (roughly) to ReviewAssignment
                    # (review assignments are created onto the current review round; see external loop on versions)

                    # editor must already be set
                    if not editor:
                        logger.error(f"editor not set for {preprintid} {article.id}")
                        connection.close()
                        return

                    # reviewer data from Current_Referees
                    reviewer_data = self.read_reviewer_data(connection, imported_version_cod, action["userCod"])

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
                            "refereeCod": action["userCod"],
                            "refereeLastName": action["lastname"],
                            "refereeFirstName": action["firstname"],
                            "refereeEmail": action["email"],
                            "refereeAssignDate": action["actionDate"],
                            "report_due_date": None,
                            "refereeAcceptDate": None,
                        }

                    self.set_reviewer(article, editor, reviewer_data)

        connection.close()

    #
    # functions to read data from wjapp
    #

    def read_article_data(self, connection, preprintid):
        """Read article main data."""
        cursor_article = connection.cursor(dictionary=True)
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

    def read_article_keywords(self, connection, version_cod):
        """Read article keywords."""
        cursor_keywords = connection.cursor(dictionary=True)
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

    def read_versions_data(self, connection, document_cod):
        """Read article versions data."""
        cursor_versions = connection.cursor(dictionary=True)
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

    def read_history_data(self, connection, imported_version_cod):
        """Read history data."""
        cursor_history = connection.cursor(dictionary=True)
        query_history = """
SELECT
ah.actHistCod,
ah.versionCod,
ah.actionCod,
ah.agentCod,
ah.userCod,
u.lastname,
u.firstname,
u.email,
u.editorWorkload,
ah.realAgentCod,
ah.actionDate,
a.actionID
FROM Action_History ah
LEFT JOIN Action a USING (actionCod)
LEFT JOIN User u ON (u.userCod=ah.userCod)
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

    def read_editor_parameters(self, connection, editor_cod):
        """Read editor parameters."""
        cursor_editor_parameters = connection.cursor(dictionary=True)
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

    def read_reviewer_data(self, connection, imported_version_cod, user_cod):
        """Read reviewer data."""
        cursor_reviewer = connection.cursor(dictionary=True)
        query_reviewer = """
SELECT
refereeCod,
u.lastName  AS refereeLastName,
u.firstName AS refereeFirstName,
u.email     AS refereeEmail,
assignDate  AS refereeAssignDate,
refereeReportDeadlineDate AS report_due_date,
IF(YEAR(acceptDate)!=1970, acceptDate, "") AS refereeAcceptDate
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
            article.manuscript_files.all().delete()
            article.data_figure_files.all().delete()
            article.supplementary_files.all().delete()
            article.source_files.all().delete()
            article.galley_set.all().delete()
            article.delete()

        article = submission_models.Article.objects.create(
            journal=self.journal,
        )
        article.title = row["versionTitle"]
        article.abstract = row["versionAbstract"]
        article.imported = True
        # date str ex: 2024-03-29 10:51:52,406
        date_string = str(row["submissionDate"])
        article.date_submitted = rome_timezone.localize(datetime.datetime.fromisoformat(date_string))
        article.save()
        main_author = self.account_get_or_create_check_correspondence(
            row["authorCod"],
            row["author_lastname"],
            row["author_firstname"],
            row["author_email"],
        )
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
        return (article, preprintid)

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
        request.user = editor

        with freezegun.freeze_time(
            rome_timezone.localize(datetime.datetime.fromisoformat(str(editor_assign_date))),
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

    def set_reviewer(self, article, editor, reviewer_data):
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
            rome_timezone.localize(datetime.datetime.fromisoformat(str(reviewer_data["refereeAssignDate"]))),
        ):
            # default message from settings
            # TODO: verify mail subject exists
            # TODO: verify signature in the final message request.user.signature is not missing
            default_message_rendered = render_template_from_setting(
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
            interval_days = get_setting(
                "wjs_review",
                "acceptance_due_date_days",
                self.journal,
            )
            date_due = timezone.now().date() + datetime.timedelta(days=interval_days.process_value())
            form_data = {
                "acceptance_due_date": date_due,
                "message": default_message_rendered,
            }
            review_assignment = AssignToReviewer(
                reviewer=reviewer,
                workflow=article.articleworkflow,
                editor=editor,
                form_data=form_data,
                request=request,
            ).run()

            if reviewer_data["refereeAcceptDate"]:
                request = create_fake_request(user=None, journal=self.journal)
                request.user = reviewer

                with freezegun.freeze_time(
                    rome_timezone.localize(datetime.datetime.fromisoformat(str(reviewer_data["refereeAcceptDate"]))),
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
                            datetime.datetime.fromisoformat(str(reviewer_data["report_due_date"])),
                        )
                        # note: review_assignment date_due is datetime.date not datetime.datetime
                        review_assignment.date_due = datetime_due.date()
                        review_assignment.save()

        return review_assignment
