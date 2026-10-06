from core.models import Setting, SettingValue
from django.core.management.base import BaseCommand

GROUP = "wjs_review"

# (existing setting, setting that replaces it for withdrawals after a rejection) - specs#2903
SETTING_PAIRS = (
    ("author_withdraws_preprint_subject", "author_withdraws_preprint_after_appeal_subject"),
    ("author_withdraws_preprint_body", "author_withdraws_preprint_after_appeal_body"),
)


def _value_fields() -> list[str]:
    """Return the names of the (translatable) value columns of SettingValue: ``value`` and ``value_<lang>``."""
    return [
        field.name
        for field in SettingValue._meta.concrete_fields
        if field.name == "value" or field.name.startswith("value_")
    ]


def _normalize(value: str | None) -> str:
    return (value or "").strip()


class Command(BaseCommand):
    help = (  # noqa: A003
        "Run once after deploying specs#2903: copy the journals' customizations of author_withdraws_preprint_* "
        "to the new author_withdraws_preprint_after_appeal_* settings. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be copied.")

    def handle(self, *args, **options):
        """
        Carry the journals' customizations of the withdrawal message over to the "after appeal" settings.

        Withdrawals after a rejection used to prefill the notification from ``author_withdraws_preprint_*``; they
        now use ``author_withdraws_preprint_after_appeal_*``, which only ship with a (copied) default. A journal
        that overrides the old setting with something different from the default gets the same value for the new
        setting, unless it already has an override of its own.

        This is a command and not a data migration because the new settings are installed by the plugin settings
        step (``install_plugins``), which runs after the migrations: they must already be installed when this runs.
        """
        dry_run = options["dry_run"]
        value_fields = _value_fields()
        for old_name, new_name in SETTING_PAIRS:
            old_setting = Setting.objects.filter(name=old_name, group__name=GROUP).first()
            new_setting = Setting.objects.filter(name=new_name, group__name=GROUP).first()
            if old_setting is None:
                continue
            if new_setting is None:
                self.stderr.write(f"Setting {new_name!r} is not installed: customizations of {old_name!r} not copied.")
                continue
            default = SettingValue.objects.filter(setting=old_setting, journal=None).first()
            default_values = {name: _normalize(getattr(default, name, None)) for name in value_fields}
            for override in SettingValue.objects.filter(setting=old_setting).exclude(journal=None):
                if all(_normalize(getattr(override, name)) == default_values[name] for name in value_fields):
                    continue  # not a real customization (same as the default, up to whitespace)
                if SettingValue.objects.filter(setting=new_setting, journal=override.journal).exists():
                    self.stdout.write(f"{override.journal.code}: {new_name!r} already customized, kept.")
                    continue
                prefix = "Would copy" if dry_run else "Copied"
                self.stdout.write(f"{prefix} {old_name!r} to {new_name!r} for {override.journal.code}.")
                if not dry_run:
                    SettingValue.objects.create(
                        setting=new_setting,
                        journal=override.journal,
                        **{name: getattr(override, name) for name in value_fields},
                    )
