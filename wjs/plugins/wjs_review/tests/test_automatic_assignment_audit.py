"""Tests for the automatic-assignment audit trail."""

import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from submission.models import Article

from ..models import (
    AutomaticAssignmentAudit,
    AutomaticAssignmentCandidate,
    WjsEditorAssignment,
)

Account = get_user_model()


def _make_audit(article, editor, **kwargs):
    defaults = {
        "algorithm": AutomaticAssignmentAudit.Algorithms.DEFAULT,
        "matched_by_keyword": False,
        "workload_available": 3,
        "workload_cap": 5,
    }
    defaults.update(kwargs)
    return AutomaticAssignmentAudit.objects.create(article=article, editor=editor, **defaults)


def _header_names(content: str) -> list[str]:
    """Return the accessible column names of the list table: the hidden full name when there is one."""
    names = []
    for header in re.findall(r"<th[^>]*>(.*?)</th>", content, re.S):
        hidden = re.findall(r'<span class="visually-hidden">(.*?)</span>', header, re.S)
        names.append((hidden[-1] if hidden else re.sub(r"<[^>]+>", "", header)).strip())
    return names


@pytest.mark.django_db
def test_audit_survives_editor_assignment_deletion(article, editors):
    """Deleting the WjsEditorAssignment (deassignment/reassignment) must not delete the audit row."""
    assignment = WjsEditorAssignment.objects.create(article=article, editor=editors[0], editor_type="section-editor")
    audit = _make_audit(article, editors[0])

    assignment.delete()

    assert AutomaticAssignmentAudit.objects.filter(pk=audit.pk).exists(), "audit rows are an independent log"


@pytest.mark.django_db
def test_audit_editor_set_null_on_account_deletion(article, editors):
    """Deleting the chosen editor's account keeps the audit row with a null editor."""
    audit = _make_audit(article, editors[0])

    Account.objects.filter(pk=editors[0].pk).delete()
    audit.refresh_from_db()

    assert audit.editor is None, "editor FK must be SET_NULL"


@pytest.mark.django_db
def test_audit_for_journal_scopes_rows(article, editors, journal_factory):
    """for_journal() returns only rows whose article belongs to the given journal."""
    other_journal = journal_factory("JOTHER")
    other_article = Article.objects.create(journal=other_journal, title="Other", owner=editors[0])
    own = _make_audit(article, editors[0])
    _make_audit(other_article, editors[0])

    assert list(AutomaticAssignmentAudit.objects.for_journal(article.journal)) == [own], "only own-journal rows"


@pytest.mark.django_db
def test_audit_latest_for_article_returns_newest(article, editors):
    """latest_for_article() returns the most recently created row (multiple rows per article are allowed)."""
    _make_audit(article, editors[0])
    newest = _make_audit(article, editors[1])

    assert AutomaticAssignmentAudit.objects.latest_for_article(article) == newest, "newest row must win"


@pytest.mark.django_db
def test_audit_page_forbidden_to_section_editor(article, editors, section_editor):
    """Plain section editors cannot see the audit page."""
    client = Client()
    client.force_login(section_editor)

    response = client.get(reverse("wjs-automatic-assignment-audit"))

    assert response.status_code == 403, "only EO/directors may see the audit"


@pytest.mark.django_db
def test_audit_page_lists_own_journal_rows(article, editors, eo_user, journal_factory):
    """EO sees audit rows of the current journal only, newest first."""
    other_journal = journal_factory("JOTHER")
    other_article = Article.objects.create(journal=other_journal, title="Other", owner=editors[0])
    older = _make_audit(article, editors[0])
    newer = _make_audit(article, editors[1], matched_by_keyword=True, matching_keywords_count=2)
    _make_audit(other_article, editors[0])
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit"))

    assert response.status_code == 200, "EO may see the audit"
    assert list(response.context["audits"]) == [newer, older], "own journal only, newest first"


@pytest.mark.django_db
def test_audit_page_filters_by_matched_by_keyword(article, editors, director):
    """The matched-by-keyword filter narrows the list (directors may use the page too)."""
    _make_audit(article, editors[0])
    keyword_match = _make_audit(article, editors[1], matched_by_keyword=True)
    client = Client()
    client.force_login(director)

    response = client.get(reverse("wjs-automatic-assignment-audit"), {"matched_by_keyword": "true"})

    assert response.status_code == 200, "directors may see the audit"
    assert list(response.context["audits"]) == [keyword_match], "only keyword-matched rows"


