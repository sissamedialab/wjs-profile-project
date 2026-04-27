"""Libray or functions that can be run on a just-accepted article.

They should verify if the paper might have issues that would prevent a typesetter from taking it in charge.

"""

from django.core.exceptions import ObjectDoesNotExist
from plugins.wjs_submission.settings import OA_CODE_TA
from submission import models as submission_models
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import get_eo_user, render_template


# TODO: might want to refactor with checks.always_assign(), but I need a stub here
# see specs#684
def always_pass(article: submission_models.Article) -> bool:
    """Do not perform any check."""
    return True


def jcap_ta_not_yet_confirmed(article: submission_models.Article) -> bool:
    """Block automatic transition to READY_FOR_TYPESETTER for JCAP TA articles.

    Only blocks if the article's access mode is TA (OA_CODE_TA). In that case,
    the EO must manually confirm production readiness via the article page.
    Returns False and notifies EO. For all other access modes, returns True.
    """
    try:
        access_mode = article.submission_data.access_mode
    except ObjectDoesNotExist:
        return True

    if access_mode is None or access_mode.code != OA_CODE_TA:
        return True

    # Local imports to avoid circular import: wjs_review.models is not fully
    # initialized when this module is loaded (e.g. via import_string for always_pass).
    from .. import communication_utils
    from ..models import Message

    context = {"article": article}

    message_subject = render_template(
        get_setting(
            setting_group_name="wjs_review",
            setting_name="jcap_ta_pending_subject",
            journal=article.journal,
        ).processed_value,
        context,
    )

    message_body = render_template(
        get_setting(
            setting_group_name="wjs_review",
            setting_name="jcap_ta_pending_body",
            journal=article.journal,
        ).processed_value,
        context,
    )

    communication_utils.log_operation(
        article=article,
        message_subject=message_subject,
        message_body=message_body,
        actor=None,
        recipients=[get_eo_user(article)],
        verbosity=Message.MessageVerbosity.FULL,
    )
    return False
