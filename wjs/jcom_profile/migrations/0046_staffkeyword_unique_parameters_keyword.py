from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0045_clear_salutation"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="staffkeyword",
            options={
                "ordering": ["parameters", "keyword"],
                "verbose_name": "Staff keyword",
                "verbose_name_plural": "Staff keywords",
            },
        ),
        migrations.AddConstraint(
            model_name="staffkeyword",
            constraint=models.UniqueConstraint(
                fields=("parameters", "keyword"),
                name="unique_staffkeyword_parameters_keyword",
            ),
        ),
    ]
