# Generated by Django 3.2.19 on 2024-06-19 10:24

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0023_latexpreamble_wjssection"),
    ]

    operations = [
        migrations.AddField(
            model_name="articleworkflow",
            name="latex_desc",
            field=models.TextField(blank=True, null=True),
        ),
    ]
