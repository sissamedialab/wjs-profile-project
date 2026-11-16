"""Replace the JCOM-specific Editor decline reason with a generic one (specs#3306).

``JCOM_BUSY`` ("Already too busy with JCOM editorial work") was wrong on every journal but JCOM: it is replaced by
``BUSY_EDITORIAL`` ("Too busy with other editorial tasks") for all journals. Past editor assignments declined with
the old code are moved to the new one; the reverse operation moves them back.
"""

from django.db import migrations, models

OLD_CODE = "JCOM_BUSY"
NEW_CODE = "BUSY_EDITORIAL"


def _move(apps, old: str, new: str) -> None:
    """Rewrite ``decline_reason`` from ``old`` to ``new`` on every past editor assignment."""
    PastEditorAssignment = apps.get_model("wjs_review", "PastEditorAssignment")
    PastEditorAssignment.objects.filter(decline_reason=old).update(decline_reason=new)


def forwards(apps, schema_editor):
    """Move ``JCOM_BUSY`` rows to ``BUSY_EDITORIAL``."""
    _move(apps, OLD_CODE, NEW_CODE)


def backwards(apps, schema_editor):
    """Move ``BUSY_EDITORIAL`` rows back to ``JCOM_BUSY``."""
    _move(apps, NEW_CODE, OLD_CODE)


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0024_directors_sign_as_director"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pasteditorassignment",
            name="decline_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Select a decline reason"),
                    ("BUSY_EDITORIAL", "Too busy with other editorial tasks"),
                    ("BUSY", "Too busy in general"),
                    ("OUTSIDE_EXPERTISE", "Paper outside my area of expertise"),
                    ("NO_REVIEWER", "Unable to find an appropriate reviewer"),
                    ("OTHER", "Other"),
                ],
                null=True,
                verbose_name="Decline reason",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
