# Generated by Django 3.2.19 on 2023-09-14 08:05

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jcom_profile", "0025_alter_correspondence_unique_together"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipient",
            name="confirmation_email_last_sent",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="When the subscription/reminder confirmation email has been sent to an anonymous recipient",
            ),
        ),
    ]
