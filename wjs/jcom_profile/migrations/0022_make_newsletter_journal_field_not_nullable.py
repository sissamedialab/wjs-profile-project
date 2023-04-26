# Generated by Django 1.11.29 on 2023-04-12 13:10

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0021_fill_recipient_language"),
    ]

    operations = [
        migrations.AlterField(
            model_name="newsletter",
            name="journal",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="newsletter",
                to="journal.Journal",
                verbose_name="Journal",
            ),
        ),
    ]
