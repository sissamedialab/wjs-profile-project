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


def _is_ta(article: submission_models.Article) -> bool:
    """Return whether the article's access mode is the transformative-agreement one."""
    try:
        access_mode = article.submission_data.access_mode
    except ObjectDoesNotExist:
        return False
    return access_mode is not None and access_mode.code == OA_CODE_TA


def _is_imported(article: submission_models.Article) -> bool:
    """Return whether the article was imported from wjapp, i.e. it has a populated preprintid."""
    return bool(article.get_identifier("preprintid"))


def _render_setting(setting_name: str, article: submission_models.Article) -> str:
    """Render a wjs_review setting with the article in the context."""
    return render_template(
        get_setting(
            setting_group_name="wjs_review", setting_name=setting_name, journal=article.journal
        ).processed_value,
        {"article": article},
    )


def render_production_readiness_subject(article: submission_models.Article) -> str:
    """Return the subject of the message sent to the EO when an article is held in ACCEPTED.

    Also used by the wjapp importer to find that message (specs#3131).
    """
    return _render_setting("jcap_ta_pending_subject", article)


def jcap_production_readiness_not_confirmed(article: submission_models.Article) -> bool:
    """Block automatic transition to READY_FOR_TYPESETTER for JCAP TA and imported articles.

    Blocks if the article's access mode is TA (OA_CODE_TA, specs#2591) or if the article was
    imported from wjapp (populated preprintid, specs#3131): imported articles have no affiliation
    and the EO must set it before production. In both cases the EO must manually confirm
    production readiness via the article page. Returns False and notifies EO; otherwise returns True.
    """
    if not (_is_ta(article) or _is_imported(article)):
        return True

    # Local imports to avoid circular import: wjs_review.models is not fully
    # initialized when this module is loaded (e.g. via import_string for always_pass).
    from .. import communication_utils
    from ..models import Message

    communication_utils.log_operation(
        article=article,
        message_subject=render_production_readiness_subject(article),
        message_body=_render_setting("jcap_ta_pending_body", article),
        actor=None,
        recipients=[get_eo_user(article)],
        verbosity=Message.MessageVerbosity.FULL,
    )
    return False