@pytest.mark.django_db
def test_audit_page_filters_by_editor(article, editors, eo_user):
    """The editor filter narrows the list to the chosen editor."""
    _make_audit(article, editors[0])
    wanted = _make_audit(article, editors[1])
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit"), {"editor": editors[1].pk})

    assert list(response.context["audits"]) == [wanted], "only the chosen editor's rows"


@pytest.mark.django_db
def test_audit_page_renders_rows_without_30d_value(article, editors, eo_user):
    """Rows written before the 30-day field existed (NULL) render as a dash, not an error."""
    _make_audit(article, editors[0], workload_available_30d=None)
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit"))

    assert response.status_code == 200, "page must render legacy rows"
    content = response.content.decode()
    assert "30-day" in content, "the new column header must be shown"
    last_cells = re.findall(r"<td>([^<]*)</td>\s*</tr>", content)
    assert last_cells and last_cells[-1].strip() == "—", "the NULL 30-day cell must render as a dash"


@pytest.mark.django_db
def test_audit_page_shows_keyword_score(article, editors, eo_user):
    """The audit page shows the keyword score as a percentage, and a dash for legacy rows."""
    _make_audit(article, editors[0], keyword_score=0.52)
    _make_audit(article, editors[1], keyword_score=None)
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    assert "Keyword score" in content, "column header shown"
    assert "52%" in content, "score rendered as a whole percentage"
    headers = _header_names(content)
    score_index = headers.index("Keyword score")
    assert headers[score_index - 1] == "Max keyword weight", "score column follows the max keyword weight column"
    score_cells = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S):
        cells = [c.strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if cells:
            score_cells.append(cells[score_index])
    assert sorted(score_cells) == ["52%", "—"], "one row shows its percentage, the legacy row shows a dash"


def _make_candidate(audit, editor, position, **kwargs):
    defaults = {
        "rank": position,
        "selected": position == 1,
        "reason": "selected" if position == 1 else "lower keyword score",
        "keyword_score": 0.5,
        "matching_keywords_count": 1,
        "shared_keywords": [{"word": "quark", "article_weight": 100, "editor_weight": 50}],
        "monthly_workload": 3,
        "workload_30d": 2,
        "workload_cap": 5,
    }
    defaults.update(kwargs)
    return AutomaticAssignmentCandidate.objects.create(audit=audit, editor=editor, position=position, **defaults)


@pytest.mark.django_db
def test_audit_list_title_links_to_the_article_audit(article, editors, eo_user):
    """The title opens the per-article audit and no longer links to the status page."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    audit_url = reverse("wjs-automatic-assignment-audit-article", args=(article.pk,))
    status_url = reverse("wjs_article_details", args=(article.articleworkflow.pk,))
    assert re.search(rf'<a href="{audit_url}[^"]*">[^<]*{article.pk} - ', content), "the title links to the audit view"
    assert not re.search(
        rf'<a href="{status_url}">[^<]*{article.pk} - ', content
    ), "the title no longer links to the status"


@pytest.mark.django_db
def test_article_audit_page_shows_audits_newest_first_with_candidates(article, editors, eo_user):
    """The page lists every audit of the article, newest first, each with its winner summary and candidates."""
    older = _make_audit(article, editors[0])
    newer = _make_audit(article, editors[1], matched_by_keyword=True, matching_keywords_count=1, keyword_score=0.5)
    _make_candidate(newer, editors[1], 1)
    _make_candidate(newer, editors[0], 2)
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit-article", args=(article.pk,)))

    assert response.status_code == 200, "EO may see the article audit"
    assert list(response.context["audits"]) == [newer, older], "all the article's audits, newest first"
    content = response.content.decode()
    assert "quark (article 100, editor 50)" in content, "shared keywords shown with both weights"
    assert "lower keyword score" in content, "the reason each editor lost is shown"
    assert reverse("wjs_article_details", args=(article.articleworkflow.pk,)) in content, "link to the article page"


@pytest.mark.django_db
def test_article_audit_page_without_candidates_says_so(article, editors, eo_user):
    """A legacy or keyword-less audit renders without a candidates table."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit-article", args=(article.pk,)))

    assert response.status_code == 200, "audits without candidates must render"
    assert "No candidates were recorded" in response.content.decode(), "an explicit empty state"


