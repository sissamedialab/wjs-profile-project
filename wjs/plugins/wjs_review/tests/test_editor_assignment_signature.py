"""The signature of the "EO manually assigns an Editor" body shows the sender's role (specs#3187)."""

import importlib

import pytest
from core.models import Account, Setting, SettingValue
from django.apps import apps as django_apps
from django.http import HttpRequest
from journal.models import Journal
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from ..forms import SupervisorAssignEditorForm
from ..models import ArticleWorkflow


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


@pytest.mark.django_db
def test_signature_shows_eo_label(review_settings, article: Article, fake_request: HttpRequest, eo_user: JCOMProfile):
    body = _initial_body(article, eo_user.janeway_account, fake_request)
    assert f"{article.journal.code} EO" in body
    assert "role.slug" not in body


@pytest.mark.django_db
def test_signature_shows_director_label(
    review_settings, article: Article, fake_request: HttpRequest, director: JCOMProfile
):
    body = _initial_body(article, director.janeway_account, fake_request)
    assert f"{article.journal.code} Director" in body


@pytest.mark.django_db
def test_signature_eo_wins_over_director(
    review_settings, article: Article, fake_request: HttpRequest, eo_user: JCOMProfile
):
    eo_user.add_account_role("director", article.journal)
    body = _initial_body(article, eo_user.janeway_account, fake_request)
    assert f"{article.journal.code} EO" in body
    assert f"{article.journal.code} Director" not in body


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
