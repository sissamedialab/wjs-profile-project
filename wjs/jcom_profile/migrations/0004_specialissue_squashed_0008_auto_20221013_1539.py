"""New models SpecialIssue and ArticleWrapper."""
# Generated by Django 1.11.29 on 2022-10-13 14:40
from __future__ import unicode_literals

import django.db.models.deletion
import submission.models
from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [
        ("jcom_profile", "0004_specialissue"),
        ("jcom_profile", "0005_articlewrapper"),
        ("jcom_profile", "0006_auto_20221012_1750"),
        ("jcom_profile", "0007_auto_20221013_1500"),
        ("jcom_profile", "0008_auto_20221013_1539"),
    ]

    dependencies = [
        ("submission", "0069_delete_blank_keywords"),
        ("jcom_profile", "0005_auto_20221005_1620"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpecialIssue",
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
                ("name", models.CharField(max_length=121)),
                ("is_open_for_submission", models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name="ArticleWrapper",
            fields=[
                (
                    "janeway_article",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to="submission.Article",
                    ),
                ),
                (
                    "special_issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="special_issue",
                        to="jcom_profile.SpecialIssue",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
            bases=("submission.article",),
        ),
        migrations.AlterModelManagers(
            name="articlewrapper",
            managers=[
                ("objects", submission.models.ArticleManager()),
            ],
        ),
        migrations.AlterField(
            model_name="articlewrapper",
            name="special_issue",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="special_issue",
                to="jcom_profile.SpecialIssue",
            ),
        ),
        migrations.AlterModelManagers(
            name="articlewrapper",
            managers=[],
        ),
        migrations.AlterField(
            model_name="articlewrapper",
            name="janeway_article",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                parent_link=True,
                primary_key=True,
                serialize=False,
                to="submission.Article",
            ),
        ),
    ]
