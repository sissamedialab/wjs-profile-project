"""Editor "Decline assignment" reasons: one generic "busy" reason for every journal (specs#3306)."""

import importlib

import pytest
from django.apps import apps as django_apps
from django.http import HttpRequest
from django.utils import translation
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from ..forms import EditorDeclinesAssignmentForm
from ..logic import HandleEditorDeclinesAssignment
from ..models import Message, PastEditorAssignment, WjsEditorAssignment

MIGRATION_MODULE = "plugins.wjs_review.migrations.0025_editor_decline_reason_busy_editorial"
NEW_LABEL = "Too busy with other editorial tasks"
DeclineReasons = PastEditorAssignment.DeclineReasons


def test_decline_reasons_offer_generic_busy_editorial():
    """The generic reason replaces the JCOM-specific one, in the same position."""
    assert dict(DeclineReasons.choices)["BUSY_EDITORIAL"] == NEW_LABEL
    assert "JCOM_BUSY" not in DeclineReasons.values
    assert not [label for label in DeclineReasons.labels if "JCOM" in str(label)]
    assert DeclineReasons.values[1] == "BUSY_EDITORIAL"


@pytest.mark.parametrize(
    "language,expected",
    [
        ("es", "Demasiado ocupado con otras tareas editoriales"),
        ("pt", "Muito ocupado com outras tarefas editoriais"),
    ],
)
def test_busy_editorial_label_is_translated(language: str, expected: str):
    """Spanish and Portuguese editors see the generic reason in their language."""
    with translation.override(language):
        assert str(DeclineReasons.BUSY_EDITORIAL.label) == expected


@pytest.mark.django_db
def test_decline_form_rejects_stale_jcom_busy(
    assigned_article: Article, section_editor: JCOMProfile, fake_request: HttpRequest
):
    """A modal opened before the deploy posting the old code is rejected, not stored."""
    fake_request.user = section_editor.janeway_account
    stale = EditorDeclinesAssignmentForm(
        data={"decline_reason": "JCOM_BUSY"}, request=fake_request, instance=assigned_article
    )
    assert not stale.is_valid()
    assert "decline_reason" in stale.errors
    current = EditorDeclinesAssignmentForm(
        data={"decline_reason": "BUSY_EDITORIAL"}, request=fake_request, instance=assigned_article
    )
    assert current.is_valid(), current.errors


@pytest.mark.django_db
def test_editor_decline_busy_editorial_notifies_director(
    assigned_article: Article, section_editor: JCOMProfile, director: JCOMProfile, fake_request: HttpRequest
):
    """Declining with the generic reason stores its code and shows its label to the Director."""
    fake_request.user = section_editor.janeway_account
    past = HandleEditorDeclinesAssignment(
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        editor=section_editor.janeway_account,
        request=fake_request,
        form_data={"decline_reason": DeclineReasons.BUSY_EDITORIAL, "decline_text": ""},
    ).run()
    assert past.decline_reason == "BUSY_EDITORIAL"
    message = Message.objects.filter(recipients=director.janeway_account).order_by("-created").first()
    assert message is not None, "The Director must receive the decline notification"
    assert NEW_LABEL in message.body
    assert "JCOM editorial work" not in message.body


def _past_assignments_with(article: Article, editor: JCOMProfile, reasons: list) -> list[int]:
    """Create one past assignment per reason, bypassing choices validation; return their pks."""
    pks = []
    for reason in reasons:
        past = PastEditorAssignment.objects.create(
            article=article,
            editor=editor.janeway_account,
            date_assigned=article.date_submitted,
            date_unassigned=article.date_submitted,
        )
        PastEditorAssignment.objects.filter(pk=past.pk).update(decline_reason=reason)
        pks.append(past.pk)
    return pks


def _reasons(pks: list[int]) -> list:
    by_pk = dict(PastEditorAssignment.objects.filter(pk__in=pks).values_list("pk", "decline_reason"))
    return [by_pk[pk] for pk in pks]


@pytest.mark.django_db
def test_migration_moves_jcom_busy_to_busy_editorial(assigned_article: Article, section_editor: JCOMProfile):
    """Only rows with the old code move; other reasons, NULLs and legacy free text are untouched."""
    reasons = ["JCOM_BUSY", "BUSY", "OTHER", None, "Too busy in general"]
    pks = _past_assignments_with(assigned_article, section_editor, reasons)
    importlib.import_module(MIGRATION_MODULE).forwards(django_apps, None)
    assert _reasons(pks) == ["BUSY_EDITORIAL", "BUSY", "OTHER", None, "Too busy in general"]


@pytest.mark.django_db
def test_migration_backwards_restores_jcom_busy(assigned_article: Article, section_editor: JCOMProfile):
    """The reverse operation restores the old code and leaves other rows alone."""
    pks = _past_assignments_with(assigned_article, section_editor, ["BUSY_EDITORIAL", "BUSY", None])
    importlib.import_module(MIGRATION_MODULE).backwards(django_apps, None)
    assert _reasons(pks) == ["JCOM_BUSY", "BUSY", None]
