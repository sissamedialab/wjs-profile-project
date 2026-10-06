import logging

from django.db import migrations
from django.template import Context, Template
from journal.models import Journal
from review.const import EditorialDecisions
from utils.setting_handler import get_setting

logger = logging.getLogger(__name__)


def _system_user(Account, journal):
    """Return the system user of the journal, or None if it cannot be found."""
    email = get_setting("general", "support_email", journal=journal).value
    return Account.objects.filter(email=email).first()


def set_system_actor_on_appeal_granted(apps, schema_editor):
    """Set the system user as actor of existing "Appeal granted" messages (specs#2903)."""
    Account = apps.get_model("core", "Account")
    Article = apps.get_model("submission", "Article")
    ContentType = apps.get_model("contenttypes", "ContentType")
    EditorDecision = apps.get_model("wjs_review", "EditorDecision")
    Message = apps.get_model("wjs_review", "Message")

    article_ct = ContentType.objects.get_for_model(Article)
    article_ids = (
        EditorDecision.objects.filter(decision=EditorialDecisions.OPEN_APPEAL.value)
        .values_list("workflow__article_id", flat=True)
        .distinct()
    )
    for article in Article.objects.filter(pk__in=article_ids):
        if article.correspondence_author_id is None:
            continue
        # get_setting needs a "real" (non-historical) Journal instance to run its FK lookups (it only reads
        # journal.pk); ".only" limits the loaded columns so a future field added to Journal can't slow this
        # migration down or break it, keeping "code" too as a minimal safety margin for callers that log it.
        journal = Journal.objects.only("pk", "code").get(pk=article.journal_id)
        system_user = _system_user(Account, journal)
        if system_user is None:
            continue
        subject_template = get_setting("wjs_review", "eo_opens_appeal_subject", journal=journal).value
        subject = Template(subject_template).render(Context({"article": article})).strip()
        appeal_granted_messages = Message.objects.filter(
            content_type=article_ct,
            object_id=article.pk,
            subject=subject,
            recipients=article.correspondence_author_id,
        )
        if not appeal_granted_messages.exists():
            # Fail-safe skip (e.g. a journal override of the subject with other variables): make it visible.
            logger.warning(
                "0020: no 'Appeal granted' message matched for article %s (subject %r)", article.pk, subject
            )
            continue
        updated = appeal_granted_messages.exclude(actor=system_user).update(actor=system_user)
        logger.info("0020: article %s: %s 'Appeal granted' message(s) moved to the system user", article.pk, updated)


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0019_pasteditorassignment_on_appeal"),
    ]

    operations = [
        migrations.RunPython(set_system_actor_on_appeal_granted, migrations.RunPython.noop),
    ]