@pytest.mark.django_db
def test_article_audit_page_back_link_keeps_the_list_filters(article, editors, eo_user):
    """The back link returns to the list with the querystring it was opened with."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    response = client.get(
        reverse("wjs-automatic-assignment-audit-article", args=(article.pk,)), {"matched_by_keyword": "true"}
    )

    assert f'{reverse("wjs-automatic-assignment-audit")}?matched_by_keyword=true' in response.content.decode()


@pytest.mark.django_db
def test_article_audit_page_forbidden_to_section_editor(article, editors, section_editor):
    """Plain section editors cannot see the article audit."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(section_editor)

    response = client.get(reverse("wjs-automatic-assignment-audit-article", args=(article.pk,)))

    assert response.status_code == 403, "only EO/directors may see the audit"


@pytest.mark.django_db
def test_article_audit_page_404_without_audits_or_in_another_journal(article, editors, eo_user, journal_factory):
    """No audit for the article in the current journal means 404, whatever other journals hold."""
    other_journal = journal_factory("JOTHER")
    other_article = Article.objects.create(journal=other_journal, title="Other", owner=editors[0])
    _make_audit(other_article, editors[0])
    client = Client()
    client.force_login(eo_user)

    assert client.get(reverse("wjs-automatic-assignment-audit-article", args=(article.pk,))).status_code == 404
    assert client.get(reverse("wjs-automatic-assignment-audit-article", args=(other_article.pk,))).status_code == 404


