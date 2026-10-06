import pytest
from core.models import Setting, SettingValue
from django.core.management import call_command
from journal.models import Journal
from utils import setting_handler

OLD_BODY = "author_withdraws_preprint_body"
NEW_BODY = "author_withdraws_preprint_after_appeal_body"
OLD_SUBJECT = "author_withdraws_preprint_subject"
NEW_SUBJECT = "author_withdraws_preprint_after_appeal_subject"


def _override(journal: Journal, name: str) -> str | None:
    """Return the journal-level value of a wjs_review setting, or None if the journal has no override."""
    value = SettingValue.objects.filter(setting__name=name, setting__group__name="wjs_review", journal=journal).first()
    return value.value if value else None


@pytest.mark.django_db
def test_customized_body_is_copied(journal: Journal, review_settings):
    """A journal override of the old body that differs from the default is copied to the new setting."""
    setting_handler.save_setting("wjs_review", OLD_BODY, journal, "Dear Editor-in-charge, withdrawn.")
    call_command("unatantum_copy_withdraw_settings_after_appeal")
    assert _override(journal, NEW_BODY) == "Dear Editor-in-charge, withdrawn.", "Customized body must be copied"


@pytest.mark.django_db
def test_dry_run_copies_nothing(journal: Journal, review_settings):
    """--dry-run only reports: no override is created."""
    setting_handler.save_setting("wjs_review", OLD_BODY, journal, "custom")
    call_command("unatantum_copy_withdraw_settings_after_appeal", "--dry-run")
    assert _override(journal, NEW_BODY) is None, "Dry run must not create the override"


@pytest.mark.django_db
def test_override_equal_to_default_is_not_copied(journal: Journal, review_settings):
    """An override identical to the default up to whitespace is not a customization and is not copied."""
    default = SettingValue.objects.get(setting__name=OLD_BODY, setting__group__name="wjs_review", journal=None).value
    setting_handler.save_setting("wjs_review", OLD_BODY, journal, default.rstrip("\n"))
    call_command("unatantum_copy_withdraw_settings_after_appeal")
    assert _override(journal, NEW_BODY) is None, "A whitespace-only difference must not be copied"


@pytest.mark.django_db
def test_existing_new_override_is_kept(journal: Journal, review_settings):
    """A journal that already customized the new setting keeps its own value."""
    setting_handler.save_setting("wjs_review", OLD_BODY, journal, "old customization")
    setting_handler.save_setting("wjs_review", NEW_BODY, journal, "new customization")
    call_command("unatantum_copy_withdraw_settings_after_appeal")
    assert _override(journal, NEW_BODY) == "new customization", "Existing override of the new setting must be kept"


@pytest.mark.django_db
def test_subject_is_copied_and_command_is_idempotent(journal: Journal, review_settings):
    """The subject is handled like the body, and running the command twice changes nothing."""
    setting_handler.save_setting("wjs_review", OLD_SUBJECT, journal, "Custom subject")
    call_command("unatantum_copy_withdraw_settings_after_appeal")
    call_command("unatantum_copy_withdraw_settings_after_appeal")
    assert _override(journal, NEW_SUBJECT) == "Custom subject", "Customized subject must be copied"
    count = SettingValue.objects.filter(setting__name=NEW_SUBJECT, journal=journal).count()
    assert count == 1, "Running twice must not duplicate the override"


@pytest.mark.django_db
def test_skipped_when_new_settings_are_not_installed(journal: Journal, review_settings, capsys):
    """If the new settings do not exist yet, the command reports it and does not create them."""
    setting_handler.save_setting("wjs_review", OLD_BODY, journal, "custom")
    Setting.objects.filter(name=NEW_BODY, group__name="wjs_review").delete()
    call_command("unatantum_copy_withdraw_settings_after_appeal")
    assert not Setting.objects.filter(name=NEW_BODY).exists(), "The command must not create the setting itself"
    assert NEW_BODY in capsys.readouterr().err, "The command must report the missing setting"
