# Generated by Django 1.11.29 on 2022-12-16 21:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0011_auto_20221216_2128"),
    ]

    operations = [
        migrations.AlterField(
            model_name="specialissue",
            name="documents",
            field=models.ManyToManyField(blank=True, null=True, to="core.File"),
        ),
    ]
