# Generated by Django 4.2.11 on 2024-09-22 14:31
import core.model_utils
import plugins.wjs_review.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wjs_review", "0037_send_review_file"),
    ]

    operations = [
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="abstract",
            field=core.model_utils.JanewayBleachField(blank=True, null=True, verbose_name="Abstract"),
        ),
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="title",
            field=core.model_utils.JanewayBleachCharField(blank=True, max_length=999, null=True, verbose_name="Title"),
        ),
        migrations.AlterField(
            model_name="editordecision",
            name="decision",
            field=models.CharField(
                choices=[
                    (None, "Select one decision"),
                    ("accept", "Accept"),
                    ("reject", "Reject"),
                    ("minor_revisions", "Minor revision"),
                    ("major_revisions", "Major revision"),
                    ("tech_revisions", "Change Metadata"),
                    ("not_suitable", "Not suitable"),
                    ("requires_resubmission", "Requires resubmission"),
                    ("open_appeal", "Open appeal"),
                ],
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="message",
            name="body",
            field=plugins.wjs_review.models.WjsBleachCharField(
                blank=True, default="", help_text="The content of the message.", max_length=1111, verbose_name="body"
            ),
        ),
        migrations.AddField(
            model_name="editorrevisionrequest",
            name="confirm_previous_version",
            field=models.BooleanField(default=False, verbose_name="Confirm version"),
        ),
        migrations.AlterField(
            model_name="pasteditorassignment",
            name="decline_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("JCOM_BUSY", "already too busy with JCOM editorial work"),
                    ("BUSY", "too busy in general"),
                    ("OUTSIDE_EXPERTISE", "paper outside my area of expertise"),
                    ("NO_REVIEWER", "unable to find an appropriate reviewer"),
                    ("OTHER", "other"),
                ],
                null=True,
                verbose_name="Decline reason",
            ),
        ),
    ]
