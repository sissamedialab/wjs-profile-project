"""Drop the Genealogy model, superseded by the hydra plugin.

The data has been moved by 0042_genealogy_to_hydra.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("jcom_profile", "0042_genealogy_to_hydra"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="genealogy",
            name="children",
        ),
        migrations.RemoveField(
            model_name="genealogy",
            name="parent",
        ),
        migrations.DeleteModel(
            name="Genealogy",
        ),
    ]
