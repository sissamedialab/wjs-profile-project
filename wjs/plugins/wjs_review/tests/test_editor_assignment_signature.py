"""The signature of the "EO manually assigns an Editor" body shows the sender's role (specs#3187)."""

import importlib

import pytest
from core.models import Account, Setting, SettingValue
from django.apps import apps as django_apps
from django.http import HttpRequest
from journal.models import Journal
from submission.models import Article
from utils.setting_handler import get_setting, save_setting

from wjs.jcom_profile.models import JCOMProfile

from ..forms import SupervisorAssignEditorForm
from ..models import ArticleWorkflow
from ..plugin_settings import set_default_plugin_settings


def _initial_body(article: Article, user: Account, fake_request: HttpRequest) -> str:
    """Return the pre-filled body for the new editor, as rendered for ``user``."""
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()
    fake_request.user = user
    form = SupervisorAssignEditorForm(
        user=user,
        request=fake_request,
        instance=article.articleworkflow,
        selectable_editors=Account.objects.get_editors_with_keywords(article),
    )
    return form.fields["note_for_new_editor"].initial


def _signature(body: str) -> str:
    """Return the last line of the body: the "<journal code> <role>" signature."""
    return body.rstrip().splitlines()[-1]


@pytest.mark.django_db
def test_signature_shows_eo_label(review_settings, article: Article, fake_request: HttpRequest, eo_user: JCOMProfile):
    body = _initial_body(article, eo_user.janeway_account, fake_request)
    assert _signature(body) == f"{article.journal.code} Editorial Office"
    assert "role.slug" not in body


@pytest.mark.django_db
def test_signature_shows_director_label(
    review_settings, article: Article, fake_request: HttpRequest, director: JCOMProfile, main_director: JCOMProfile
):
    for user in (director, main_director):
        body = _initial_body(article, user.janeway_account, fake_request)
        assert _signature(body) == f"{article.journal.code} Director"


@pytest.mark.django_db
def test_signature_main_director_without_setting(
    review_settings, article: Article, fake_request: HttpRequest, director: JCOMProfile, main_director: JCOMProfile
):
    save_setting(
        setting_group_name="wjs_review",
        setting_name="directors_sign_as_director",
        journal=article.journal,
        value=False,
    )
    body = _initial_body(article, main_director.janeway_account, fake_request)
    assert _signature(body) == f"{article.journal.code} Editor-in-chief"
    body = _initial_body(article, director.janeway_account, fake_request)
    assert _signature(body) == f"{article.journal.code} Deputy Editor"


@pytest.mark.django_db
def test_signature_eo_wins_over_director(
    review_settings, article: Article, fake_request: HttpRequest, eo_user: JCOMProfile
):
    eo_user.add_account_role("director", article.journal)
    body = _initial_body(article, eo_user.janeway_account, fake_request)
    assert _signature(body) == f"{article.journal.code} Editorial Office"


MIGRATION_MODULE = "plugins.wjs_review.migrations.0023_editor_assignment_signature_role"
OLD = "{{ request.user.role.slug }}"
NEW = "{{ sender_role_label }}"


def _value_fields() -> list[str]:
    """Names of the ``value`` column and its translated ``value_<lang>`` columns."""
    return [f.name for f in SettingValue._meta.concrete_fields if f.name == "value" or f.name.startswith("value_")]


def _body_setting_values(journal: Journal) -> tuple[SettingValue, SettingValue]:
    """Return (default, journal override) of editor_assignment_manual_body, with the old placeholder."""
    setting = Setting.objects.get(name="editor_assignment_manual_body")
    default = SettingValue.objects.get(setting=setting, journal=None)
    default.value = f"Regards,<br>{{{{ article.journal.code }}}} {OLD}\n"
    default.save()
    override, __ = SettingValue.objects.get_or_create(setting=setting, journal=journal)
    override.value = "<p>Custom text&nbsp;kept</p>{{ article.journal.code }} {{request.user.role.slug}}\xa0</p>"
    override.save()
    return default, override


@pytest.mark.django_db
def test_migration_rewrites_default_and_overrides(review_settings, journal: Journal):
    default, override = _body_setting_values(journal)
    other_fields = [name for name in _value_fields() if name != "value" and getattr(override, name) is None]
    importlib.import_module(MIGRATION_MODULE).forwards(django_apps, None)
    default.refresh_from_db()
    override.refresh_from_db()
    assert default.value == f"Regards,<br>{{{{ article.journal.code }}}} {NEW}\n"
    assert override.value == f"<p>Custom text&nbsp;kept</p>{{{{ article.journal.code }}}} {NEW}\xa0</p>"
    for name in other_fields:
        assert getattr(override, name) is None


