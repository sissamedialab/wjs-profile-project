"""Copy button on the "Show author notes" modal (wjs/specs#3128)."""

import re
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from wjs.jcom_profile.models import JCOMProfile

GALLEYPROOFING_TEMPLATE = "wjs_review/details/elements/production_galleyproofing.html"
MODAL_TEMPLATE = "wjs_review/base/elements/modal.html"
COPY_SOURCE_ATTR = 'data-copy-source="template[data-author-notes-source]"'

NOTES_HTML = (
    "<p>Fix <strong>this</strong> &amp; that: $\\alpha_s$ &lt;tag&gt; è</p>\n<ul>\n<li>one</li>\n<li>two</li>\n</ul>"
)


class _NoFiles:
    """Stand-in for an empty related manager: the template only calls `exists()` (and `all()` behind it)."""

    def exists(self):
        return False


def _version(number: int, notes: str) -> SimpleNamespace:
    """Build the minimal version object `production_galleyproofing.html` reads (a real, if lightweight, duck type)."""
    galleyproofing = SimpleNamespace(notes=notes, proofed_files=_NoFiles(), annotated_files=_NoFiles())
    return SimpleNamespace(number=number, galleyproofing=galleyproofing)


def _render(version) -> str:
    return render_to_string(GALLEYPROOFING_TEMPLATE, {"version": version, "heading_level": 3})


def _modal_body(html: str, modal_id: str) -> str:
    match = re.search(rf'id="{modal_id}".*?<div class="modal-body">(.*?)</div>\s*</div>\s*</div>\s*</div>', html, re.S)
    assert match, f"modal {modal_id} not found"
    return match.group(1)


def test_copy_button_rendered_in_modal_body_before_notes():
    html = _render(_version(1, "Some notes"))
    body = _modal_body(html, "author_notes-1")
    assert COPY_SOURCE_ATTR in body, "Copy button missing from the notes modal body"
    assert body.index("data-copy-source") < body.index("Some notes"), "Copy button must be above the notes"


def test_notes_modal_uses_xl_size():
    html = _render(_version(1, "Some notes"))
    assert re.search(r'<div class="modal-dialog modal-xl">', html), "author notes modal must be modal-xl"


def test_source_template_holds_raw_notes():
    html = _render(_version(1, NOTES_HTML))
    match = re.search(r"<template data-author-notes-source>(.*?)</template>", html, re.S)
    assert match, "source <template> missing"
    assert match.group(1) == NOTES_HTML, "stored notes must be copied verbatim (no linebreaksbr, no re-escaping)"
    assert "<br" not in match.group(1), "a <br> inside the list would split it in rich editors"


def test_no_button_and_no_template_without_notes():
    html = _render(_version(1, ""))
    assert "data-copy-source" not in html
    assert "data-author-notes-source" not in html


def _copy_roots(html: str) -> list[str]:
    """Return the markup of each `[data-copy-root]` element (the wrapper has no nested div before the template)."""
    return re.findall(r"<div class=\"d-flex gap-2\" data-copy-root>(.*?</template>)", html, re.S)


def test_source_is_found_inside_the_button_own_copy_root_per_version():
    html = _render(_version(1, "Notes one")) + _render(_version(2, "Notes two"))
    roots = _copy_roots(html)
    assert len(roots) == 2, "one copy root per rendered version"
    for root, text in zip(roots, ("Notes one", "Notes two")):
        assert root.count(COPY_SOURCE_ATTR) == 1, "exactly one Copy button per copy root"
        assert f"<template data-author-notes-source>{text}</template>" in root, "button and source must be in one root"


def test_same_version_rendered_twice_has_no_duplicate_ids_and_each_root_is_self_contained():
    """The pending-proofs version is rendered in the accordion and in the Production card of the same page."""
    version = _version(1, "Notes one")
    html = _render(version) + _render(version)
    assert "author_notes_source" not in html, "the source template must not carry an id that renders twice"
    roots = _copy_roots(html)
    assert len(roots) == 2, "both renders must get their own copy root"
    for root in roots:
        assert root.count(COPY_SOURCE_ATTR) == 1, "each render has its own Copy button"
        assert (
            root.count("<template data-author-notes-source>Notes one</template>") == 1
        ), "each render has its own source template inside the same root as its button"


def test_shared_modal_has_no_copy_button_by_default():
    html = render_to_string(MODAL_TEMPLATE, {"modal_id": "other-1", "modal_title": "T", "modal_body": "body"})
    assert "data-copy-source" not in html


@pytest.mark.django_db
def test_typesetter_and_author_see_copy_button_on_status_page(
    assigned_to_typesetter_proofs_done_article,
    typesetter: JCOMProfile,
    client: Client,
):
    article = assigned_to_typesetter_proofs_done_article
    url = reverse("wjs_article_details", kwargs={"pk": article.articleworkflow.pk})
    for user in (typesetter, article.correspondence_author):
        client.force_login(user)
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert COPY_SOURCE_ATTR in content, f"no Copy button for {user}"
        assert "Dear typ, please correct this and that" in content


@pytest.mark.django_db
def test_status_page_loads_clipboard_script_once(
    assigned_to_typesetter_proofs_done_article,
    typesetter: JCOMProfile,
    client: Client,
):
    article = assigned_to_typesetter_proofs_done_article
    client.force_login(typesetter)
    response = client.get(reverse("wjs_article_details", kwargs={"pk": article.articleworkflow.pk}))
    assert response.content.decode().count("js/clipboard_copy.js") == 1
