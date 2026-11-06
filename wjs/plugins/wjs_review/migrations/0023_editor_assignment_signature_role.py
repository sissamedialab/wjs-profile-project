"""Fix the signature placeholder of the "EO manually assigns an Editor" body (specs#3187).

The stored text used ``{{ request.user.role.slug }}``, but ``Account`` has no ``role`` attribute and the placeholder
always rendered as an empty string. The form now provides ``sender_role_label``: rewrite only that placeholder in every
stored copy of the setting (default and journal overrides, all translated ``value_<lang>`` columns), leaving any other
customised text untouched.
"""

import re

from django.db import migrations

SETTING_NAME = "editor_assignment_manual_body"
OLD_PLACEHOLDER = "{{ request.user.role.slug }}"
NEW_PLACEHOLDER = "{{ sender_role_label }}"
OLD_PATTERN = re.compile(r"\{\{\s*request\.user\.role\.slug\s*\}\}")
NEW_PATTERN = re.compile(r"\{\{\s*sender_role_label\s*\}\}")


def replace_placeholder(text: str | None, pattern: re.Pattern, replacement: str) -> str | None:
    """Return ``text`` with every match of ``pattern`` replaced by ``replacement`` (``None``/empty unchanged)."""
    if not text:
        return text
    return pattern.sub(replacement, text)


def _rewrite(apps, pattern: re.Pattern, replacement: str) -> None:
    """Apply ``pattern`` -> ``replacement`` to every value column of every stored copy of the setting."""
    SettingValue = apps.get_model("core", "SettingValue")
    value_fields = [
        field.name
        for field in SettingValue._meta.concrete_fields
        if field.name == "value" or field.name.startswith("value_")
    ]
    for setting_value in SettingValue.objects.filter(setting__name=SETTING_NAME):
        changed = []
        for name in value_fields:
            old = getattr(setting_value, name)
            new = replace_placeholder(old, pattern, replacement)
            if new != old:
                setattr(setting_value, name, new)
                changed.append(name)
        if changed:
            setting_value.save(update_fields=changed)


def forwards(apps, schema_editor):
    """Replace the dead role placeholder with ``sender_role_label``."""
    _rewrite(apps, OLD_PATTERN, NEW_PLACEHOLDER)


def backwards(apps, schema_editor):
    """Restore the original placeholder."""
    _rewrite(apps, NEW_PATTERN, OLD_PLACEHOLDER)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0110_one_click_review_on_by_default"),
        ("wjs_review", "0022_articleworkflow_production_flag_iop_ready"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
