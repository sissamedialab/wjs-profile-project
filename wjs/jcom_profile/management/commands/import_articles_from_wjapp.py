"""Import article from wjapp."""

# profiling
import cProfile
import datetime
import io
import pstats
import sys
import traceback

import freezegun
import mariadb
import requests
from core.models import Account
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from identifiers import models as identifiers_models
from journal.models import Issue, IssueType, Journal
from plugins.wjs_review.logic import (
    EditorRevisionRequest,
    PermissionAssignment,
    WorkflowReviewAssignment,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    EditorDecision,
    Message,
    PastEditorAssignment,
)
from review.models import (
    EditorAssignment,
    ReviewAssignment,
    ReviewRound,
    RevisionRequest,
)
from submission import models as submission_models
from submission.models import Licence
from typesetting.models import TypesettingAssignment, TypesettingRound

import wjs.jcom_profile.import_file_manager as import_file_manager
import wjs.jcom_profile.import_logic as import_logic
from wjs.jcom_profile import constants
from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_utils import (
    JANEWAY_LANGUAGES_BY_CODE,
    JOURNALS_DATA,
    NON_PEER_REVIEWED,
    SECTIONS_MAPPING,
    rome_timezone,
    set_author_country,
    sync_frozen_authors_with_authors,
)


def profile_command(func):
    """Decorator to profile the management command"""

    def wrapper(self, *args, **options):
        if options.get("profile"):
            pr = cProfile.Profile()
            pr.enable()

            try:
                result = func(self, *args, **options)
            finally:
                pr.disable()

                # define profiling result
                s = io.StringIO()
                ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
                ps.print_stats()

                # save to file
                filename = f"command_profile_{options.get('preprintid')}.prof"
                with open(f"/tmp/{filename}", "w") as f:
                    f.write(s.getvalue())

                self.stdout.write(self.style.SUCCESS(f"Profiling saved in {filename}"))

            return result
        else:
            return func(self, *args, **options)

    return wrapper


class UnknownSection(Exception):
    """Unknown section / article-type."""


