from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wjs_review", "0025_editor_decline_reason_busy_editorial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attentioncondition",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("dismissed", "Dismissed"),
                    ("resolved", "Resolved"),
                    ("expired", "Expired"),
                    ("suppressed", "Suppressed"),
                ],
                default="active",
                max_length=16,
            ),
        ),
    ]
