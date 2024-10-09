"""Handlers functions.

Generally registered onto some event in the `app` module.
"""

from typing import Optional

from django.conf import settings
from django.utils.module_loading import import_string
from events import logic as events_logic
from submission import logic as submission_logic
from submission import models as submission_models
from utils import setting_handler
from utils.logger import get_logger

from wjs.jcom_profile.utils import render_template_from_setting

from .. import communication_utils
from ..logic import (
    ConvertManuscriptToPdf,
    CreateReviewRound,
    VerifyProductionRequirements,
)
from ..models import (
    ArticleWorkflow,
    ProphyAccount,
    ProphyCandidate,
    WjsEditorAssignment,
)
from ..plugin_settings import STAGE
from ..prophy import Prophy
from . import ReviewEvent
from .assignment import dispatch_assignment, dispatch_eo_assignment

logger = get_logger(__name__)


def sync_article_articleworkflow(**kwargs) -> None:
    """Sync ArticleWorkflow state with article on submission."""
    article = kwargs["article"]
    if article.stage == STAGE and article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION:
        article.articleworkflow.author_submits_paper()
        article.articleworkflow.save()
        kwargs = {"workflow": article.articleworkflow}
        events_logic.Events.raise_event(ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED, task_object=article, **kwargs)


def on_article_submission_start(**kwargs) -> None:
    """Assign current user as main author."""
    article = kwargs["article"]
    request = kwargs["request"]
    user_automatically_author = setting_handler.get_setting(
        "general",
        "user_automatically_author",
        request.journal,
    ).processed_value
    user_automatically_main_author = setting_handler.get_setting(
        "general",
        "user_automatically_main_author",
        request.journal,
    ).processed_value

    if user_automatically_main_author and user_automatically_author:
        submission_logic.add_user_as_author(request.user, article)
        if user_automatically_main_author:
            article.correspondence_author = request.user
            article.save()


def process_submission(**kwargs) -> None:
    """When ArticleWorkflow is marked as submitted, the process filtering tasks are run."""
    workflow = kwargs["workflow"]
    workflow.system_process_submission()
    workflow.save()


def dispatch_checks(article: submission_models.Article) -> Optional[bool]:
    """
    Run sanity checks on article.

    If checks are successful, dispatch assignment to editor and return True is assignment is created, False otherwise.

    If checks are unsuccessful, return None.

    :py:function:`wjs_review.events.handlers.dispatch_checks` run functions registered per journal in
    `settings.WJS_REVIEW_CHECK_FUNCTIONS`: if any fails, the whole check is considered failed.
    """
    journal = article.journal.code
    checks_functions = settings.WJS_REVIEW_CHECK_FUNCTIONS.get(
        journal,
        settings.WJS_REVIEW_CHECK_FUNCTIONS.get(None, []),
    )
    for check_function in checks_functions:
        status = import_string(check_function)(article)
        if not status:
            return None

    assignment = dispatch_assignment(article=article)
    dispatch_eo_assignment(article=article)
    return bool(assignment)


def restart_review_process_after_revision_submission(**kwargs) -> None:
    """
    When a new article revision is submitted, start the revision process again.

    State is reset to EDITOR_SELECTED and a new review round is created unless the revision is a technical revision.
    """
    article = kwargs["revision"].article
    if article.articleworkflow.state == ArticleWorkflow.ReviewStates.UNDER_APPEAL:
        article.articleworkflow.author_submits_appeal()
    else:
        article.articleworkflow.author_submits_again()
    if kwargs["revision"].type != ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        assignment = WjsEditorAssignment.objects.get_current(article)
        CreateReviewRound(assignment=assignment).run()
    article.articleworkflow.save()
    article.stage = submission_models.STAGE_ASSIGNED
    # NB: STAGE_ASSIGNED is the correct stage here, because the other candidate STAGE_UNDER_REVIEW is set by
    # review.logic.quick_assign() only when a review assigment is created.
    article.save()


