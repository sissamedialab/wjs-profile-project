# Generated by Django 1.11.29 on 2023-04-12 12:46

from django.db import migrations

from utils.setting_handler import get_setting


def fill_recipient_language(apps, schema_editor):
    Recipient = apps.get_model("jcom_profile", "Recipient")
    for recipient in Recipient.objects.all():
        language = get_setting(
            "general",
            "default_journal_language",
            recipient.journal.pk,
        )
        recipient.language = language.processed_value
        recipient.save()


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0020_fill_newsletter_journal_field_with_first_journal"),
    ]

    operations = [
        migrations.RunPython(fill_recipient_language, migrations.RunPython.noop),
    ]
