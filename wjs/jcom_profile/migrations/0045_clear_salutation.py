from django.db import migrations
from django.db.models import F


def clear_salutations(apps, schema_editor):
    """
    Clear every salutation and the author-name prefixes that were copied from it (specs#3271).

    Janeway's ``Account.snapshot_as_author`` copies the account's salutation into ``FrozenAuthor.name_prefix``
    when the account has no prefix, and ``FrozenAuthor.full_name`` then renders it.
    The frozen authors must be cleared first, while the salutations are still there to compare with:
    only a prefix equal to the author's salutation is cleared, any other prefix is left alone.
    """
    FrozenAuthor = apps.get_model("submission", "FrozenAuthor")
    Account = apps.get_model("core", "Account")

    FrozenAuthor.objects.exclude(name_prefix="").filter(author__salutation=F("name_prefix")).update(name_prefix="")
    Account.objects.exclude(salutation="").update(salutation="")


class Migration(migrations.Migration):

    dependencies = [
        ("jcom_profile", "0044_alter_staffworkloadparameters_brake_on"),
        ("core", "0110_one_click_review_on_by_default"),
        ("submission", "0089_keyword_created_keyword_deactivated_keyword_notes_and_more"),
    ]

    operations = [
        # The cleared values cannot be restored, so there is nothing to do on reverse.
        migrations.RunPython(clear_salutations, migrations.RunPython.noop),
    ]