def notify_author_article_submission(**kwargs):
    """Notify the corresponding author of submission."""
    # This overrides janeway's
    # src/utils/transitional_email.send_submission_acknowledgement
    # which should be "unregistered" from the event ON_ARTICLE_SUBMITTED in
    # wjs_review.app

    article = kwargs["article"]
    request = kwargs["request"]

    context = {
        "article": article,
        "request": request,
    }
    message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_submission_acknowledgement",
        journal=article.journal,
        request=request,
        context=context,
        template_is_setting=True,
    )

    message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="submission_acknowledgement",
        journal=article.journal,
        request=request,
        context=context,
        template_is_setting=True,
    )

    communication_utils.log_operation(
        article=article,
        message_subject=message_subject,
        message_body=message_body,
        recipients=[article.correspondence_author],
        flag_as_read=True,
        flag_as_read_by_eo=True,
    )


def notify_coauthors_article_submission(**kwargs):
    """Notify co-authors of submission."""
    # FIXME: This logic is intended to be insert in janeway; this is a copy-paste of janeway
    #  src/utils/transitional_email.send_submission_acknowledgement function, with the difference that we want to
    #  notify coauthors
    article = kwargs["article"]
    request = kwargs["request"]
    if article.authors.count() == 1:
        # no co-authors (only the correspondence author)
        return

    context = {
        "article": article,
        "request": request,
    }
    message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="submission_coauthors_acknowledgement_subject",
        journal=article.journal,
        request=request,
        context=context,
        template_is_setting=True,
    )

    # Send per-coauthor customized notifications
    coauthors = [c for c in article.authors.all() if c != article.correspondence_author]
    for coauthor in coauthors:
        # we call the recipient "author" because thus the template is easier to read
        context["author"] = coauthor
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="submission_coauthors_acknowledgement_body",
            journal=article.journal,
            request=request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=article,
            message_subject=message_subject,
            message_body=message_body,
            recipients=[coauthor],
            flag_as_read=False,
            flag_as_read_by_eo=True,
        )


def send_to_prophy(**kwargs) -> None:
    """Send article to prophy."""
    # This function can be called by different event handlers. Upon submission events, we get a `article` kwarg, but
    # upon revision-submission events we get a `revision` kwarg.
    if "article" in kwargs:
        article = kwargs["article"]
    elif "revision" in kwargs:
        article = kwargs["revision"].article
    else:
        logger.error("unexpected missing article")
        return
    p = Prophy(article)
    p.async_article_prophy_upload()
    return


def perform_checks_at_acceptance(**kwargs):
    """Check if a paper can go to the workflow state READY_FOR_TYPESETTER.

    This function should be called just after the paper has been accepted.
    """
    article: submission_models.Article = kwargs["article"]
    if article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED:
        VerifyProductionRequirements(article.articleworkflow).run()
    else:
        logger.error(
            f"Wrong signal call: attempting to perform acceptance checks on article {article.pk}"
            " in state {article.articleworkflow.state}. Please check your signal registrations.",
        )


def clean_prophy_candidates(**kwargs) -> None:
    """Clean Prophy candidates for article published, rejected or not suitable."""
    article = kwargs["article"]
    if article.articleworkflow.state in (
        ArticleWorkflow.ReviewStates.PUBLISHED,
        ArticleWorkflow.ReviewStates.REJECTED,
        ArticleWorkflow.ReviewStates.NOT_SUITABLE,
    ):
        ProphyCandidate.objects.filter(
            article=article.id,
        ).delete()
        ProphyAccount.objects.filter(prophycandidate__isnull=True).delete()


def convert_manuscript_to_pdf(**kwargs) -> None:
    """This responds to ON_ARTICLE_FILE_UPLOAD Event coming from Janeway's submission module."""
    article = kwargs["article"]
    file_type = kwargs["file_type"]

    if file_type == "manuscript":
        ConvertManuscriptToPdf(article).run()
    elif file_type == "data":
        pass