class Command(BaseCommand):
    help = "Connect to wjApp jcom database and read article data."  # noqa A003

    @profile_command
    def handle(self, *args, **options):
        """Command entry point."""

        if not getattr(settings, "NO_NOTIFICATION", None):
            self.stderr.write(
                """Notifications are enabled, not importing to avoid spamming. Please set `NO_NOTIFICATION = True`
                in your django settings to proceed.""",
            )
            sys.exit(1)
        self.options = options

        if self.options["preprintid"].startswith("JHEP"):
            journal_code = "JHEP"
        elif self.options["preprintid"].startswith("JCAP"):
            journal_code = "JCAP"
        else:
            journal_code = self.options["preprintid"].split("_")[0].upper()
        if journal_code in ("JCOM", "JCOMAL", "JHEP", "JCAP"):
            self.journal = Journal.objects.get(code=journal_code)
            self.journal_data = JOURNALS_DATA[journal_code]
            exitcode = self.import_data_article(**options)
            if exitcode != 0:
                import_logic.logger.error(f"import_data_article exitcode={exitcode} quitting")
                sys.exit(1)
            else:
                return
        else:
            import_logic.logger.error(
                f"Journal not identified from {self.options['preprintid']} {journal_code}. Please check."
            )
            sys.exit(1)

    def add_arguments(self, parser):
        """Add arguments to command."""

        parser.add_argument(
            "--profile",
            action="store_true",
            help="activates the profiling of the command",
        )

        parser.add_argument(
            "--preprintid",
            default="",
            help="jcom/jcomal wjApp preprintid ex: JCOM_010A_0324",
            required=True,
        )
        parser.add_argument(
            "--importfilesweb",
            default=False,
            action="store_true",
            help="also downloads files from wjapp jcom/jcomal",
            required=False,
        )
        parser.add_argument(
            "--importfilesfake",
            default=False,
            action="store_true",
            help="create only empty fake files for the article",
            required=False,
        )
        parser.add_argument(
            "--importfilesarchive",
            default=False,
            action="store_true",
            help="import files from the archive on the file system",
            required=False,
        )

    def import_data_article(self, **options):
        """Process one article."""

        session = None

        if self.options["importfilesarchive"] and self.journal.code.upper() not in ["JCAP"]:
            import_logic.logger.error(f"option import_files from archive is not enabled for {self.journal.code}.")
            return 1

        if self.options["importfilesfake"] and self.journal.code.upper() not in ["JHEP"]:
            import_logic.logger.error(
                f"option import fake files is not enabled for {self.journal.code}.",
            )
            return 1

        if self.options["importfilesweb"] and self.journal.code.upper() not in ["JCOM", "JCOMAL"]:
            import_logic.logger.error(
                f"option import_files from web is not enabled for {self.journal.code}.",
            )
            return 1

        if self.options["importfilesweb"]:
            login_setting = f"WJAPP_{self.journal.code.upper()}_IMPORT_LOGIN_PARAMS"
            login_parameters = getattr(settings, login_setting, None)
            if login_parameters is None:
                import_logic.logger.error(
                    f'Missing login data for {self.journal.code}. Please ensure "{login_setting}" exists in settings.'
                    f"Cannot import files, quitting."
                )
                return 1
            elif login_parameters.get("username", "") == "":
                import_logic.logger.error(
                    f'Empty username parameter for "{login_setting}". Please ensure `username`, etc. are correct.'
                    f"Cannot login, quitting."
                )
                return 1

            if not getattr(settings, f"WJAPP_{self.journal.code.upper()}_BASE_URL", None):
                self.stderr.write(
                    f"Missing base wjapp url, import files is not possible. Please set "
                    f"WJAPP_{self.journal.code.upper()}_BASE_URL in your django settings to proceed.",
                )
                return 1

            username = login_parameters.get("username", "")
            passwd = login_parameters.get("password", "")
            login_base_url = login_parameters.get("login_base_url", "")
            http_ba_username = login_parameters.get("http_ba_username", "")
            http_ba_password = login_parameters.get("http_ba_password", "")

            if not http_ba_username and self.journal.code.upper() == "JCOM":
                import_logic.logger.error(
                    f"Missing Basic Authentication username for {self.journal.code}. "
                    f"Check your django settings to proceed."
                )
                return 1

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
            import_logic.logger.error(
                f'Missing connection parameters for {self.journal.code}. Please ensure "{setting}" exists in settings.'
                f"Cannot connect, quitting."
            )
            return 1
        elif connection_parameters.get("user", "") == "":
            import_logic.logger.error(
                f'Empty connection parameters for "{setting}". Please ensure `user`, `host`, etc. are correct.'
                f"Cannot connect, quitting."
            )
            return 1

        self.connection = mariadb.connect(**connection_parameters)

        current_version_row = self.read_article_data(preprintid)

        if not current_version_row:
            self.connection.close()
            import_logic.logger.debug(f"Article not found {self.journal.code} {preprintid}.")
            return 1

        document_cod = current_version_row["documentCod"]
        preprintid = current_version_row["preprintId"]
        publicationid = current_version_row["publicationId"]
        if self.journal.code.upper() in ("JHEP", "JCAP"):
            publication_date = current_version_row["publicationDate"]
        document_revision_dead_line = current_version_row["revisionDeadline"]
        section = current_version_row["documentType"]
        language = current_version_row["language"]
        version_cod = current_version_row["versionCod"]
        article_expected_final_state = current_version_row["stateID"]
        # current_version -> row  "versionNumber"

        current_archive = ""
        old_archive = ""
        if self.options["importfilesarchive"] and self.journal.code.upper() in ["JCAP"]:
            (current_archive, old_archive) = import_file_manager.ImportFileManager.dedup_archive_directory(
                self.journal, preprintid
            )
            import_logic.logger.debug(f"{current_archive=} {old_archive=}")

        import_logic.logger.info(f"""Importing {preprintid}""")

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
            import_logic.logger.warning(
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
        if publicationid and self.journal.code.upper() in ("JCOM", "JCOMAL"):
            pub_article = submission_models.Article.get_article(
                journal=self.journal,
                identifier_type="pubid",
                identifier=publicationid,
            )
            if not pub_article:
                import_logic.logger.error(
                    f"missing published version for {publicationid}. Import published version"
                    f" before to import review and production versions of {preprintid}"
                )
                return 1

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
                    import_logic.logger.debug(
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
                import_logic.logger.debug(
                    f"Importing history ({preprintid}) of a paper already published in WJS "
                    f"({publicationid} / {pub_article.id}"
                )
                article = pub_article
                self.store_article_status(article)

            elif not pub_article and article:
                import_logic.logger.debug(f"Re-importing {preprintid} as {article.id} (paper is not published).")

            else:
                import_logic.logger.debug(f"Importing {preprintid} (paper is not published).")

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
            import_logic.logger.debug(f"Set preprintid {preprintid} onto {article.pk}")

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
            if author_data and author_data["professionCod"]:
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
                import_logic.logger.debug(
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
                    import_logic.logger.debug(f"Looking at action {action['actionID']} ({action['actHistCod']})")

                    # in case of editor reassign during the production the first editor acceptance is changed
                    # in ED_ACC_DOC_WMC to permit the author resubmission.
                    # These wjapp actions are skipped TYP_UPLOADS_FOR_PM PM_REQ_ED_CHECK ED_RESTARTS_REV
                    # and afterwards the author resubmission continues the workflow
                    special_cases = {
                        ("JCOM_003A_0417", 2, 265550),
                        ("JCOM_012A_0615", 2, 260964),
                    }
                    if (preprintid, imported_version_num, action["actHistCod"]) in special_cases:
                        import_logic.logger.warning(
                            f"change action {action['actionID']} to ED_ACC_DOC_WMC "
                            f"for {preprintid}/{imported_version_num} (manage editor restart - known case)."
                        )
                        action["actionID"] = "ED_ACC_DOC_WMC"

                    if hasattr(import_logic, f"{action['actionID']}"):
                        action_manager = getattr(import_logic, f"{action['actionID']}")
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
                            importfiles=self.options["importfilesarchive"],
                            imported_document_layer_cod_list=self.imported_document_layer_cod_list,
                            action_triggers_import_files=False,
                            imported_doclayer_check_visibility=self.imported_doclayer_check_visibility,
                            url_base=getattr(settings, f"WJAPP_{self.journal.code.upper()}_BASE_URL", None),
                            current_archive=current_archive,
                            old_archive=old_archive,
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
                            import_logic.logger.debug(f"Action {action['actionID']} managed in import correspondence.")
                        else:
                            import_logic.logger.warning(f"Action {action['actionID']} not yet managed.")

                # set_authors bios(..)
                # arguments: imported_version_bios_text, article, imported_version_num
                # no more called because the source data are not reliable

                import_logic.ImportCorrespondenceManager(
                    connection=self.connection,
                    session=session,
                    journal=self.journal,
                    preprintid=preprintid,
                    article=article,
                    imported_version_num=imported_version_num,
                    imported_version_cod=imported_version_cod,
                    importfiles=self.options["importfilesarchive"],
                    imported_document_layer_cod_list=self.imported_document_layer_cod_list,
                ).run()

            import_logic.mark_all_messages_read(article)

            import_logic.ImportPermissionsManager(
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
                    import_logic.logger.warning(f"editor role added by the import removed: {a.id} {a.full_name()}")

        except Exception as e:
            traceback.print_exc()
            import_logic.logger.error(
                f"""An exception {type(e).__name__} occurred for {article.id} / {preprintid}
                 {str(e)},

                The preprintid {article.id} / {preprintid} must be imported again
                """
            )
            if self.options["importfilesarchive"]:
                import_file_manager.ImportFileManager().reset_preprintid_dedup(self.journal, preprintid)
            return 1

        # fix forced manual withdrawn appeal on wjapp without action in the history
        if preprintid in ("JCOM_015Y_0515", "JCOM_008A_1120", "JCOMAL_002Y_0921"):
            if article.stage != submission_models.STAGE_REJECTED:
                import_logic.logger.warning(
                    f"stage is '{article.stage}' for withdrawn appeal article {preprintid} / {article.id}"
                )
                article.stage = submission_models.STAGE_REJECTED
                article.save()
                import_logic.logger.warning(
                    f"forced fixed stage to '{article.stage}' for withdrawn appeal article {preprintid} / {article.id}"
                )
            if ArticleWorkflow.objects.filter(article=article).exists():
                if article.articleworkflow.state != ArticleWorkflow.ReviewStates.REJECTED:
                    import_logic.logger.warning(
                        f"review state is '{article.articleworkflow.state}' "
                        f"for rejected article {preprintid} / {article.id}"
                    )
                    article.articleworkflow.state = ArticleWorkflow.ReviewStates.REJECTED
                    article.articleworkflow.save()
                    article.save()
                    import_logic.logger.warning(
                        f"forced fixed review state to '{article.articleworkflow.state}' "
                        f"for withdrawn appeal article {preprintid} / {article.id}"
                    )
        elif article_expected_final_state == "REJECTED":
            if article.stage != submission_models.STAGE_REJECTED:
                import_logic.logger.error(f"stage not rejected: {article.stage=} for {article.id} / {preprintid}")
                return 1
            if (
                ArticleWorkflow.objects.filter(article=article).exists()
                and article.articleworkflow.state != ArticleWorkflow.ReviewStates.REJECTED
            ):
                import_logic.logger.error(
                    f"state not rejected: {article.articleworkflow.state=} for {article.id} / {preprintid}"
                )
                return 1
        else:
            # TODO: verify cases where an error should be logged
            import_logic.logger.debug(f"state: {article_expected_final_state=} {article.stage=}")
            if ArticleWorkflow.objects.filter(article=article).exists():
                import_logic.logger.debug(f"{article.articleworkflow.state=}")

        # for published import restore the article status
        if publicationid:
            if self.journal.code.upper() not in ["JHEP", "JCAP"]:
                self.restore_article_status(article)
            else:
                article.date_published = rome_timezone.localize(publication_date)
                article.save()

            # for published import set the article stage to Published
            if article.stage != submission_models.STAGE_PUBLISHED:
                import_logic.logger.warning(
                    f"stage is '{article.stage}' for published article {preprintid} / {article.id}"
                )
                article.stage = submission_models.STAGE_PUBLISHED
                article.save()
                import_logic.logger.warning(
                    f"forced fixed stage to '{article.stage}' for published article {preprintid} / {article.id}"
                )

            # for published import set the article review state to Published.
            # There are paper in wjapp which are published, but have incomplete history
            # then the review state must be forced to published. e.g.JCOM_015A_0215
            if ArticleWorkflow.objects.filter(article=article).exists():
                if article.articleworkflow.state != ArticleWorkflow.ReviewStates.PUBLISHED:
                    import_logic.logger.warning(
                        f"review state is '{article.articleworkflow.state}' "
                        f"for published article {preprintid} / {article.id}"
                    )
                    article.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
                    article.articleworkflow.save()
                    article.save()
                    import_logic.logger.warning(
                        f"forced fixed review state to '{article.articleworkflow.state}' "
                        f"for published article {preprintid} / {article.id}"
                    )

        if ArticleWorkflow.objects.filter(article=article).exists():
            if article.articleworkflow.state in (
                ArticleWorkflow.ReviewStates.PUBLISHED,
                ArticleWorkflow.ReviewStates.REJECTED,
                ArticleWorkflow.ReviewStates.WITHDRAWN,
                ArticleWorkflow.ReviewStates.NOT_SUITABLE,
            ):
                num_disabled = import_logic.get_reminders_for_article(article, only_enabled=True).update(disabled=True)
                import_logic.logger.debug(
                    f"forced disabled of {num_disabled} reminder for {article.id}/{preprintid}"
                    f" in state {article.articleworkflow.state}"
                )

        if self.options["importfilesarchive"]:
            import_file_manager.ImportFileManager().reset_preprintid_dedup(self.journal, preprintid)

        if settings.DEBUG and self.journal.code.upper() not in ["JHEP", "JCAP"]:
            self.debug_list_article_files_imported(article)
            self.debug_list_reminder(article)

        self.connection.close()
        return 0

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
            import_logic.logger.error("no article status stored")
            raise ValueError("no article status stored")

        article.language = self.store_article_language
        import_logic.logger.warning(f"{self.store_article_language=}")

        article.title = self.store_article_title
        import_logic.logger.warning(f"{self.store_article_title=}")

        if hasattr(self, "store_article_title_en"):
            article.title_en = self.store_article_title_en
            import_logic.logger.warning(f"{self.store_article_title_en=}")

        if hasattr(self, "store_article_title_es"):
            article.title_es = self.store_article_title_es
            import_logic.logger.warning(f"{self.store_article_title_es=}")

        if hasattr(self, "store_article_title_pt"):
            article.title_pt = self.store_article_title_pt
            import_logic.logger.warning(f"{self.store_article_title_pt=}")

        article.abstract = self.store_article_abstract
        import_logic.logger.warning(f"{self.store_article_abstract=}")

        if hasattr(self, "store_article_abstract_en"):
            article.abstract_en = self.store_article_abstract_en
            import_logic.logger.warning(f"{self.store_article_abstract_en=}")

        if hasattr(self, "store_article_abstract_es"):
            article.abstract_es = self.store_article_abstract_es
            import_logic.logger.warning(f"{self.store_article_abstract_es=}")

        if hasattr(self, "store_article_abstract_pt"):
            article.abstract_pt = self.store_article_abstract_pt
            import_logic.logger.warning(f"{self.store_article_abstract_pt=}")

        article.date_submitted = self.store_article_date_submitted
        import_logic.logger.warning(f"{self.store_article_date_submitted=}")

        article.date_accepted = self.store_article_date_accepted
        import_logic.logger.warning(f"{self.store_article_date_accepted=}")

        article.date_published = self.store_article_date_published
        import_logic.logger.warning(f"{self.store_article_date_published=}")

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
        import_logic.logger.warning(f"{self.store_article_doi=}")

        # depending on settings enabled during import the pubid can be modified
        try:
            pubid_identifier = identifiers_models.Identifier.objects.get(
                id_type="pubid",
                article=article,
            )
        except identifiers_models.Identifier.DoesNotExist:
            import_logic.logger.error(f"No pubid identifier found for {article.id}.")
            pubid_identifier = identifiers_models.Identifier.objects.create(
                id_type="pubid",
                article=article,
                enabled=True,
            )
        except identifiers_models.Identifier.MultipleObjectsReturned:
            import_logic.logger.error(f"More than one pubid identifier found for {article.id}, taken the first.")
            pubid_identifier = identifiers_models.Identifier.objects.filter(
                id_type="pubid",
                article=article,
            )[0]
        if pubid_identifier.identifier != self.store_article_pubid:
            import_logic.logger.info(
                f"Resetting old DOI {self.store_article_pubid} onto generated {pubid_identifier.identifier} "
                f"for {article.id}"
            )
            pubid_identifier.identifier = self.store_article_pubid
            pubid_identifier.save()

        # date revision ?

        article.authors.clear()
        import_logic.logger.warning(f"{self.store_article_authors=}")
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
            import_logic.logger.warning(f"author: {a}")

        article.owner = self.store_article_owner
        import_logic.logger.warning(f"{self.store_article_owner=}")

        article.correspondence_author = self.store_article_correspondence_author
        import_logic.logger.warning(f"{self.store_article_correspondence_author=}")

        article.license_id = self.store_article_license_id
        import_logic.logger.warning(f"{self.store_article_license_id=}")

        article.keywords.clear()
        for k in self.store_article_keywords:
            article.keywords.add(k)
            import_logic.logger.warning(f"keyword {k.id} {k}")

        # published paper has no articleworkflow therefore no eo_in_charge}")

        for a in self.store_article_authors:
            import_logic.logger.warning(f"{a} {self.store_authors_bios.get(a, None)}")

        article.section = self.store_article_section
        import_logic.logger.warning(f"{self.store_article_section=}")

        # the special issue ?

        article.primary_issue = self.store_article_primary_issue
        import_logic.logger.warning(f"{self.store_article_primary_issue=}")

        article.page_numbers = self.article_page_numbers
        import_logic.logger.warning(f"{self.article_page_numbers=}")

        # to not loose the render galley for an unexpected problem
        if not article.render_galley:
            import_logic.logger.warning(f"missing render galley {self.store_article_render_galley} restored")
            article.render_galley = self.store_article_render_galley

        article.save()
        article.refresh_from_db()

        import_logic.logger.warning("article status restored")

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
d.publicationDate,
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

        import_logic.logger.debug("reset_article_data")

        # delete article reminders
        list_deleted_reminders = import_logic.get_reminders_for_article(article).delete()
        import_logic.logger.debug(f"{list_deleted_reminders=}")

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
            import_logic.logger.warning(
                f"created ArticleWorkflow during reset article {article}, "
                f"probably missing due to a crashed import, please check"
            )

        # delete  ArticleWorkflow's files
        for f in article.articleworkflow.supplementary_files_at_acceptance.all():
            import_logic.logger.warning(f"reset Supplementary {f.id} {f} {f.file}")
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

        article.title = wjs_models.WjsSimpleBleach().to_python(row["versionTitle"])
        article.abstract = row["versionAbstract"]
        article.imported = True
        article.date_submitted = rome_timezone.localize(row["submissionDate"])
        article.save()
        main_author = import_logic.account_get_or_create_check_correspondence(
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
            eo_in_charge = import_logic.account_get_or_create_check_correspondence(
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
            import_logic.logger.warning(
                f"No author bios found in wjapp for version: {imported_version_num} {article} "
            )
            return

        authors_bios = bios_text.split("\r\n\r\n")

        if len(authors_bios) != article.authors.count():
            import_logic.logger.warning(
                f"Authors bios paragraphs: {len(authors_bios)}, article authors: {article.authors.count()}"
            )

        for author in article.authors.all():
            bios_found = self.get_author_bio_by_name(authors_bios, author)
            if len(bios_found) != 1:
                import_logic.logger.debug(
                    f"Found {len(bios_found)} bios for author {author.full_name()} version: {imported_version_num}."
                )

            # saved first bio found or let unchanged
            if bios_found:
                # TODO: add always to article frozen author frozen biography

                import_logic.logger.debug(
                    f"Updated bio for author {author.full_name()} version: {imported_version_num}."
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
            import_logic.logger.critical(
                f'Unknown article type "{section_name}" for {article.get_identifier("preprintid")}'
            )
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
            import_logic.logger.warning(
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

        # default for jhep when not defined
        if not language and article.journal.code in ["JHEP", "JCAP"]:
            article.language = "eng"
            article.save()
            import_logic.logger.debug(f"{article.journal.code} {article.id} undefined language saved as 'eng'")
            return

        # default for jcom when not defined
        if not language and article.journal.code == "JCOM":
            article.language = "eng"
            article.save()
            import_logic.logger.debug(f"jcom {article.id} undefined language saved as 'eng'")
            return

        # default for jcomal when not defined
        if not language and article.journal.code == "JCOMAL":
            import_logic.logger.warning(f"jcomal {article.id} undefined language let None")
            return

        if language:
            language = language.strip()
        else:
            import_logic.logger.warning(f"{article.id} undefined language let None")
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
                import_logic.logger.warning(f"""Wjapp's "{language}" mapped to "{lang_code}" for {article.id}""")
                article.language = lang_code
                article.save()
                return

        for lang_code in JANEWAY_LANGUAGES_BY_CODE.keys():
            trimmed_lang_name_list = [x.strip() for x in JANEWAY_LANGUAGES_BY_CODE[lang_code].split(";")]
            if language in trimmed_lang_name_list:
                import_logic.logger.warning(f"""Wjapp's "{language}" mapped to "{lang_code}" for {article.id}""")
                article.language = lang_code
                article.save()
                return

        # default for jcom when not matched
        if not article.language and article.journal.code == "JCOM":
            article.language = "eng"
            article.save()
            import_logic.logger.warning(f"jcom {article.id} not matched language <{language}> saved as 'eng'")
            return

        # default for jcomal when not matched
        if not language and article.journal.code == "JCOMAL":
            import_logic.logger.warning(f"jcomal {article.id} not matched language {language} let None")
            return

        # wjapp language not null, but not found in the choices defined above
        if not article.language:
            import_logic.logger.warning(f"{article.id} language {language} not matched let undefined")

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
                import_logic.logger.warning(
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
            import_logic.logger.debug(f"Keyword {kwd_word} set at order {order}")
            article.keywords.add(keyword)
        article.save()

    def set_article_special_issue(self, article, special_issue, preprintid):
        """Set article special issue"""

        # in wjapp this means that is a normal article without special issue
        if "Normal" == special_issue["name"]:
            return

        if self.journal.code == "JCOMAL":
            issue = Issue.objects.get(pk=import_logic.jcomal_map_si[special_issue["issueCod"]])
            issue.articles.add(article)
            article.primary_issue = issue
            article.save()
            # primary issue saved directly article not refreshed because the signal at the refresh is triggered
            # only if the m2m is changed. For example it does not work when the same article is reloaded.
            #
            # Note from: wjs/jcom_profile/tests/conftest.py
            # we must reload article from db as Article.primary_issue is set by a signal triggered by
            # m2m save, and thus our in memory article object has no knowledge of that change
            import_logic.logger.debug(
                f"article {preprintid}/{article.id} from {special_issue['longname']}"
                f" added to issue: {issue.issue_title}"
            )
            return

        if self.journal.code == "JCOM":
            issue = Issue.objects.get(pk=import_logic.jcom_map_si[special_issue["issueCod"]])
            issue.articles.add(article)
            article.primary_issue = issue
            article.save()
            # primary issue saved directly article not refreshed because the signal at the refresh is triggered
            # only if the m2m is changed. For example it does not work when the same article is reloaded.
            #
            # Note from: wjs/jcom_profile/tests/conftest.py
            # we must reload article from db as Article.primary_issue is set by a signal triggered by
            # m2m save, and thus our in memory article object has no knowledge of that change
            import_logic.logger.debug(
                f"article {preprintid}/{article.id} from {special_issue['longname']}"
                f" added to issue: {issue.issue_title}"
            )
            return

        editor_special_issue = import_logic.account_get_or_create_check_correspondence(
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
        article.primary_issue = issue
        article.save()
        # primary issue saved directly article not refreshed because the signal at the refresh is triggered
        # only if the m2m is changed. For example it does not work when the same article is reloaded.
        #
        # Note from: wjs/jcom_profile/tests/conftest.py
        # we must reload article from db as Article.primary_issue is set by a signal triggered by
        # m2m save, and thus our in memory article object has no knowledge of that change
        return issue

    def set_article_document_notes(self, article, document_notes):
        """Set article notes from wjapp document notes"""

        for dn in document_notes:
            note_author = import_logic.account_get_or_create_check_correspondence(
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
                    body=import_logic.newlines_text_to_html(dn["documentNoteContent"]),
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
            account = import_logic.account_get_or_create_check_correspondence(
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
                import_logic.logger.debug(f"cleaned coauthor {author}")

        if authors_modified:
            sync_frozen_authors_with_authors(article)
            article.save()

    def debug_list_article_files_imported(self, article):
        """Log debug of all files imported also historical"""

        article = submission_models.Article.objects.get(id=article.pk)

        for f in article.manuscript_files.all():
            import_logic.logger.debug(f"imported: {article.id} manuscript: {f.id} {f}")
        for f in article.source_files.all():
            import_logic.logger.debug(f"imported: {article.id} source: {f.label} {f.id} {f}")
        for f in article.data_figure_files.all():
            import_logic.logger.debug(f"imported: {article.id} data figure: {f.id} {f}")

        for f in article.articleworkflow.supplementary_files_at_acceptance.all():
            import_logic.logger.debug(f"imported: {article.id} suppl. file at acceptance: {f.id} {f}")

        err_list = EditorRevisionRequest.objects.filter(article=article)
        for e in err_list:
            for m in e.manuscript_files.all():
                import_logic.logger.debug(
                    f"imported: {article.id} rev round {e.review_round.round_number} manuscript: {m.id} {m}"
                )
            for s in e.source_files.all():
                import_logic.logger.debug(
                    f"imported: {article.id} rev round {e.review_round.round_number} source: {s.label} {s.id} {s}"
                )
            for d in e.data_figure_files.all():
                import_logic.logger.debug(
                    f"imported: {article.id} rev round {e.review_round.round_number} data figure: {d.id} {d}"
                )

        for tr in article.typesettinground_set.all():
            ta = tr.typesettingassignment
            for f in ta.files_to_typeset.all():
                import_logic.logger.debug(f"imported: {article.id} TA: {ta.round} FTT: {f.id} {f}")
            for g in ta.galleys_created.all():
                import_logic.logger.debug(f"imported: {article.id} TA: {ta.round} GC: {g.id} {g} public: {g.public}")
            for gp in ta.round.galleyproofing_set.all():
                import_logic.logger.debug(
                    f"imported: {article.id} TA: {ta.round} GP: {gp.id} {gp.proofed_files.all()}"
                )

        if pgs := article.articleworkflow.publication_galleys_source_file:
            import_logic.logger.debug(f"imported: {article.id} publication galleys source uploaded: {pgs.id} {pgs}")

    def debug_list_reminder(self, article):
        """List all reminder set on the article"""

        # TBV: in JCOM_001N_0424 appear the reminders of version1 (AUMJR1 AUMJR2) should be deleted?
        for r in import_logic.get_reminders_for_article(article):
            import_logic.logger.debug(
                f"reminder on {article.id}: {r.code} created:{r.date_created.date()} due:{r.date_due} "
                f"sent:{r.date_sent} {r.disabled=} recipient:{r.recipient}"
            )