@pytest.mark.django_db
def test_candidates_are_kept_when_editor_account_is_deleted(article, editors):
    """Deleting a candidate's account keeps the snapshot row with a null editor."""
    audit = _make_audit(article, editors[0])
    candidate = _make_candidate(audit, editors[1], 2)

    Account.objects.filter(pk=editors[1].pk).delete()
    candidate.refresh_from_db()

    assert candidate.editor is None, "editor FK must be SET_NULL"


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ("wjs-automatic-assignment-audit", "wjs-automatic-assignment-audit-article"))
def test_audit_pages_render_the_view_title_in_the_base_title_block(article, editors, eo_user, url_name):
    """Both pages take their title from the view's ``title`` attribute, rendered once by the base template."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)
    args = (article.pk,) if url_name.endswith("-article") else ()

    response = client.get(reverse(url_name, args=args))

    content = response.content.decode()
    assert response.context["view"].title == "Automatic assignment audit", "the title is a view attribute"
    assert re.search(r"<h1[^>]*>\s*<span>\s*Automatic assignment audit\s*</span>", content), "rendered by the base h1"
    assert content.count("<h1") == 1, "the template must not add a second h1"


@pytest.mark.django_db
def test_audit_list_is_accessible(article, editors, eo_user):
    """WCAG 2.2: named search form and table region, table caption, links distinguishable out of context."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    assert re.search(r'<form[^>]*role="search"[^>]*aria-label="[^"]+"', content), "the filter form is a named landmark"
    assert re.search(
        r'<div class="table-responsive"\s+role="region"\s+aria-labelledby="audit-list-caption"\s+tabindex="0"', content
    ), "the scrollable table is a focusable, named region (keyboard users can scroll it)"
    assert 'id="audit-list-caption"' in content, "the region's label exists"


@pytest.mark.django_db
def test_article_audit_page_is_accessible(article, editors, eo_user):
    """WCAG 2.2: heading hierarchy, labelled sections, focusable table regions, row headers, no low-contrast text."""
    audit = _make_audit(article, editors[0])
    _make_candidate(audit, editors[0], 1)
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit-article", args=(article.pk,))).content.decode()

    modal_start = content.rindex('<div class="modal fade', 0, content.index('id="audit-scores-help"'))
    first_audit = content.index('<section class="card')
    page = content[:modal_start] + content[first_audit:]  # the modal is its own dialog, with its own headings
    levels = [int(level) for level in re.findall(r"<h([1-6])[\s>]", page)]
    assert levels[:4] == [1, 2, 3, 4], f"headings must nest without skipping levels, got {levels}"
    assert re.search(r"<h2[^>]*>\s*" + str(article.pk) + r" - ", content), "the article is the h2 under the page title"
    assert re.search(
        rf'<a\s[^>]*href="{reverse("wjs_article_details", args=(article.articleworkflow.pk,))}"[^>]*>'
        r'<i class="bi bi-clipboard2-data" aria-hidden="true"></i>'
        rf'<span class="visually-hidden">Article page for article {article.pk}</span></a>',
        content,
    ), "the article page link is the icon button, inside the article heading"
    assert re.search(r'<a class="ms-auto" href="[^"]*automatic-assignment-audit/\?', content), "back link on the right"
    assert f'aria-labelledby="audit-{audit.pk}-heading"' in content, "each audit section is named by its heading"
    assert re.search(r'role="region"\s+aria-labelledby="audit-\d+-candidates"\s+tabindex="0"', content), "table region"
    assert "<caption" in content, "the candidates table has a caption"
    assert re.search(r'<th scope="row"[^>]*>\s*Editor0', content), "the editor is the row header"
    assert "text-muted" not in content, "text-muted lacks contrast on striped/highlighted rows"


@pytest.mark.django_db
def test_audit_list_headers_are_abbreviated_with_the_full_name_for_assistive_technology(article, editors, eo_user):
    """Abbreviated headers show a tooltip to mouse users and expose the full name to screen readers."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    assert (
        '<th scope="col"><abbr title="Keyword score" aria-hidden="true">KW score</abbr>'
        '<span class="visually-hidden">Keyword score</span></th>'
    ) in content, "visible abbreviation with title, full name hidden visually only"
    assert _header_names(content) == [
        "Date",
        "Article",
        "Article page",
        "Editor",
        "Matched by keyword",
        "Matching keywords",
        "Max keyword weight",
        "Keyword score",
        "Workload cap",
        "Available workload",
        "Available (30-day)",
    ], "every column keeps its full accessible name"


@pytest.mark.django_db
def test_audit_list_has_one_cell_per_column_and_no_article_page_button(article, editors, eo_user):
    """The title cell holds only the audit link; no cell links to the article page."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    cells = re.findall(r"<td[^>]*>(.*?)</td>", re.search(r"<tbody>(.*?)</tbody>", content, re.S).group(1), re.S)
    assert "Article page" not in cells[1], "no button in the title cell"
    assert "bi-clipboard2-data" not in content, "the article page icon button is gone from the list"
    assert len(cells) == len(_header_names(content)), "one cell per column"


def _audit_link(response) -> str | None:
    """Return the article audit link found in the article page, if any."""
    match = re.search(
        r'<a\s[^>]*href="(/[^"]*automatic-assignment-audit/article/\d+/)"[^>]*>(.*?)</a>',
        response.content.decode(),
        re.S,
    )
    return match.group(0) if match else None


@pytest.mark.django_db
@pytest.mark.parametrize("role", ("eo", "director"))
def test_article_page_links_to_the_article_audit_for_eo_and_director(
    assigned_article, section_editor, eo_user, director, role
):
    """EO and director see a plain link to the article audit, after the editor's other controls."""
    _make_audit(assigned_article, section_editor)
    client = Client()
    client.force_login(eo_user if role == "eo" else director)

    response = client.get(reverse("wjs_article_details", args=(assigned_article.articleworkflow.pk,)))

    link = _audit_link(response)
    assert link, f"{role} must see the audit link"
    assert reverse("wjs-automatic-assignment-audit-article", args=(assigned_article.pk,)) in link, "right target"
    assert 'class="btn' not in link, "a proper link, not a button"
    assert '<i class="bi bi-card-checklist" aria-hidden="true"></i>' in link, "the link shows an icon"
    assert 'title="Automatic assignment audit"' in link, "tooltip for mouse users"
    assert '<span class="visually-hidden">Automatic assignment audit</span>' in link, "text alternative"
    visible_text = re.sub(r'<span class="visually-hidden">.*?</span>|<[^>]+>', "", link, flags=re.S).strip()
    assert visible_text == "", f"icon only, no visible label, got {visible_text!r}"
    editor_cell = response.content.decode().split("Set visibility rights")[-1]
    assert "automatic-assignment-audit/article" in editor_cell, "rightmost: after the visibility rights button"


@pytest.mark.django_db
def test_article_page_has_no_audit_link_for_an_audit_of_another_editor(assigned_article, eo_user):
    """The link follows the current editor: an audit of someone else (e.g. a previous editor) does not show it."""
    previous_editor = Account.objects.create(username="previous-editor", email="previous-editor@example.com")
    _make_audit(assigned_article, previous_editor)
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs_article_details", args=(assigned_article.articleworkflow.pk,)))

    assert response.status_code == 200
    assert _audit_link(response) is None, "no audit for the current editor, no link"


@pytest.mark.django_db
def test_article_page_has_no_audit_link_without_audits(assigned_article, eo_user):
    """No audit recorded means no dead link."""
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs_article_details", args=(assigned_article.articleworkflow.pk,)))

    assert response.status_code == 200
    assert _audit_link(response) is None, "nothing to show"


@pytest.mark.django_db
def test_article_page_hides_the_audit_link_from_the_assigned_editor(assigned_article, section_editor):
    """The audit is not for the article's own editor."""
    _make_audit(assigned_article, section_editor)
    client = Client()
    client.force_login(section_editor)

    response = client.get(reverse("wjs_article_details", args=(assigned_article.articleworkflow.pk,)))

    assert response.status_code == 200
    assert _audit_link(response) is None, "only EO and directors see the link"


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ("wjs-automatic-assignment-audit", "wjs-automatic-assignment-audit-article"))
def test_audit_views_are_for_eo_and_director_only(article, editors, eo_user, director, section_editor, url_name):
    """EO and directors get in; staff without EO, editors and anonymous users do not."""
    _make_audit(article, editors[0])
    args = (article.pk,) if url_name.endswith("-article") else ()
    url = reverse(url_name, args=args)
    staff_only = Account.objects.create(username="staff-only", email="staff-only@example.com", is_staff=True)
    staff_only.is_active = True
    staff_only.jcomprofile.gdpr_checkbox = True
    staff_only.jcomprofile.save()
    staff_only.save()

    statuses = {}
    for label, user in (("eo", eo_user), ("director", director), ("staff", staff_only), ("editor", section_editor)):
        client = Client()
        client.force_login(user)
        statuses[label] = client.get(url).status_code
    anonymous = Client().get(url)

    assert statuses == {"eo": 200, "director": 200, "staff": 403, "editor": 403}, "only EO and directors"
    assert anonymous.status_code in (302, 403), "anonymous users are sent away"
    assert anonymous.status_code != 200, "never served"


