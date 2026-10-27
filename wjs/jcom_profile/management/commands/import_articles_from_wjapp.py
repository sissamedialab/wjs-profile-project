"""Import article from wjapp."""

# profiling
import cProfile
import datetime
import io
import os
import pstats
import sys
import traceback

import freezegun
import mariadb
import requests
from core.models import Account, File
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from identifiers import models as identifiers_models
from journal.models import Issue, IssueType, Journal
from plugins.hydra.models import LinkedArticle, LinkType
from plugins.wjs_review.logic import (
    EditorRevisionRequest,
    PermissionAssignment,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    EditorDecision,
    Message,
    PastEditorAssignment,
)
from plugins.wjs_submission.models import (
    AccessMode,
    AccessModeJournal,
    ArticleCollaboration,
    ArticleSubmission,
    Collaboration,
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

import wjs.jcom_profile.import_wjapp.import_file_manager as import_file_manager
import wjs.jcom_profile.import_wjapp.import_logic as import_logic
import wjs.jcom_profile.import_wjapp.import_production_hold as import_production_hold
import wjs.jcom_profile.import_wjapp.import_specific_corrections as import_specific_corrections
from wjs.jcom_profile import constants
from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_utils import (
    JANEWAY_LANGUAGES_BY_CODE,
    JOURNALS_DATA,
    NON_PEER_REVIEWED,
    SECTIONS_MAPPING,
    rome_timezone,
    set_author_country,
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


# Map wjapp's Collaboration.collabName (JCAP source db) onto the matching wjs
# Collaboration.name, so re-imported articles attach to the existing wjs collaboration
# instead of creating a duplicate with wjapp's naming convention.
# Entries with no confident match in wjs (ambiguous multi-collaboration names, or names
# not found at all in wjs) are intentionally left out: create_collaboration_from_wjapp()
# falls back to the raw wjapp name and logs a warning so they can be reviewed and added here.
WJAPP_JCAP_COLLABORATION_NAME_MAPPING = {
    "Alice": "ALICE collaboration",
    "CMS": "CMS collaboration",
    "LHCb": "LHCb collaboration",
    "ATLAS": "ATLAS collaboration",
    "TOTEM": "TOTEM collaboration",
    "LHCf": "LHCf collaboration",
    "Pierre Auger": "Pierre Auger collaboration",
    "Ice Cube": "IceCube collaboration",
    "Cuore": "CUORE collaboration",
    "Borexino": "Borexino collaboration",
    "ANTARES": "ANTARES collaboration",
    "EDELWEISS": "Edelweiss collaboration",
    "SDSS": "SDSS collaboration",
    "Fermi LAT": "Fermi-LAT collaboration",
    "Magic": "MAGIC collaboration",
    "Exo-200": "EXO-200 collaboration",
    "XENON": "XENON collaboration",
    "ACTPol": "ACTPol collaboration",
    "Double Chooz": "Double Chooz collaboration",
    "ArDM": "ArDM collaboration",
    "Daya Bay": "Daya Bay collaboration",
    "HAWC": "HAWC collaboration",
    "IMAGINE": "IMAGINE Consortium collaboration",
    "H.E.S.S.": "H.E.S.S. collaboration",
    "Simons Observatory": "Simons Observatory collaboration",
    "PTOLEMY": "PTOLEMY collaboration",
    "Cosine-100": "COSINE-100 collaboration",
    "CREDO": "CREDO collaboration",
    "LSST Dark Energy Science": "LSST Dark Energy Science collaboration",
    "Arianna": "ARIANNA collaboration",
    "NOvA": "NOvA collaboration",
    "CTA - Cherenkov Telescope Array Consortium": "CTA consortium collaboration",
    "LSPE": "LSPE collaboration",
    "LISA": "LISA collaboration",
    "DarkSide": "DarkSide collaboration",
    "ANITA": "ANITA collaboration",
    "MADMAX": "MADMAX collaboration",
    "DRIFT": "DRIFT collaboration",
    "DUNE": "DUNE collaboration",
    "LiteBIRD": "LiteBIRD collaboration",
    "Juno": "JUNO collaboration",
    "The Gerda collaboration": "Gerda collaboration",
    "Euclid Consortium": "EUCLID consortium collaboration",
    "COMPACT": "COMPACT collaboration",
    "CDEX": "CDEX collaboration",
    "KM3NeT collaboration": "KM3NeT collaboration",
    "DESI collaboration": "DESI collaboration",
    "QUEST-DMC": "QUEST-DMC collaboration",
    "BEACON Collaboration": "BEACON collaboration",
    "COSINUS Collaboration": "COSINUS collaboration",
    "Telescope Array": "Telescope Array collaboration",
    "JEM-EUSO": "JEM-EUSO collaboration",
    "CUPID-0": "CUPID-0 collaboration",
    # Not found (or no unambiguous single match) in the wjs collaboration list at the time
    # this mapping was built - review manually if/when they show up during an import:
    # "Atacama Cosmology Telescope" (12), #noqa
    # "PRISM" (14), #noqa
    # "LOPES" (15), #noqa
    # "Tunka-Rex" (18), #noqa
    # "ANTARES, TAROT, ROTSE, Swift and Zadko" (20), #noqa
    # "IceCube, Pierre Auger and Telescope Array" (23), #noqa
    # "DARWIN" (24), #noqa
    # "CORE" (28), #noqa
    # "POLARBEAR" (29), #noqa
    # "CAST" (34), #noqa
    # "LAGO" (45), #noqa
    # "POEMMA" (49), #noqa
    # "ACT & DES" (58), #noqa
    # "SPT-3G & DES Collaborations" (61), #noqa
    # "DESI and ACT collaboration" (63), #noqa
    # "Einstein Telescope" (65), #noqa
    # "Cosmoglobe and LiteBIRD Collaborations" (68), #noqa
    # "The Fermi -LAT, HAWC, H.E.S.S., MAGIC, and VERITAS Collaborations" (69), #noqa
    # "MINOS+" (73), #noqa
}


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
            import_logic.logger.error(f"Article not found {self.journal.code} {preprintid}.")
            return 1

        document_cod = current_version_row["documentCod"]
        preprintid = current_version_row["preprintId"]
        publicationid = current_version_row["publicationId"]
        if self.journal.code.upper() in ("JHEP", "JCAP"):
            publication_date = current_version_row["publicationDate"]
            acceptance_date = current_version_row["acceptanceDate"]
            doi = current_version_row["doi"]
            archive_number = current_version_row["archiveNumber"]
            archive_category = current_version_row["archiveCategory"]
            oaa_cod = current_version_row["oaaCod"]
            copy_cod = current_version_row["copyCod"]
            correction_type = current_version_row["correctionType"]
            corrected_document_cod = current_version_row["correctedDocumentCod"]
            collab_name = current_version_row["collabName"]
        document_revision_dead_line = current_version_row["revisionDeadline"]
        section = current_version_row["documentType"]
        language = current_version_row["language"]
        version_cod = current_version_row["versionCod"]
        article_expected_final_state = current_version_row["stateID"]
        # current_version -> row  "versionNumber"

        current_archive = ""
        old_archive = ""
        if self.options["importfilesarchive"] and self.journal.code.upper() in ["JCAP"]:
            try:
                (current_archive, old_archive) = import_file_manager.ImportFileManager.dedup_archive_directory(
                    self.journal, preprintid
                )
            except Exception as e:
                self.connection.close()
                import_logic.logger.error(
                    f"An exception {type(e).__name__}: {e} occurred deduplicating archives for"
                    f" {preprintid}, the preprintid must be imported again"
                )
                return 1
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

        # search if exists an id for publicationid in the wjs journal only for jcom and jcomal
        wjs_published_article = None
        if publicationid and self.journal.code.upper() in ("JCOM", "JCOMAL"):
            wjs_published_article = submission_models.Article.get_article(
                journal=self.journal,
                identifier_type="pubid",
                identifier=publicationid,
            )
            if not wjs_published_article:
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

        # Scenarios for jcom jcomal
        #
        # Note:
        # - wjs_published_article: is the article found searching the publicationid
        # - article: is the article found searching the preprintid
        #
        # 1. wjs_published_article != article
        #       runtime error
        #
        # 2. article == wjs_published_article
        #       re-import preprintid versions, restore publicationid status
        #
        # 3. wjs_published_article exists and article is none
        #       import preprintid using publicationid, restore publicationid status
        #
        # 4. article exists and wjs_published_article is none
        #       re-import preprintid versions
        #
        # 5. article is none and wjs_published_article is none
        #       import preprintid versions creating new id
        #
        # e.g. published papers: JCOM_002N_0722 JCOM_005A_1022 JCOM_002N_0323

        # scenarios for jhep and jcap
        #
        # 1. Re-Importing article (implies reset_article) (case 4. above)
        # 2. Importing article (case 5. above)
        #
        # in both cases:
        #  - all versions are imported also the publication version if exists
        #  - no need of store article and reset article

        self.stored_status = False

        try:
            if wjs_published_article and article:
                if wjs_published_article.id == article.id:
                    self.store_article_status(wjs_published_article)
                    import_logic.logger.debug(
                        f"Re-importing history ({preprintid}) of a paper already published in WJS "
                        f"{publicationid} / {wjs_published_article.id}"
                    )

                else:
                    raise RuntimeError(
                        f"different wjs id for pubid {wjs_published_article.id} and preprintid {article.id}"
                        f"delete {preprintid} from wjs and import in wjs the galley for {publicationid}"
                        f"afterwards import old versions of {preprintid}"
                    )

            elif wjs_published_article and not article:
                import_logic.logger.debug(
                    f"Importing history ({preprintid}) of a paper already published in WJS "
                    f"({publicationid} / {wjs_published_article.id}"
                )
                article = wjs_published_article
                self.store_article_status(article)

            elif not wjs_published_article and article:
                import_logic.logger.debug(f"Re-importing {preprintid} as {article.id}.")

            else:
                import_logic.logger.debug(f"Importing {preprintid}.")

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

            # the object submission_data is deleted by reset article and
            # created by a signal at article creation
            try:
                article.submission_data
            except ArticleSubmission.DoesNotExist:

                ArticleSubmission.objects.create(
                    article=article,
                )

            if archive_number:
                identifiers_models.Identifier.objects.get_or_create(
                    identifier=archive_number,
                    article=article,
                    id_type="arxiv",
                )
                if not archive_category:
                    archive_category = ""
                article.submission_data.arxiv_category = archive_category

            article.submission_data.cas = ""
            article.submission_data.das = ""
            article.submission_data.use_of_ai_flag = False
            access_mode, licence, copyright_name = self.create_access_mode_from_wjapp(article, oaa_cod, copy_cod)
            article.submission_data.access_mode = access_mode
            # Triggers ArticleSubmission._sync_access_mode_parameters(), which applies the
            # access mode's journal-level default license/rights to the article.
            article.submission_data.save()
            article.refresh_from_db()
            # If this specific paper's actual license/rights (from wjapp) differ from the
            # default just applied above, override them per-article rather than letting a
            # single wjapp (oaa_cod, copy_cod) combination fork the shared access mode
            # configuration (see create_access_mode_from_wjapp).
            if article.license != licence:
                article.license = licence
                article.submission_data.license_override = True
            if article.rights != copyright_name:
                article.rights = copyright_name
                article.submission_data.rights_override = True
            article.submission_data.save()
            article.save()
            self.create_collaboration_from_wjapp(article, collab_name)
            article.refresh_from_db()

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

            # erratum addendum section
            if correction_type in ("erratum", "addendum"):
                section = correction_type
                import_logic.logger.debug(f"for {preprintid} / {article.id} section set to {correction_type}")
                corrected_preprintid = self.read_corrected_preprintid(corrected_document_cod).get("preprintid", None)
                corrected_article = submission_models.Article.get_article(
                    journal=article.journal,
                    identifier_type="preprintid",
                    identifier=corrected_preprintid,
                )
                if corrected_article:
                    relationship = LinkType.ERRATUM if correction_type == "erratum" else LinkType.ADDENDUM
                    LinkedArticle.objects.get_or_create(
                        from_article=corrected_article,
                        to_article=article,
                        relationship=relationship,
                        defaults={
                            "order": LinkedArticle.objects.filter(
                                from_article=corrected_article,
                                relationship=relationship,
                            ).count(),
                        },
                    )
                else:
                    import_logic.logger.warning(f"Erratum parent not found: {corrected_preprintid}")

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
                self.history = self.read_history_data(imported_version_cod)

                # repair some known problems in the history to be compatible with wjs history
                self.repair_history_insert_delay_on_action_datetime(preprintid, imported_version_num)
                self.repair_history_move_author_submit_first(preprintid, imported_version_num)
                self.repair_history_specific_article(preprintid, imported_version_num)
                self.repair_history_general(preprintid, imported_version_num)

                # Note: editor selection is done with actions of the history

                for action in self.history:
                    # TODO: move the update of imported_document_layer_cod_list out of the action manager
                    # using as return value of each action manager run() the partial list
                    import_logic.logger.debug(
                        f"Looking at action {preprintid}/{imported_version_num}"
                        f" {action['actionID']} ({action['actHistCod']})"
                    )

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

            import_production_hold.mark_all_messages_read(article)

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
                f"An exception {type(e).__name__}: {str(e)} occurred for"
                f" {article.id} / {preprintid}, the preprintid must be imported again"
            )
            if self.options["importfilesarchive"]:
                import_file_manager.ImportFileManager().reset_preprintid_dedup(self.journal, preprintid)
            return 1

        # wjapp Document.acceptanceDate is authoritative; accept_article() (run during
        # action replay) sets date_accepted from the ED_ACC_DOC action's actionDate
        # instead, which can differ by a second or so. Applies to every paper with an
        # acceptance date, not just published ones.
        # TODO: JCAP only for now, extend to JHEP once verified there too.
        if self.journal.code.upper() == "JCAP" and acceptance_date and acceptance_date.year != 1970:
            wjapp_date_accepted = rome_timezone.localize(acceptance_date)
            if article.date_accepted != wjapp_date_accepted:
                article.date_accepted = wjapp_date_accepted
                article.save()

        # fix forced manual withdrawn appeal on wjapp without action in the history
        if preprintid in (
            "JCAP_025P_0225",
            "JCAP_057P_0425",
            "JCAP_105P_1125",
            "JCAP_103P_0924",
            "JCAP_089P_0924",
            "JCAP_003P_0124",
            "JCAP_071P_1224",
            "JCAP_037P_0126",
            "JCAP_021P_0521",
            "JCAP_018P_0716",
            "JCAP_052P_0721",
            "JCAP_031P_1020",
            "JCAP_037P_0118",
            "JCAP_067P_0318",
            "JCAP_081P_0919",
            "JCAP_098P_0721",
            "JCAP_018P_0523",
            "JCAP_013P_0723",
        ):
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

        # for published import restore the article status only for jcom jcomal
        if publicationid:
            if self.journal.code.upper() not in ["JHEP", "JCAP"]:
                self.restore_article_status(article)
            else:
                # sets publication identifiers as imported from wjapp
                article.date_published = rome_timezone.localize(publication_date)

                # depending on settings enabled during import the doi can be modified
                # so we drop any DOI an re-create the correct one
                identifiers_models.Identifier.objects.filter(
                    id_type="doi",
                    article=article,
                ).delete()
                identifiers_models.Identifier.objects.create(
                    id_type="doi",
                    article=article,
                    identifier=doi,
                )
                identifiers_models.Identifier.objects.filter(
                    id_type="pubid",
                    article=article,
                ).delete()
                identifiers_models.Identifier.objects.create(
                    id_type="pubid",
                    article=article,
                    enabled=True,
                    identifier=publicationid,
                )
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

        # specs#3131: a paper whose wjapp history stopped at acceptance stays held for the EO;
        # any other paper must keep nothing of the hold.
        if ArticleWorkflow.objects.filter(article=article).exists():
            import_production_hold.finalize_production_readiness_hold(article)

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
    # repair history
    #

    def repair_history_insert_delay_on_action_datetime(self, preprintid, imported_version_num):
        """Insert a delay of 1 second per consecutive action sharing the same datetime (00:00:00),
        so that actions with the same date have a complete order."""

        # apply correction only if all actions have time (h, m, s) = (0, 0, 0)
        if not all(
            a["actionDate"].hour == 0 and a["actionDate"].minute == 0 and a["actionDate"].second == 0
            for a in self.history
        ):
            return

        # group consecutive actions by original datetime, then apply incremental delay
        i = 0
        while i < len(self.history):
            ref_dt = self.history[i]["actionDate"]
            run = [i]
            j = i + 1
            while j < len(self.history) and self.history[j]["actionDate"] == ref_dt:
                run.append(j)
                j += 1

            if len(run) > 1:
                for offset, idx in enumerate(run):
                    self.history[idx]["actionDate"] = ref_dt + datetime.timedelta(seconds=offset)

            i = j

        for action in self.history:
            import_logic.logger.warning(
                f"history insert delay {preprintid}/{imported_version_num}: "
                f"{action['actionID']} {action['actionDate']}"
            )

    def repair_history_move_author_submit_first(self, preprintid, imported_version_num):
        """Repair the order of submission action for a defined set of old articles."""

        for a in self.history:
            import_logic.logger.warning(
                f"original history move author submit first {preprintid}/{imported_version_num}: {a['actionID']}"
            )

        articles_version = [
            ("JCAP001A0204", 2),
            ("JCAP001A0503", 2),
            ("JCAP003A0503", 2),
            ("JCAP016A0303", 2),
            ("JCAP018A0404", 2),
            ("JCAP021A0703", 2),
            ("JCAP019A0404", 2),
            ("JCAP009A0203", 2),
            ("JCAP009A0304", 2),
            ("JCAP012A1103", 2),
            ("JCAP021A06042", 2),
            ("JCAP021A0604", 2),
        ]

        if (preprintid, imported_version_num) not in articles_version:
            return

        # find the AU_SUB_REV action
        action_index = None
        for i, action in enumerate(self.history):
            if action.get("actionID") == "AU_SUB_REV":
                action_index = i
                break

        if action_index is None:
            import_logic.logger.warning(
                f"repair history move author submit first: AU_SUB_REV not found"
                f" in {preprintid}/{imported_version_num}"
            )
            return

        if action_index == 0:
            # already in place, nothing to do
            return

        action = self.history.pop(action_index)
        self.history.insert(0, action)
        import_logic.logger.warning(
            f"repair history move author submit first: moved AU_SUB_REV from index"
            f" {action_index} to 0 in {preprintid}/{imported_version_num}"
        )

    def repair_history_specific_article(self, preprintid, imported_version_num):
        """Apply correction to the history of a specific article version."""
        import_specific_corrections.repair_history_specific_article(
            self.history, preprintid, imported_version_num, self.eo_in_charge_identity
        )

    def repair_history_general(self, preprintid, imported_version_num):
        """Apply general corrections to the wjapp history of a version.

        the sequence of actions of a wjapp version have some known differences with wjs logic
        which can be repaired."""

        for a in self.history:
            import_logic.logger.warning(
                f"original history general correction {preprintid}/{imported_version_num}: {a['actionID']}"
            )

        self.correction_double_final_au_publishes(preprintid, imported_version_num)
        self.correction_remove_send_proof_info(preprintid, imported_version_num)
        self.correction_double_final_pm_publishes(preprintid, imported_version_num)
        self.remove_send_proofs_info_when_remind_auth_typ(preprintid, imported_version_num)
        self.remove_send_proofs_info_when_reminds_auth_and_action_send_typ(preprintid, imported_version_num)
        self.remove_send_proofs_info_when_three_reminds_auth(preprintid, imported_version_num)
        self.remove_ed_check_ed_approves_before_ready_for_pub(preprintid, imported_version_num)
        self.remove_last_inconsistent_action(preprintid, imported_version_num)
        self.reorder_history_send_corrections(preprintid, imported_version_num)
        self.change_au_publishes_to_pm_publishes(preprintid, imported_version_num)
        self.remove_double_ed_ref_doc(preprintid, imported_version_num)
        self.correction_remove_repeated_actions_same_agent(preprintid, imported_version_num)

        for a in self.history:
            import_logic.logger.warning(
                f"repaired history general correction {preprintid}/{imported_version_num}: {a['actionID']}"
            )

    def correction_double_final_au_publishes(self, preprintid, imported_version_num):
        """Correction double final AU_PUBLISHES."""

        if len(self.history) >= 2:
            penult, last = self.history[-2], self.history[-1]
            if penult["actionID"] == "AU_PUBLISHES" and last["actionID"] == "AU_PUBLISHES":
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: {self.history[-2]['actionID']}"
                )
                del self.history[-2]  # remove the penultimate element in-place

    def correction_remove_send_proof_info(self, preprintid, imported_version_num):
        """Correction remove send_proof_info."""

        if len(self.history) >= 2:
            penult, last = self.history[-2], self.history[-1]
            if penult["actionID"] in ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R") and last["actionID"] in (
                "TYP_PUBLISHES",
                "PM_PUBLISHES",
            ):
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: {self.history[-2]['actionID']}"
                )
                del self.history[-2]  # remove the penultimate element in-place
            elif len(self.history) >= 3:
                thirdlast, penult, last = self.history[-3], self.history[-2], self.history[-1]
                if (
                    thirdlast["actionID"] in ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R")
                    and penult["actionID"] in ("PM_REMINDS_AUTH", "ADM_SETS_PUB_SETINGS")
                    and last["actionID"]
                    in (
                        "TYP_PUBLISHES",
                        "PM_PUBLISHES",
                    )
                ):
                    import_logic.logger.warning(
                        f"del from history: {preprintid}/{imported_version_num}: {self.history[-3]['actionID']}"
                    )
                    del self.history[-3]  # remove the thirdlast element in-place
                elif (
                    thirdlast["actionID"] in ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R")
                    and penult["actionID"]
                    in (
                        "TYP_PUBLISHES",
                        "PM_PUBLISHES",
                    )
                    and last["actionID"] in ("PUM_SENDS_TO_TYP", "PUB_PUBLISHES")
                ):
                    import_logic.logger.warning(
                        f"del from history: {preprintid}/{imported_version_num}: {self.history[-3]['actionID']}"
                    )
                    del self.history[-3]  # remove the thirdlast element in-place

    def correction_double_final_pm_publishes(self, preprintid, imported_version_num):
        """Correction double final PM_PUBLISHES."""

        if len(self.history) >= 2:
            penult, last = self.history[-2], self.history[-1]
            if penult["actionID"] == "PM_PUBLISHES" and last["actionID"] == "PM_PUBLISHES":
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: {self.history[-2]['actionID']}"
                )
                del self.history[-2]  # remove the penultimate element in-place

    def remove_send_proofs_info_when_remind_auth_typ(self, preprintid, imported_version_num):
        """Remove send_proofs_info when followed by remind auth."""

        patterns = [
            [
                ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R"),
                "PM_REMINDS_AUTH",
                ("TYP_PUBLISHES", "PM_PUBLISHES"),
                "PUM_SENDS_TO_TYP",
            ],
            [
                ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R"),
                "PM_REMINDS_AUTH",
                "PM_REMINDS_AUTH",
                ("TYP_PUBLISHES", "PM_PUBLISHES"),
            ],
            [("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R"), "PM_PUBLISHES", "PUM_SENDS_TO_TYP", "PM_REMINDS_TYP"],
        ]

        len_pat = 4
        if len(self.history) < len_pat:
            return

        # extract the last len_pat actionID
        recent = [h["actionID"] for h in self.history[-len_pat:]]

        for pattern in patterns:
            # compare the last len_pat elements
            recent_slice = recent[-len_pat:]
            if all(self.match_pattern_item(exp, act) for exp, act in zip(pattern, recent_slice)):
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: {self.history[-len_pat]['actionID']}"
                )
                del self.history[-len_pat]  # remove the first element of the pattern
                break

    def remove_send_proofs_info_when_reminds_auth_and_action_send_typ(self, preprintid, imported_version_num):
        """Remove send_proofs_info when followed by reminds auth and last action PUM_SENDS_TO_TYP."""

        pattern = [
            ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R"),
            "PM_REMINDS_AUTH",
            "PM_REMINDS_AUTH",
            ("TYP_PUBLISHES", "PM_PUBLISHES"),
            "PUM_SENDS_TO_TYP",
        ]

        len_pattern = len(pattern)
        if len(self.history) >= len_pattern:
            recent = [h["actionID"] for h in self.history[-len_pattern:]]

            if all(self.match_pattern_item(exp, act) for exp, act in zip(pattern, recent)):
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: {self.history[-5]['actionID']}"
                )
                del self.history[-5]  # first element of the pattern

    def remove_ed_check_ed_approves_before_ready_for_pub(self, preprintid, imported_version_num):
        """Remove PM_REQ_ED_CHECK and ED_APPROVES with last action PM_PUBLISHES."""

        pattern = [
            "PM_REQ_ED_CHECK",
            "ED_APPROVES",
            "PM_PUBLISHES",
        ]

        len_pattern = len(pattern)
        if len(self.history) >= len_pattern:
            recent = [h["actionID"] for h in self.history[-len_pattern:]]

            if all(self.match_pattern_item(exp, act) for exp, act in zip(pattern, recent)):
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: "
                    f"{self.history[-3]['actionID']} {self.history[-2]['actionID']}"
                )
                del self.history[-2]  # ED_APPROVES
                del self.history[-2]  # PM_REQ_ED_CHECK

    def remove_last_inconsistent_action(self, preprintid, imported_version_num):
        """Remove an inconsistent action after withdrawn."""

        patterns = [
            [
                ("AU_WITHD_DOC", "ADMIN_WITHD_DOC", "ED_REQ_REV"),
                ("ED_ACT_AS_REF", "ED_ASS_REF", "ED_ADD_REF"),
            ],
            [
                "PM_PUBLISHES",
                ("ED_ASS_REF", "ED_ADD_REF"),
            ],
            [
                "ED_REJ_DOC",
                "AU_WITHD_DOC",
            ],
            [
                "PUB_PUBLISHES",
                "ED_ADD_REF",
            ],
        ]

        len_pattern = 2
        if len(self.history) < len_pattern:
            return

        recent = [h["actionID"] for h in self.history[-len_pattern:]]

        for pattern in patterns:
            # compare the last len_pat elements
            recent_slice = recent[-len_pattern:]
            if all(self.match_pattern_item(exp, act) for exp, act in zip(pattern, recent_slice)):
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: {self.history[-1]['actionID']}"
                )
                del self.history[-1]  # last element of the pattern
                break

    def match_pattern_item(self, expected, actual):
        return (isinstance(expected, tuple) and actual in expected) or (expected == actual)

    def remove_send_proofs_info_when_three_reminds_auth(self, preprintid, imported_version_num):
        """Remove send_proofs_info when followed by three reminds auth."""
        pattern = [
            ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R"),
            "PM_REMINDS_AUTH",
            "PM_REMINDS_AUTH",
            "PM_REMINDS_AUTH",
            ("TYP_PUBLISHES", "PM_PUBLISHES"),
        ]

        len_pattern = len(pattern)
        if len(self.history) >= len_pattern:
            recent = [h["actionID"] for h in self.history[-len_pattern:]]

            if all(self.match_pattern_item(exp, act) for exp, act in zip(pattern, recent)):
                import_logic.logger.warning(
                    f"del from history: {preprintid}/{imported_version_num}: {self.history[-5]['actionID']}"
                )
                del self.history[-5]  # first element of the pattern

    def reorder_history_send_corrections(self, preprintid, imported_version_num):
        """Reorder history because some old paper has send correction after publication."""

        if len(self.history) >= 4:
            fourthlast, thirdlast, penult, last = (
                self.history[-4],
                self.history[-3],
                self.history[-2],
                self.history[-1],
            )
            if (
                fourthlast["actionID"] in ("PM_REQ_INFO", "PM_SENDS_FOR_PROOF_R")
                and thirdlast["actionID"]
                in (
                    "TYP_PUBLISHES",
                    "PM_PUBLISHES",
                )
                and penult["actionID"] == "PUB_PUBLISHES"
                and last["actionID"] == "AU_SENDS_CORRECT"
            ):
                import_logic.logger.warning(
                    f"history reordered: {preprintid}/{imported_version_num}: "
                    f"{self.history[-1]['actionID']} after {self.history[-4]['actionID']}"
                )
                self.history[-3], self.history[-2], self.history[-1] = (
                    self.history[-1],
                    self.history[-3],
                    self.history[-2],
                )

    def change_au_publishes_to_pm_publishes(self, preprintid, imported_version_num):
        """Change AU_PUBLISHES to PM_PUBLISHES when found a specific pattern."""

        if len(self.history) >= 4:
            fourthlast, thirdlast, penult, last = (
                self.history[-4],
                self.history[-3],
                self.history[-2],
                self.history[-1],
            )
            if (
                fourthlast["actionID"] == "TYP_UPLOADS_FOR_PM"
                and thirdlast["actionID"] == "PM_SENDS_FOR_PROOF_R"
                and penult["actionID"] == "AU_SENDS_CORRECT"
                and last["actionID"] == "AU_PUBLISHES"
            ):
                import_logic.logger.warning(
                    f"change action in history: {preprintid}/{imported_version_num}:"
                    f" {self.history[-1]['actionID']} to PM_PUBLISHES"
                )
                self.history[-1]["actionID"] = "PM_PUBLISHES"  # change AU_PUBLISHES to PM_PUBLISHES in-place

    def remove_double_ed_ref_doc(self, preprintid, imported_version_num):
        """Remove doubled ED_REF_DOC under some conditions."""

        # this correction is done if the second consecutive ED_REF_DOC action has agent null
        # it is due to a repated wrong action in wjapp which lets broken data
        target = "ED_REF_DOC"
        to_remove = []
        for i in range(len(self.history) - 1):
            a = self.history[i]
            b = self.history[i + 1]
            if a.get("actionID") == target and b.get("actionID") == target:
                agent = b.get("agentCod")
                # second action has agent null
                if agent is None:
                    to_remove.append(i + 1)

        for idx in sorted(set(to_remove), reverse=True):
            del self.history[idx]
            import_logic.logger.warning(
                f"delete action in history: {preprintid}/{imported_version_num}:" f" action: {target} index: {idx}"
            )

    def correction_remove_repeated_actions_same_agent(self, preprintid, imported_version_num):
        """Executes the correction for the targets respecting the order in the list."""

        targets = [
            "AU_SENDS_CORRECT",
            "ED_ACT_AS_REF",
            "AU_WITHD_DOC",
            "AU_PUBLISHES",
            "PM_REQ_INFO",
            "TYP_TAKES_CHARGE",
            "PM_SENDS_FOR_PROOF_R",
            "PUM_SENDS_TO_TYP",
            "REF_SENDS_REP",
            "ADMIN_CONFIRMS_TA",
            "PUB_PUBLISHES",
        ]

        # For most targets a run must share the same agentCod (the actor) to be treated as a
        # wjapp duplicate-log glitch - two different agents doing the "same" action back to
        # back is usually two genuinely distinct events. PM_SENDS_FOR_PROOF_R is a confirmed
        # exception: e.g. JCAP_028P_0410/2 (actHistCod 55035/55036, 3 seconds apart) has two
        # different EO officers sending the *same* author's proofs request - agentCod differs
        # but targetCod (the recipient) is identical, which is the real signal of a duplicate
        # here. PM_REQ_INFO is included by analogy (same Requestproofs business logic, same
        # "notify the author" shape) but not independently confirmed the same way - remove it
        # here if it turns out to need the stricter agentCod-only rule instead. PUM_SENDS_TO_TYP
        # uses the standard same-agent rule: confirmed via JCAP_031P_1221/6 (actHistCod
        # 342942/342948, same agentCod, 2 minutes apart, byte-identical PUMANN annotation text).
        # REF_SENDS_REP also uses the standard same-agent rule: confirmed via JCAP_022P_1205/1
        # (actHistCod 10180-10185, 6 in a row, same referee, ~5 minutes apart - a wjapp bug with
        # missing state control plus stale browser-cached form data let the referee resubmit the
        # same report repeatedly).
        # ADMIN_CONFIRMS_TA also uses the standard same-agent rule: confirmed across 11 preprintids
        # (e.g. JCAP_009P_0822, actHistCod 348352/348353, same EO officer, 1 second apart, nothing
        # else in between) - the EO's TA-confirmation button double-firing. Keeping the run's last
        # occurrence is fine here: either duplicate triggers the same
        # system_verifies_production_requirements() transition, so which one survives doesn't
        # change the outcome, only the transition's logged timestamp by a few seconds.
        # PUB_PUBLISHES also uses the standard same-agent rule: confirmed via JCAP_084P_0520/5
        # (actHistCod 277401/277402, same agent, back to back) - the second replay re-ran
        # move_production_source_to_pub_galleys_source_file(), which found the placeholder file
        # already added by the first replay and logged a spurious "no files to typeset found in
        # any round" error (harmless, since publication_galleys_source_file was already
        # correctly set by the first replay, but noisy and untracked by the log-error grouping).
        also_match_by_target = {"PM_SENDS_FOR_PROOF_R", "PM_REQ_INFO"}

        # this correction is done when there are n consecutive target actions, with the same agent.
        # This is due to a repeated action in wjapp, for the actions in the targets must
        # be kept the last one.
        for target in targets:
            to_remove = []
            i = 0
            while i < len(self.history):
                if self.history[i].get("actionID") != target:
                    i += 1
                    continue

                run = [i]
                j = i + 1
                while j < len(self.history):
                    a = self.history[run[-1]]
                    b = self.history[j]
                    same_agent = b.get("agentCod") == a.get("agentCod")
                    same_target = target in also_match_by_target and b.get("targetCod") == a.get("targetCod")
                    if b.get("actionID") == target and (same_agent or same_target):
                        run.append(j)
                        j += 1
                    else:
                        break

                if len(run) >= 2:
                    to_remove.extend(run[:-1])

                i = j  # skip the past run

            for idx in sorted(set(to_remove), reverse=True):
                del self.history[idx]
                import_logic.logger.warning(
                    f"delete action in history: {preprintid}/{imported_version_num}: action: {target} index: {idx}"
                )

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
        for a in article.author_accounts.all():
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
        for a in article.author_accounts.all():
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
d.doi,
d.archiveNumber,
d.archiveCategory,
d.publicationDate,
d.acceptanceDate,
d.documentType,
d.submissionDate,
d.authorCod,
d.eoInChargeCod,
d.revisionDeadline,
d.language,
d.correctionType,
d.correctedDocumentCod,
d.oaaCod,
oa.oaaID,
d.copyCod,
cr.copyID,
co.collabName,
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
LEFT JOIN Open_Access_Assignment oa ON (oa.oaaCod=d.oaaCod)
LEFT JOIN Copyright cr ON (cr.copyCod=d.copyCod)
LEFT JOIN Collaboration co ON (co.collabCod=d.collabCod)
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
keywordName,
keywordWeightByAuthor
FROM Version_Keyword
LEFT JOIN Keyword USING (keywordCod)
WHERE
    versioncod=%(version_cod)s
