# Generated by Django 3.2.19 on 2024-01-01 16:57

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("wjs_review", "0007_article_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="hijacking_actor",
            field=models.ForeignKey(
                blank=True,
                help_text="The real author of the message (if actor has been hijacked)",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="authored_messages_as_hijacker",
                to=settings.AUTH_USER_MODEL,
                verbose_name="hijacker",
            ),
        ),
    ]