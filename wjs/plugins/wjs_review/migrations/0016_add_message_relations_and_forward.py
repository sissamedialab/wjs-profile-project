# Generated by Django 3.2.19 on 2024-04-23 07:54

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("wjs_review", "0015_alter_reminder_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="to_be_forwarded_to",
            field=models.ForeignKey(
                blank=True,
                help_text="The final recipient that this message was intended for",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="pre_moderation_messages",
                to=settings.AUTH_USER_MODEL,
                verbose_name="final recipient",
            ),
        ),
        migrations.AlterField(
            model_name="articleworkflow",
            name="eo_in_charge",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"groups__name": "EO"},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
                verbose_name="EO in charge",
            ),
        ),
        migrations.CreateModel(
            name="MessageThread",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "relation_type",
                    models.CharField(
                        choices=[
                            ("Forward", "The child message is a forward of the parent message."),
                            ("Reply", "The child message is a reply to the parent message."),
                        ],
                        max_length=101,
                    ),
                ),
                (
                    "child_message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="parents", to="wjs_review.message"
                    ),
                ),
                (
                    "parent_message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="children", to="wjs_review.message"
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="message",
            name="related_messages",
            field=models.ManyToManyField(
                related_name="children_messages", through="wjs_review.MessageThread", to="wjs_review.Message"
            ),
        ),
    ]
