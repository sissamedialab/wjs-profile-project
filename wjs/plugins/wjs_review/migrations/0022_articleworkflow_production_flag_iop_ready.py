from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0021_automaticassignmentaudit"),
    ]

    operations = [
        migrations.AddField(
            model_name="articleworkflow",
            name="production_flag_iop_ready",
            field=models.BooleanField(
                default=False,
                verbose_name="The publisher confirmed that the paper can be published",
            ),
        ),
    ]