"""
        cursor_keywords.execute(query_keywords, {"version_cod": version_cod})
        keywords = []
        for rk in cursor_keywords:
            keywords.append([rk["keywordName"], rk["keywordWeightByAuthor"]])
        cursor_keywords.close()
        return keywords

    def read_corrected_preprintid(self, document_cod):
        """Read article special issue."""
        cursor_corrected_preprintid = self.connection.cursor(dictionary=True)
        query_corrected_preprintid = """
SELECT
preprintid
FROM Document d
WHERE documentCod=%(document_cod)s
"""
        cursor_corrected_preprintid.execute(query_corrected_preprintid, {"document_cod": document_cod})
        corrected_preprintid = cursor_corrected_preprintid.fetchone()
        cursor_corrected_preprintid.close()
        return corrected_preprintid

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

            for f in err.supplementary_files.all():
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
            for ed in EditorDecision.objects.filter(review_round=rr):
                if ed.decision_editor_report_pdf:
                    ed.decision_editor_report_pdf.unlink_file()
                    ed.decision_editor_report_pdf.delete()
                ed.delete()

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

        if article.journal.code in ("JCAP"):
            for f in article.supplementary_files.all():
                f.file.unlink_file()
                f.delete()

        # TODO: the article supplementary files must be deleted only for NOT published
        if not publicationid and article.journal.code in ("JCOM", "JCOMAL"):
            for f in article.supplementary_files.all():
                f.file.unlink_file()
                f.delete()

        if not ArticleWorkflow.objects.filter(article=article).exists():
            ArticleWorkflow.objects.create(article=article)
            import_logic.logger.warning(
                f"created ArticleWorkflow during reset article {article}, "
                f"probably missing due to a crashed import, please check"
            )

        # delete  ArticleWorkflow's files
        for f in article.articleworkflow.supplementary_files_at_acceptance.all():
            import_logic.logger.warning(f"reset Supplementary at acceptance {f.id} {f} {f.file}")
            f.file.unlink_file()
            f.file.delete()
            f.delete()

        if article.articleworkflow.publication_galleys_source_file:
            article.articleworkflow.publication_galleys_source_file.delete()
            article.articleworkflow.publication_galleys_source_file = None
            article.articleworkflow.save()

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

        for wea in WjsEditorAssignment.objects.filter(article__id=article.id):
            if wea.editor_report_pdf_draft:
                wea.editor_report_pdf_draft.unlink_file()
                wea.editor_report_pdf_draft.delete()

        RevisionRequest.objects.filter(article__id=article.id).delete()
        ReviewAssignment.objects.filter(article__id=article.id).delete()
        EditorAssignment.objects.filter(article__id=article.id).delete()
        PastEditorAssignment.objects.filter(article__id=article.id).delete()
        submission_models.KeywordArticle.objects.filter(article__id=article.id).delete()
        # AttentionCondition is keyed to Article (not ArticleWorkflow, deleted below), so it
        # survives a re-import untouched otherwise - a stale event-based AC from a previous,
        # broken import attempt would never get cleaned up, since populate_attention_conditions
        # only resolves stale *time*-based codes outside the article's current state.
        AttentionCondition.objects.filter(article__id=article.id).delete()
        PermissionAssignment.objects.filter(
            object_id=article.articleworkflow.id,
        ).delete()
        article.articleworkflow.delete()

        submission_models.ArticleAuthorOrder.objects.filter(article__id=article.id).delete()
        article.authors.clear()

        ArticleCollaboration.objects.filter(article__id=article.id).delete()

        submission_models.ArticleStageLog.objects.filter(article__id=article.id).delete()
        article.stage = submission_models.STAGE_UNASSIGNED

        # for NOT published the identifier doi must be removed (temporary doi)
        if not publicationid or article.journal.code in ("JHEP", "JCAP"):
            identifiers_models.Identifier.objects.filter(
                article=article,
                id_type="doi",
            ).delete()

        if article.journal.code in ("JHEP", "JCAP"):
            ArticleSubmission.objects.filter(
                article=article,
            ).delete()
            for g in article.galley_set.all():
                g.file.unlink_file()
                g.file.delete()
                g.delete()

        # Note: the article object is not deleted, only the data are reset
        #
        # There are objects related to the article that are not deleted
        # because are reused:
        #
        # Frozen authors: not to delete
        # Galleys: not to delete but for JHEP JCAP
        # identifiers: not to delete but for JHEP JCAP
        # ProphyCandidates: not to delete. if resent to prophy they will be updated.

        # delete any file left over in the article's files
        for f in File.objects.filter(article_id=article.id):
            import_logic.logger.warning(f"reset article File entry: {f}")
            f.unlink_file()
            f.delete()

        # delete any file left over in the article's file directory (e.g. orphaned files not
        # tracked by any of the models cleaned up above)
        article_dir = os.path.join(settings.BASE_DIR, "files", "articles", str(article.id))
        if os.path.isdir(article_dir):
            for entry in os.scandir(article_dir):
                import_logic.logger.warning(f"reset article file dir: {entry}")
                os.remove(entry.path)

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
            self.eo_in_charge_identity = None
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
            # Stashed so repair_history_specific_article() can attribute a synthetic action to
            # this same EO without an ambiguous reverse Correspondence lookup - one EO account
            # can have more than one Correspondence row for the same source (e.g. after a wjapp
            # account merge), so filtering by source alone isn't guaranteed unique.
            self.eo_in_charge_identity = {
                "agentCod": row["eoInChargeCod"],
                "agentLastname": row["eoInCharge_lastname"],
                "agentFirstname": row["eoInCharge_firstname"],
                "agentEmail": row["eoInCharge_email"],
                "agentPrivacy": row["eoInCharge_privacy"],
            }

        if not main_author.check_role(self.journal, "author", staff_override=False):
            main_author.add_account_role("author", self.journal)
        article.owner = main_author

        # replace article.authors.add(main_author)
        submission_models.FrozenAuthor.get_or_snapshot_if_email_found(
            main_author.email,
            article,
        )

        article.correspondence_author = main_author
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

    def create_collaboration_from_wjapp(self, article, collab_name):
        """Associate the article with the collaboration imported from wjapp, if any."""

        if not collab_name:
            return

        wjs_collab_name = WJAPP_JCAP_COLLABORATION_NAME_MAPPING.get(collab_name)
        if not wjs_collab_name:
            wjs_collab_name = collab_name
            import_logic.logger.warning(
                f"No wjs mapping for wjapp collaboration {collab_name!r} (article {article.id}), "
                f"using the wjapp name as-is: please review WJAPP_JCAP_COLLABORATION_NAME_MAPPING."
            )

        collaboration, _ = Collaboration.objects.get_or_create(name=wjs_collab_name)
        ArticleCollaboration.objects.get_or_create(
            article=article,
            collaboration=collaboration,
            defaults={
                "order": Collaboration.next_collaboration_sort(article=article),
            },
        )

    def create_access_mode_from_wjapp(self, article, oaa_cod, copy_cod):

        # mapping wjapp cods with wjs access modes:
        # (oaa_cod, copy_cod) -> code, name, copyright_name, licence_name
        # Publisher licence is called most-rights licence
        #
        # wjapp cods:
        #
        # | copyCod | copyID  | copyName  |
        # +---------+---------+-----------+
        # |       1 | SI      | SISSA-IOP |
        # |       2 | CERN    | CERN      |
        # |       3 | AUTHORS | AUTHORS   |
        #
        # | oaaCod | name                                   |
        # +--------+----------------------------------------+
        # |      1 | Not Open Access                        |
        # |      2 | Open Access Paid by Author             |
        # |      3 | Addendum                               |
        # |      4 | Erratum                                |
        # |      5 | Special Issue                          |
        # |      6 | Free of charge                         |
        # |      7 | SCOAP                                  |
        # |      8 | Open Access - Transformative Agreement |

        wjs_mapping = {
            (None, None): ("not-open-access", "Not open access", "Publisher", "most-rights"),
            (1, 1): ("not-open-access", "Not open access", "Publisher", "most-rights"),
            (1, None): ("not-open-access", "Not open access", "Publisher", "most-rights"),
            (1, 3): ("not-open-access", "Not open access", "Authors", "most-rights"),
            (2, 1): ("open-access-paid", "Open Access (paid by author)", "Publisher", "most-rights"),
            (2, 3): ("open-access-paid", "Open Access (paid by author)", "Authors", "CC BY 4.0"),
            (2, 2): (
                "open-access-paid",
                "Open Access (paid by author)",
                "CERN (for the benefit of the collaboration)",
                "CC BY 4.0",
            ),
            # Not the same code as JHEP/JQuant/JCOM/JCOMAL's own "open-access" (user_selectable=True
            # there, a live submission choice for those journals): JCAP has no live "open access"
            # choice of its own, so reusing that code here would make AccessModeJournal link it to
            # JCAP too, and JCAP's own submission form would then wrongly offer it as selectable
            # (queried as parameters__journal=<journal>, user_selectable=True - see step7/forms.py).
            # "open-access-legacy" is JCAP-only and pre-seeded not user-selectable (issue #3169).
            (7, 3): ("open-access-legacy", "Open Access", "Authors", "CC BY 4.0"),
            (4, 1): ("free-to-read", "Free to read", "Publisher", "most-rights"),
            (4, None): ("free-to-read", "Free to read", "Authors", "CC BY 4.0"),
            (4, 3): ("free-to-read", "Free to read", "Authors", "CC BY 4.0"),
            (3, 3): ("free-to-read", "Free to read", "Authors", "CC BY 4.0"),
            (6, 3): ("free-to-read", "Free to read", "Authors", "CC BY 4.0"),
            (8, 3): (
                "oa-transformative-agreement",
                "Open Access - Transformative agreements",
                "Authors",
                "CC BY 4.0",
            ),
            (8, 1): (
                "oa-transformative-agreement",
                "Open Access - Transformative agreements",
                "Publisher",
                "most-rights",
            ),
        }

        # _name is kept only as documentation of the mapping (matching setup_access_mode.py's
        # display name for this code); no longer read since AccessMode.objects.get() below
        # looks up by code alone.
        code, _name, copyright_name, licence_name = wjs_mapping[(oaa_cod, copy_cod)]

        # setup_access_mode.py (issue #3169) pre-seeds every AccessMode/AccessModeJournal this
        # mapping can ever produce, with the correct user_selectable/licence/copyright - a
        # get_or_create() here was only ever a workaround for that not existing yet. A bare
        # .get() now means a missing row surfaces as a loud, specific error (environment not set
        # up, or a new (oaa_cod, copy_cod) combination not yet added to setup_access_mode.py)
        # instead of silently auto-creating a wrongly-configured one (e.g. user_selectable=True).
        mode = AccessMode.objects.get(code=code)
        licence = Licence.objects.get(short_name=licence_name, journal=article.journal)
        access_mode_journal = AccessModeJournal.objects.get(access_mode=mode, journal=article.journal)
        import_logic.logger.debug(f"{mode=} {article.journal=} {access_mode_journal=} {copyright_name=} {licence=}")
        return mode, licence, copyright_name

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

        if len(authors_bios) != article.author_accounts.count():
            import_logic.logger.warning(
                f"Authors bios paragraphs: {len(authors_bios)}, article authors: {article.author_accounts.count()}"
            )

        for author in article.author_accounts.all():
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
        seen_words = set()
        for kwd in keywords:
            import_logic.logger.debug(f"Keyword {kwd=}")
            # Janeway's keywords are a simple model with a "word" field for the kwd text
            kwd_word = kwd[0].strip()

            if len(kwd) > 1 and kwd[1] not in (None, ""):
                kwd_weight = kwd[1]
            else:
                import_logic.logger.warning(
                    f"Missing weight for keyword {kwd[0]!r} on article {article.pk}, defaulting to 0"
                )
                kwd_weight = 0

            kwd_weight = kwd[1]
            import_logic.logger.debug(f"Keyword {kwd_word=} {kwd_weight=}")
            kwd_word = import_logic.normalize_keyword(self.journal.code, kwd_word)

            if kwd_word in seen_words:
                # wjapp's raw keyword list can repeat the exact same keyword (e.g.
                # JCAP003A0503's "CMBR theory", listed twice). KeywordArticle's real DB
                # constraint is unique on (keyword_id, article_id) only - not order - so
                # re-processing a repeat here would hit that constraint on create(), since
                # update_or_create()'s lookup below (which includes order) never matches the
                # row created by the first occurrence.
                import_logic.logger.warning(
                    f'Duplicate keyword "{kwd_word}" for {article.get_identifier("preprintid")}, skipping repeat.'
                )
                continue
            seen_words.add(kwd_word)
            order = order + 1

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

            submission_models.KeywordArticle.objects.update_or_create(
                article=article,
                keyword=keyword,
                order=order,
                defaults={"weight": kwd_weight},
            )

            import_logic.logger.debug(f"Keyword {kwd_word} {kwd_weight} set at order {order}")
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
        authors_to_check = [a for a in article.author_accounts.all() if a != article.owner]
        authors_modified = False
        for author in authors_to_check:
            if author not in current_coauthors:
                article.authors.remove(author)
                # Remember that there is a pre-delete signal on FrozenAuthors that removes all authors/authors-order
                # however, we prefer to delete them explicitly because it's more clear
                submission_models.ArticleAuthorOrder.objects.filter(author=author, article=article).delete()
                # filter insted of get because frozen author could not exist i.e. JCOM_007A_0516
                submission_models.FrozenAuthor.objects.filter(article=article, author=author).delete()
                authors_modified = True
                import_logic.logger.debug(f"cleaned coauthor {author}")

        if authors_modified:
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
