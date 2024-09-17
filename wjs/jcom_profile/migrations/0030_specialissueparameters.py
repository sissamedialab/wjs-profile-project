# Generated by Django 4.2.11 on 2024-08-31 01:05

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0029_remove_specialissue_allowed_sections_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="IssueParameters",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "batch_publish",
                    models.BooleanField(default=True, verbose_name="Batch published"),
                ),
                (
                    "issue",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="journal.issue",
                        verbose_name="Issue",
                    ),
                ),
            ],
            options={
                "verbose_name": "Issue parameters",
                "verbose_name_plural": "Issue parameters",
            },
        ),
    ]