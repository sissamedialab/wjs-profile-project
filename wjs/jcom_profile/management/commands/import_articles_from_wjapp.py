"""Import article from wjapp."""

import datetime

import freezegun
import mariadb
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
from review.models import ReviewRound
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
    pass


logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Connect to wjApp jcom database and read article data."  # NOQA A003

    def handle(self, *args, **options):
        """Command entry point."""
        if not getattr(settings, "NO_NOTIFICATION", None):
            self.stderr.write("Notifications are enabled, not importing to avoid spamming")
            return
        self.options = options
        for journal_code in ("JCOM",):
            journal = Journal.objects.get(code=journal_code)
            self.journal_data = JOURNALS_DATA[journal_code]
            self.read_data_article(journal, **options)

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--preprintid",
            default="",
            help="jcom wjApp preprintid ex: JCOM_010A_0324",
            required=True,
        )

    def read_data_article(self, journal, **options):
        """Process one article."""
        preprintid = self.options["preprintid"]
        if not preprintid:
            return
        setting = f"WJAPP_{journal.code.upper()}_IMPORT_CONNECTION_PARAMS"
        connection_parameters = getattr(settings, setting, None)
        if connection_parameters is None:
            logger.debug(f'Unknown journal {journal.code}. Please ensure "{setting}" exists in settings.')
        else:
            connection = mariadb.connect(**connection_parameters)
            cursor = connection.cursor(dictionary=True)
            query = f"""
SELECT
d.preprintId,
d.documentType,
d.submissionDate,
d.authorCod,
u1.lastname AS author_lastname,
u1.firstname AS author_firstname,
u1.email AS author_email,
d.editorCod,
d.editorAssignDate,
u2.lastname AS editor_lastname,
u2.firstname AS editor_firstname,
u2.email AS editor_email,
v.versionCod,
v.versionNumber,
v.versionTitle,
v.versionAbstract
FROM Document d
LEFT JOIN User u1 ON (d.authorCod=u1.userCod)
LEFT JOIN User u2 ON (d.editorCod=u2.userCod)
LEFT JOIN Version v ON (v.documentCod=d.documentCod)
WHERE
    v.isCurrentVersion=1
AND d.preprintId='{preprintid}'
"""
            logger.debug(query)
            cursor.execute(
                query,
            )
            row = cursor.fetchone()
            preprintid = row["preprintId"]
            section = row["documentType"]
            submission_date = row["submissionDate"]
            author_cod = row["authorCod"]
            author_last_name = row["author_lastname"]
            author_first_name = row["author_firstname"]
            author_email = row["author_email"]
            editor_cod = row["editorCod"]
            editor_assign_date = row["editorAssignDate"]
            editor_last_name = row["editor_lastname"]
            editor_first_name = row["editor_firstname"]
            editor_email = row["editor_email"]
            title = row["versionTitle"]
            abstract = row["versionAbstract"]
            version_cod = row["versionCod"]
            version_number = row["versionNumber"]
            if row:
                logger.debug(
                    f"""
preprint: {preprintid}
version number: {version_number}
submission_date: {submission_date}
title: {title}
author: {author_cod} {author_last_name} {author_first_name} {author_email}
editor: {editor_cod} {editor_last_name} {editor_first_name} {editor_email}
editor_assign_date: {editor_assign_date}
abstract: {abstract}
section: {section}
""",
                )

                article, preprintid, editor = self.create_article(journal, row)
                self.set_section(article, section)
                logger.debug(article.id)
                logger.debug(preprintid)
                logger.debug(article)
            cursor.close()

            # keywords
            cursor_keywords = connection.cursor(dictionary=True)
            query_keywords = f"""
SELECT
keywordName
FROM Version_Keyword
LEFT JOIN Keyword USING (keywordCod)
WHERE
    versioncod={version_cod}
"""
            logger.debug(query_keywords)
            cursor_keywords.execute(
                query_keywords,
            )
            keywords = []
            for rk in cursor_keywords:
                keywords.append(rk["keywordName"])
            logger.debug(f"Keywords: {keywords}")
            self.set_keywords(article, keywords)
            cursor_keywords.close()

            # Create the review round until current version
            for i in range(1, version_number + 1):
                review_round = ReviewRound.objects.get_or_create(article=article, round_number=i)
                logger.debug(f"Review Round: {review_round}")

            # use current review round
            # reviewers
            cursor_reviewers = connection.cursor(dictionary=True)
            query_reviewers = f"""
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
    versioncod={version_cod}
ORDER BY assignDate
"""
            logger.debug(query_reviewers)
            cursor_reviewers.execute(
                query_reviewers,
            )
            for reviewer_data in cursor_reviewers:
                logger.debug(f"Reviewer: {reviewer_data}")
                self.set_reviewer(article, editor, reviewer_data)
            cursor_reviewers.close()

            connection.close()
        return

    def create_article(self, journal, row):
        """Create the article."""
        preprintid = row["preprintId"]
        logger.debug(f"Creating {preprintid}")
        article = submission_models.Article.get_article(
            journal=journal,
            identifier_type="preprintid",
            identifier=preprintid,
        )
        if article:
            # This is not the default situation: if we are here it
            # means that the article has been already imported and
            # that we are re-importing.
            logger.warning(
                f"Re-importing existing article {preprintid} at {article.id} "
                f"The {article.id} here will disappear because of the delete() below"
            )
            article.manuscript_files.all().delete()
            article.data_figure_files.all().delete()
            article.supplementary_files.all().delete()
            article.source_files.all().delete()
            article.galley_set.all().delete()
            article.delete()

        article = submission_models.Article.objects.create(
            journal=journal,
        )
        article.title = row["versionTitle"]
        article.abstract = row["versionAbstract"]
        article.imported = True
        # date str ex: 2024-03-29 10:51:52,406
        date_string = str(row["submissionDate"])
        article.date_submitted = rome_timezone.localize(datetime.datetime.fromisoformat(date_string))
        article.save()
        logger.debug(f"article id: {article.id}")
        main_author = self.account_get_or_create_check_correspondence(
            row["authorCod"],
            row["author_lastname"],
            row["author_firstname"],
            row["author_email"],
            journal,
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
        editor = self.account_get_or_create_check_correspondence(
            row["editorCod"],
            row["editor_lastname"],
            row["editor_firstname"],
            row["editor_email"],
            journal,
        )
        logger.debug(f"Editor {editor.last_name} {editor.first_name} onto {article.pk}")
        request = create_fake_request(user=None, journal=journal)
        request.user = editor

        if not editor.check_role(request.journal, "section-editor"):
            editor.add_account_role("section-editor", journal)

        logger.debug(f"Editor is section-editor: {editor.check_role(request.journal, 'section-editor')}")

        # Manually move into a state where editor assignment can take place
        # TODO: check if this is not the case already...
        article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        article.articleworkflow.save()
        logger.debug(f"workflow state  {article.articleworkflow.state} article stage {article.stage}")

        logger.debug(f"NO NOTIFICATION: {getattr(settings, 'NO_NOTIFICATION', None)}")

        with freezegun.freeze_time(
            rome_timezone.localize(datetime.datetime.fromisoformat(str(row["editorAssignDate"]))),
        ):
            AssignToEditor(
                article=article,
                editor=editor,
                request=request,
            ).run()
            article.save()
        article.refresh_from_db()
        return (article, preprintid, editor)

    def account_get_or_create_check_correspondence(self, user_cod, last_name, first_name, imported_email, journal):
        """get a user account - check Correspondence and eventually create new account"""
        # Check if we know this person form some other journal or by email
        source = journal.code.lower()
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
            logger.debug(f"user exists: {imported_email}")
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
            journal=article.journal,
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
            logger.debug(f" {article.journal.code.upper()} ")
            if article.journal.code.upper() == "JCOMAL":
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
            article.journal.keywords.add(keyword)

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
            article.journal,
        )

        request = create_fake_request(user=None, journal=article.journal)
        request.user = editor

        logger.debug(f"article workflow: {article.articleworkflow}")

        with freezegun.freeze_time(
            rome_timezone.localize(datetime.datetime.fromisoformat(str(reviewer_data["refereeAssignDate"]))),
        ):
            # default message from settings
            # TO CHECK: add mail subject
            # TO CHECK: missing signature in the final message request.user.signature
            default_message_rendered = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="review_invitation_message",
                journal=article.journal,
                request=request,
                context={
                    "article": article,
                    "request": request,
                },
                template_is_setting=True,
            )
            logger.debug(f"invitation review message: {default_message_rendered}")
            logger.debug(f"editor in charge: {request.user.signature}")
            interval_days = get_setting(
                "wjs_review",
                "acceptance_due_date_days",
                article.journal,
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
                request = create_fake_request(user=None, journal=article.journal)
                request.user = reviewer

                with freezegun.freeze_time(
                    rome_timezone.localize(datetime.datetime.fromisoformat(str(reviewer_data["refereeAcceptDate"]))),
                ):
                    logger.debug(f"review assignment: {review_assignment}")
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
                    logger.debug(f"Review Assignment date_due: {review_assignment.date_due}")
            logger.debug(f"Review Round: {review_assignment.review_round}")

        return review_assignment
