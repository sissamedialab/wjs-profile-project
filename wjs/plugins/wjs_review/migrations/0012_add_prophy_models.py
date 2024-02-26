# Generated by Django 3.2.19 on 2024-02-02 09:25

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0011_reminder"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProphyAccount",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("author_id", models.IntegerField(unique=True)),
                ("affiliation", models.CharField(blank=True, max_length=1000, null=True, verbose_name="Institution")),
                ("articles_count", models.IntegerField(blank=True, null=True)),
                ("authors_groups", models.CharField(blank=True, max_length=1000, null=True)),
                ("citations_count", models.IntegerField(blank=True, null=True)),
                ("email", models.EmailField(max_length=254, null=True, unique=True, verbose_name="Email")),
                ("h_index", models.IntegerField(blank=True, null=True)),
                ("name", models.CharField(max_length=900, null=True, verbose_name="Full name")),
                ("orcid", models.CharField(blank=True, max_length=40, null=True, verbose_name="ORCiD")),
                ("url", models.CharField(blank=True, max_length=300, null=True, verbose_name="Prophy author url")),
                (
                    "correspondence",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="jcom_profile.correspondence",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ProphyCandidate",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.FloatField(null=True, verbose_name="Prophy score")),
                ("prophy_manuscript_id", models.IntegerField(blank=True, null=True)),
                ("article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="submission.article")),
                (
                    "prophy_account",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="wjs_review.prophyaccount"),
                ),
            ],
        ),
    ]