SCORES_HELP_TEMPLATE = "wjs_review/automatic_assignment_audit/elements/scores_help.html"
SCORES_EXPLAINED = (
    "Algorithm",
    "Matched by keyword",
    "Rank",
    "Outcome",
    "Matching keywords",
    "Max keyword weight",
    "Keyword score",
    "Workload cap",
    "Available workload",
    "Available (30-day)",
)


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ("wjs-automatic-assignment-audit", "wjs-automatic-assignment-audit-article"))
def test_audit_pages_share_one_scores_help_modal(article, editors, eo_user, url_name):
    """Both pages include the same template: a button opening a modal that explains every score."""
    _make_audit(article, editors[0])
    args = (article.pk,) if url_name.endswith("-article") else ()
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse(url_name, args=args))

    content = response.content.decode()
    assert SCORES_HELP_TEMPLATE in [t.name for t in response.templates], "the shared template is used"
    assert re.search(
        r'<button [^>]*data-bs-target="#audit-scores-help"[^>]*data-bs-toggle="modal"[^>]*type="button"', content
    ), "a button (an action, not a link) opens the modal"
    assert 'id="audit-scores-help"' in content and 'aria-labelledby="modal-title-audit-scores-help"' in content
    modal = content.split('id="audit-scores-help"', 1)[1]
    terms = [re.sub(r"<[^>]+>|\s+", " ", t).strip() for t in re.findall(r"<dt[^>]*>(.*?)</dt>", modal, re.S)]
    terms = terms[: terms.index("Available (30-day)") + 1]  # the page's own <dl> follows the modal
    assert terms == list(SCORES_EXPLAINED), "the modal explains each score, in the order of the columns"
    assert content.count('id="audit-scores-help"') == 1, "one modal per page"
    assert re.search(r'<div class="modal fade modal-xl"[^>]*id="audit-scores-help"', content), "extra large modal"


@pytest.mark.django_db
def test_scores_help_modal_describes_the_computation(article, editors, eo_user):
    """The explanations carry the rules of the Editor Selection logic spec, not just labels."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    assert "brake on" in content, "the cap explanation mentions the EO brake on"
    assert "30 days" in content, "the 30-day availability explanation names the window"
    assert "100%" in content, "the keyword score explanation says what 100% means"


@pytest.mark.django_db
def test_scores_help_modal_section_headings_are_larger_than_the_text(article, editors, eo_user):
    """The modal's h3 section headings use fs-5, so they stand out from the body text."""
    _make_audit(article, editors[0])
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    sections = re.findall(r'<h3 class="([^"]*)">(Selection|Keywords|Workload)</h3>', content)
    assert [title for _, title in sections] == ["Selection", "Keywords", "Workload"], "the three sections"
    assert all(css == "fs-5" for css, _ in sections), "section headings are fs-5"
