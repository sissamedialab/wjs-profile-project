"""Keep "Editor-in-chief" / "Deputy Editor" director signatures in JCOM and JCOMAL (specs#3187).

The ``directors_sign_as_director`` setting defaults to true (every director signs as "Director"). JCOM and JCOMAL keep
the previous wording, so this migration stores a false override for those journals only.

The setting and its default value ("on") are also created here when missing: otherwise, until
``plugin_settings.install()`` runs, reading the setting would raise ``SettingValue.DoesNotExist`` in every journal
without an override. ``install()`` later rewrites the default value only, never these overrides.
"""

from django.db import migrations

SETTING_NAME = "directors_sign_as_director"
SETTING_GROUP = "wjs_review"
JOURNAL_CODES = ("JCOM", "JCOMAL")
TRUE_VALUE = "on"  # Janeway reads a boolean setting as true only when its value is "on"
FALSE_VALUE = ""


def _value_fields(SettingValue) -> list[str]:
    """Names of the ``value`` column and its translated ``value_<lang>`` columns."""
    return [
        field.name
        for field in SettingValue._meta.concrete_fields
        if field.name == "value" or field.name.startswith("value_")
    ]


def forwards(apps, schema_editor):
    """Ensure the setting and its true default exist, then store a false override for JCOM and JCOMAL."""
    Journal = apps.get_model("journal", "Journal")
    SettingGroup = apps.get_model("core", "SettingGroup")
    Setting = apps.get_model("core", "Setting")
    SettingValue = apps.get_model("core", "SettingValue")

    group, __ = SettingGroup.objects.get_or_create(name=SETTING_GROUP, defaults={"enabled": True})
    setting, __ = Setting.objects.get_or_create(
        name=SETTING_NAME,
        group=group,
        defaults={
            "types": "boolean",
            "pretty_name": "Directors sign as Director",
            "description": (
                "If true, all directors sign their messages as 'Director'; otherwise the main director signs as "
                "'Editor-in-chief' and the other directors as 'Deputy Editor'."
            ),
            "is_translatable": False,
        },
    )
    value_fields = _value_fields(SettingValue)
    SettingValue.objects.get_or_create(
        setting=setting, journal=None, defaults={name: TRUE_VALUE for name in value_fields}
    )
    false_values = {name: FALSE_VALUE for name in value_fields}
    for journal in Journal.objects.filter(code__in=JOURNAL_CODES):
        SettingValue.objects.update_or_create(setting=setting, journal=journal, defaults=false_values)


def backwards(apps, schema_editor):
    """Remove the JCOM and JCOMAL overrides (the journals fall back to the default value).

    The setting and its default value are left in place: ``install()`` manages them.
    """
    SettingValue = apps.get_model("core", "SettingValue")
    SettingValue.objects.filter(setting__name=SETTING_NAME, journal__code__in=JOURNAL_CODES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0110_one_click_review_on_by_default"),
        ("wjs_review", "0023_editor_assignment_signature_role"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
