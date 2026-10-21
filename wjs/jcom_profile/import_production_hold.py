"""Production-readiness hold of the papers imported from wjapp (specs#3131).

Kept apart from import_logic: importing that module patches the review logic for the whole process
(e.g. ``EvaluateReview._log_decline``), so these helpers must be usable, and testable, without it.
"""

import contextlib
import datetime

import freezegun
from django.contrib.contenttypes.models import ContentType
from plugins.wjs_review import ac_service
from plugins.wjs_review.events.checks_after_acceptance import (
    render_production_readiness_subject,
)
from plugins.wjs_review.logic__production import VerifyProductionRequirements
from plugins.wjs_review.models import ArticleWorkflow, Message, MessageRecipients
from submission import models as submission_models
from utils.logger import get_logger

logger = get_logger(__name__)


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


def release_production_readiness_hold(
    article: submission_models.Article, when: datetime.datetime | None = None
) -> bool:
    """Let through an article still in ACCEPTED when the replayed wjapp history goes on (specs#3131).

    The replayed wjapp acceptance holds every imported JCAP paper in ACCEPTED; when the wjapp history
    goes on, the paper had already left that state in wjapp and the import must not stop. Any article
    found in ACCEPTED at this point is released, whatever held it there. The release
    follows the automatic path of VerifyProductionRequirements: FSM transition (which sends the
    production zip to the publisher) and "ready for typesetter" timeline message.

    :param when: date of the replayed wjapp action; like the other replayed actions, what the release
        logs is dated then instead of when the import runs.
    :return: True if the article was released, False if it was not in ACCEPTED.
    """
    workflow = article.articleworkflow
    if workflow.state != ArticleWorkflow.ReviewStates.ACCEPTED:
        return False
    with freezegun.freeze_time(when) if when else contextlib.nullcontext():
        workflow.system_verifies_production_requirements()
        workflow.save()
        VerifyProductionRequirements(workflow).log_ready_for_typesetter()
        ac_service.resolve_all_for_article(article, codes=[ac_service.ACCESS_MODE_TO_CHECK])
    logger.warning(f"Released {article.id} from ACCEPTED to READY_FOR_TYPESETTER: the wjapp history goes on")
    return True


def finalize_production_readiness_hold(article: submission_models.Article) -> None:
    """Settle the production-readiness hold once the import of an article is complete (specs#3131).

    An article still in ACCEPTED is held for the EO: the message asking to confirm production readiness
    is flagged unread again (the import marks every message read). In any other state nothing of the
    hold must be left behind, so its attention condition is resolved.
    """
    if article.articleworkflow.state != ArticleWorkflow.ReviewStates.ACCEPTED:
        ac_service.resolve_all_for_article(article, codes=[ac_service.ACCESS_MODE_TO_CHECK])
        return
    held_messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
        subject=render_production_readiness_subject(article),
    )
    MessageRecipients.objects.filter(message__in=held_messages).update(read=False)
    held_messages.update(read_by_eo=False)
