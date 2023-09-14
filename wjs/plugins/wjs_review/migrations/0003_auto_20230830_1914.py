# Generated by Django 3.2.19 on 2023-08-30 17:14

import django.db.models.deletion
import django.utils.timezone
import django_fsm
import model_utils.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0002_alter_articleworkflow_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="articleworkflow",
            name="state",
            field=django_fsm.FSMField(
                choices=[
                    ("ED_TO_BE_SE", "Editor to be selected"),
                    ("EDITO_SELEC", "Editor selected"),
                    ("_SUBMITTED_", "Submitted"),
                    ("_TO_BE_REV_", "To be revised"),
                    ("_WITHDRAWN_", "Withdrawn"),
                    ("_REJECTED__", "Rejected"),
                    ("INCOM_SUBMI", "Incomplete submission"),
                    ("_NOT_SUITA_", "Not suitable"),
                    ("PA_HA_ED_RE", "Paper has editor report"),
                    ("_ACCEPTED__", "Accepted"),
                    ("WRITE_PRODU", "Writeme production"),
                    ("PA_MI_HA_IS", "Paper might have issues"),
                ],
                default="INCOM_SUBMI",
                max_length=50,
                verbose_name="State",
            ),
        ),
        migrations.CreateModel(
            name="EditorDecision",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "created",
                    model_utils.fields.AutoCreatedField(
                        default=django.utils.timezone.now, editable=False, verbose_name="created"
                    ),
                ),
                (
                    "modified",
                    model_utils.fields.AutoLastModifiedField(
                        default=django.utils.timezone.now, editable=False, verbose_name="modified"
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[("accept", "Accept"), ("reject", "Reject"), ("not_suitable", "Not suitable")],
                        max_length=255,
                    ),
                ),
                ("decision_editor_report", models.TextField(blank=True, null=True)),
                ("decision_internal_note", models.TextField(blank=True, null=True)),
                (
                    "review_round",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="review.reviewround",
                        verbose_name="Review round",
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decisions",
                        to="wjs_review.articleworkflow",
                        verbose_name="Article workflow",
                    ),
                ),
            ],
            options={
                "verbose_name": "Editor decision",
                "verbose_name_plural": "Editor decisions",
                "unique_together": {("workflow", "review_round")},
            },
        ),
    ]