from typing import Optional

from django.conf import settings
from django.utils.module_loading import import_string
from events import logic as events_logic
from review import models as review_models
from submission import models as submission_models
from submission.models import Article
from utils.logger import get_logger

from wjs.jcom_profile import permissions as base_permissions

from .. import communication_utils
from ..logic import CreateReviewRound, VerifyProductionRequirements
from ..models import (
    ArticleWorkflow,
    Message,
    ProphyAccount,
    ProphyCandidate,
    WjsEditorAssignment,
)
from ..plugin_settings import STAGE
from ..prophy import Prophy
from . import ReviewEvent
from .assignment import dispatch_assignment, dispatch_eo_assignment

logger = get_logger(__name__)


def on_article_submitted(**kwargs) -> None:
    """Sync ArticleWorkflow state with article on submission."""
    article = kwargs["article"]
    if article.stage == STAGE and article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION:
        article.articleworkflow.author_submits_paper()
        article.articleworkflow.save()
        kwargs = {"workflow": article.articleworkflow}
        review_models.ReviewRound.objects.create(article=article, round_number=1)
        events_logic.Events.raise_event(ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED, task_object=article, **kwargs)


def on_workflow_submitted(**kwargs) -> None:
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


def on_revision_complete(**kwargs) -> None:
    """
    When a new article revision is submitted, start the revision process again.

    State is reset to EDITOR_SELECTED and a new review round is created unless the revision is a technical revision.
    """
    article = kwargs["revision"].article
    article.articleworkflow.author_submits_again()
    if kwargs["revision"].type != ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        assignment = WjsEditorAssignment.objects.get_current(article)
        CreateReviewRound(assignment=assignment).run()
    article.articleworkflow.save()
    article.stage = submission_models.STAGE_ASSIGNED
    # NB: STAGE_ASSIGNED is the correct stage here, because the other candidate STAGE_UNDER_REVIEW is set by
    # review.logic.quick_assign() only when a review assigment is created.
    article.save()


def log_author_uploads_revision(**kwargs) -> Message:
    """Log a message when the author uploads a revision.

    This is used because we let Janeway manage the upload of a revision.
    """
    revision_request = kwargs.pop("revision")
    article = revision_request.article
    actor = article.correspondence_author
    editor = revision_request.editor
    message = communication_utils.log_operation(
        article=article,
        message_subject="Author submits revision.",
        message_body=revision_request.author_note,
        actor=actor,
        recipients=[editor],
        message_type=Message.MessageTypes.STD,
        hijacking_actor=base_permissions.get_hijacker(),
        notify_actor=communication_utils.should_notify_actor(),
    )
    return message


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
    article: Article = kwargs["article"]
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
