"""Create BlacklistedAuthorEmail model."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wjs_review", "0016_attention_condition"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlacklistedAuthorEmail",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        help_text="The email address of the blacklisted author.", max_length=254, unique=True
                    ),
                ),
                (
                    "note",
                    models.TextField(
                        blank=True, default="", help_text="Optional note explaining why this author is blacklisted."
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Blacklisted author email",
                "verbose_name_plural": "Blacklisted author emails",
                "ordering": ["email"],
            },
        ),
    ]
