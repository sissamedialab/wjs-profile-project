from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0040_staffworkloadparameters_disabled_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jcomprofile",
            name="career_stage",
            field=models.IntegerField(
                choices=[
                    (0, "Student (i.e. pre-PhD)"),
                    (1, "Early career (e.g. postdoc, fixed-term contract)"),
                    (2, "Tenure track"),
                    (3, "Permanent (tenured or indefinite contract)"),
                ],
                null=True,
                blank=True,
            ),
        ),
    ]
