"""Resolve appeal ACs left active on papers no longer under appeal (specs#3174).

Until specs#3174, submitting an appeal did not resolve APPEAL_TO_SUBMIT
(author) and APPEAL_LATE (EO). APPEAL_TO_SUBMIT is event-based, so neither the
stale-AC cleanup nor the nightly rebuild healed those rows.
"""

from django.db import migrations

APPEAL_CODES = ("appeal_to_submit", "appeal_late")
UNDER_APPEAL = "UnderAppeal"


def resolve_stale_appeal_acs(apps, schema_editor):
    """Mark as resolved the active appeal ACs of papers not in UnderAppeal."""
    AttentionCondition = apps.get_model("wjs_review", "AttentionCondition")
    AttentionCondition.objects.filter(code__in=APPEAL_CODES, status="active").exclude(
        article__articleworkflow__state=UNDER_APPEAL,
    ).update(status="resolved")


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0017_blacklisted_authoremail"),
    ]

    operations = [
        migrations.RunPython(resolve_stale_appeal_acs, migrations.RunPython.noop),
    ]
