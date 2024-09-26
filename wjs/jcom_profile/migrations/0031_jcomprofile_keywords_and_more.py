# Generated by Django 4.2.11 on 2024-09-17 22:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0030_specialissueparameters"),
    ]

    operations = [
        migrations.AddField(
            model_name="jcomprofile",
            name="keywords",
            field=models.ManyToManyField(blank=True, to="submission.keyword", verbose_name="Interests"),
        ),
        migrations.AlterField(
            model_name="jcomprofile",
            name="invitation_token",
            field=models.CharField(blank=True, default="", max_length=500, verbose_name="Invitation token"),
        ),
    ]