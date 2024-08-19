"""Events-related functions."""

from django.urls import reverse
from utils.logger import get_logger

from wjs.jcom_profile.utils import render_template_from_setting

logger = get_logger(__name__)


def notify_coauthors_article_submission(**kwargs):
    """Notify co-authors of submission."""
    # FIXME: This logic is intended to be insert in janeway; this is a copy-paste of janeway
    #  src/utils/transitional_email.send_submission_acknowledgement function, with the difference that we want to
    #  notify coauthors
    article = kwargs["article"]
    request = kwargs["request"]
    coauthors = [c for c in article.authors.all() if c != article.correspondence_author]

    # generate URL
    review_unassigned_article_url = request.journal.site_url(
        path=reverse(
            "review_unassigned_article",
            kwargs={"article_id": article.pk},
        ),
    )

    # FIXME: We are introducing a dependency between jcom_profile and wjs_review!!!
    from plugins.wjs_review.communication_utils import log_operation

    context = {
        "article": article,
        "request": request,
        "review_unassigned_article_url": review_unassigned_article_url,
    }
    message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="submission_coauthors_acknowledgement_subject",
        journal=article.journal,
        request=request,
        context=context,
        template_is_setting=True,
    )
    message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="submission_coauthors_acknowledgement_body",
        journal=article.journal,
        request=request,
        context=context,
        template_is_setting=True,
    )

    log_operation(
        article=article,
        message_subject=message_subject,
        message_body=message_body,
        recipients=coauthors,
        flag_as_read=False,
        flag_as_read_by_eo=True,
    )
