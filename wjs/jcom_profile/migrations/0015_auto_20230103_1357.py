# Generated by Django 1.11.29 on 2023-01-03 12:57

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jcom_profile", "0014_recipient"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipient",
            name="email",
            field=models.EmailField(
                blank=True, max_length=254, null=True, unique=True, verbose_name="Anonymous user email"
            ),
        ),
        migrations.AddField(
            model_name="recipient",
            name="newsletter_token",
            field=models.CharField(blank=True, max_length=500, verbose_name="newsletter token for anonymous users"),
        ),
        migrations.AlterField(
            model_name="recipient",
            name="user",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.AUTH_USER_MODEL,
                verbose_name="Newsletter topics user",
            ),
        ),
    ]
