# Generated by Django 3.2.19 on 2023-12-06 21:58

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0007_add_eo_in_charge"),
    ]

    operations = [
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="article_history",
            field=models.JSONField(blank=True, null=True, verbose_name="Article history"),
        ),
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="data_figure_files",
            field=models.ManyToManyField(
                blank=True,
                null=True,
                related_name="_wjs_review_editorrevisionrequest_data_figure_files_+",
                to="core.File",
            ),
        ),
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="manuscript_files",
            field=models.ManyToManyField(
                blank=True,
                null=True,
                related_name="_wjs_review_editorrevisionrequest_manuscript_files_+",
                to="core.File",
            ),
        ),
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="source_files",
            field=models.ManyToManyField(
                blank=True, related_name="_wjs_review_editorrevisionrequest_source_files_+", to="core.File"
            ),
        ),
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="supplementary_files",
            field=models.ManyToManyField(
                blank=True,
                null=True,
                related_name="_wjs_review_editorrevisionrequest_supplementary_files_+",
                to="core.SupplementaryFile",
            ),
        ),
        migrations.AlterField(
            model_name="editorrevisionrequest",
            name="review_round",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT, to="review.reviewround", verbose_name="Review round"
            ),
        ),
    ]