@pytest.mark.django_db
def test_migration_rewrites_translated_columns(review_settings, journal: Journal):
    __, override = _body_setting_values(journal)
    translated = [name for name in _value_fields() if name != "value"]
    assert translated, "SettingValue is expected to have translated value_<lang> columns"
    for name in translated:
        setattr(override, name, f"x {OLD}")
    override.save()
    importlib.import_module(MIGRATION_MODULE).forwards(django_apps, None)
    override.refresh_from_db()
    for name in translated:
        assert getattr(override, name) == f"x {NEW}"


@pytest.mark.django_db
def test_migration_leaves_values_without_placeholder_untouched(review_settings, journal: Journal):
    __, override = _body_setting_values(journal)
    override.value = "<p>Fully custom signature</p>"
    override.save()
    importlib.import_module(MIGRATION_MODULE).forwards(django_apps, None)
    override.refresh_from_db()
    assert override.value == "<p>Fully custom signature</p>"


@pytest.mark.django_db
def test_migration_is_idempotent(review_settings, journal: Journal):
    default, override = _body_setting_values(journal)
    migration = importlib.import_module(MIGRATION_MODULE)
    migration.forwards(django_apps, None)
    default.refresh_from_db()
    override.refresh_from_db()
    after_first = (default.value, override.value)
    migration.forwards(django_apps, None)
    default.refresh_from_db()
    override.refresh_from_db()
    assert (default.value, override.value) == after_first


@pytest.mark.django_db
def test_migration_backwards_restores_old_placeholder(review_settings, journal: Journal):
    default, __ = _body_setting_values(journal)
    migration = importlib.import_module(MIGRATION_MODULE)
    migration.forwards(django_apps, None)
    migration.backwards(django_apps, None)
    default.refresh_from_db()
    assert default.value == f"Regards,<br>{{{{ article.journal.code }}}} {OLD}\n"


DIRECTORS_MIGRATION = "plugins.wjs_review.migrations.0024_directors_sign_as_director"
DIRECTORS_SETTING = "directors_sign_as_director"


def _signs_as_director(journal: Journal) -> bool:
    return get_setting("wjs_review", DIRECTORS_SETTING, journal).processed_value


def _override_journals() -> set[str]:
    """Codes of the journals with an override of ``directors_sign_as_director``."""
    return set(
        SettingValue.objects.filter(setting__name=DIRECTORS_SETTING, journal__isnull=False).values_list(
            "journal__code", flat=True
        )
    )


@pytest.mark.django_db
def test_0024_creates_false_overrides(review_settings, journal: Journal, journal_factory):
    jcomal = journal_factory("JCOMAL")
    jcap = journal_factory("JCAP")
    importlib.import_module(DIRECTORS_MIGRATION).forwards(django_apps, None)
    assert _override_journals() == {"JCOM", "JCOMAL"}
    assert _signs_as_director(journal) is False
    assert _signs_as_director(jcomal) is False
    assert _signs_as_director(jcap) is True


@pytest.mark.django_db
def test_0024_is_idempotent(review_settings, journal: Journal):
    migration = importlib.import_module(DIRECTORS_MIGRATION)
    migration.forwards(django_apps, None)
    migration.forwards(django_apps, None)
    overrides = SettingValue.objects.filter(setting__name=DIRECTORS_SETTING, journal=journal)
    assert overrides.count() == 1
    assert overrides.get().value == ""


@pytest.mark.django_db
def test_0024_backwards_removes_overrides(review_settings, journal: Journal):
    migration = importlib.import_module(DIRECTORS_MIGRATION)
    migration.forwards(django_apps, None)
    migration.backwards(django_apps, None)
    assert _override_journals() == set()
    assert SettingValue.objects.get(setting__name=DIRECTORS_SETTING, journal=None).value == "on"
    assert _signs_as_director(journal) is True


@pytest.mark.django_db
def test_0024_without_target_journals_adds_only_default(review_settings, journal: Journal):
    journal.code = "JCAP"
    journal.save()
    Setting.objects.filter(name=DIRECTORS_SETTING).delete()
    importlib.import_module(DIRECTORS_MIGRATION).forwards(django_apps, None)
    assert _override_journals() == set()
    assert SettingValue.objects.get(setting__name=DIRECTORS_SETTING, journal=None).value == "on"
    assert _signs_as_director(journal) is True


@pytest.mark.django_db
def test_0024_before_install(review_settings, journal: Journal, journal_factory):
    """Without install(), the migration alone makes the setting readable for every journal."""
    jcap = journal_factory("JCAP")
    Setting.objects.filter(name=DIRECTORS_SETTING).delete()
    importlib.import_module(DIRECTORS_MIGRATION).forwards(django_apps, None)
    assert _signs_as_director(journal) is False
    assert _signs_as_director(jcap) is True
    set_default_plugin_settings(force=True)
    assert _signs_as_director(journal) is False
    assert _signs_as_director(jcap) is True
