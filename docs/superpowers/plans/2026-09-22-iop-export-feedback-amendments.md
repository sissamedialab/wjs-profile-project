# IOP Export Feedback Amendments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Amend the JCAP/IOP metadata XML export to satisfy IOP's feedback on a real sample export: a missing document envelope, non-consecutive author sequence numbers, custom fields that must be omitted (not just emptied) when OA isn't agreed, a `Copyright/Licence Type` value constrained to IOP's own preset vocabulary, the correct institution for transformative-agreement articles, and an EO warning when the corresponding author's affiliation data is incomplete.

**Architecture:** All five changes are additive amendments inside the existing `metadata_export` package (`dto.py`/`mappers.py`/`service.py`/the Django template) and `SendProductionXMLToPublisher` (`logic__production.py`) — no new modules, no new DTO fields. Each task is independently testable and does not require the others to land first, except Task 5, which depends on Task 4's `map_affiliation` signature.

**Tech Stack:** Django, django templates, pytest/pytest-django (same stack as the rest of this feature).

**Spec:** `docs/superpowers/specs/2026-09-02-iop-xml-export-design.md` (updated in commit `626881d5` to document these constraints — the *Document envelope*, `author/@author_seq`, `salutation`, TA institution, corresponding-author-completeness, and `<configurable_data_fields>` sections are the ones this plan implements).

## Global Constraints

- Double quotes only, 4-space indent, 119-char line length (black), docstrings imperative/capitalized/period-terminated, all public functions documented — per this repo's code-style rules.
- TDD: write the failing test, watch it fail for the right reason, then implement.
- Run tests from `janeway/src` using the `janeway-upstream` pyenv venv: `cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "..." -v`. Prefer `-n2`/`-n4` (parallel, isolated-worker DBs) over a large sequential multi-file run — this suite has shown test-ordering artifacts in single-process sequential runs before (unrelated tests erroring only when combined) that do not reproduce in parallel mode; if that happens again, re-verify in parallel before treating it as a real regression.
- **Known environment risk, check first:** as of this plan's writing, running tests in this venv fails at Django app-loading with `TypeError: CheckConstraint.__init__() got an unexpected keyword argument 'condition'` — `janeway/src`'s checked-out code (`submission/models.py`) now uses a Django 5.1+-only `CheckConstraint` kwarg, but the `janeway-upstream` venv has Django 4.2.30 installed. This is environment drift unrelated to this plan's code changes (six days of unrelated work touched shared paths). Before starting Task 1, run a one-file sanity test (e.g. `pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k test_map_rev -v`) to confirm whether this is still broken; if so, this needs to be resolved (check `git log -1 janeway/src`, whether a different Django version needs installing in this venv, or whether `janeway/src` needs to be pointed at an older commit) before any task's tests can run. This is infrastructure, not part of this plan's scope — do not "fix" it by changing `wjs-profile-project` code.
- The template file `wjs/plugins/wjs_review/templates/wjs_review/metadata_export/jcap_metadata_export.xml` already has an **uncommitted** edit (made by the user directly) adding the `<article_set>`/`<!DOCTYPE>` wrapper — Task 1 commits it together with its test fixes, do not re-make this edit from scratch; if it's missing, see Task 1 Step 3 for the exact diff to reapply.

## Review Focus

- **Zero authors on the article** (`article.frozenauthor_set` empty): `map_authors` must return `[]` without error, and `corresponding_author_affiliation_is_incomplete` (Task 5) must return `True` gracefully (no `AttributeError` on a `None` frozen author) rather than crash the zip build. Covered by Task 2's empty-list test and Task 5's "no correspondence author" test.
- **Corresponding author is not the first author** (e.g. 2nd or 3rd in `order`): easy to accidentally hardcode "author #1" when implementing the TA/completeness checks. Covered by Task 5's tests, which put the corresponding author at `order=2`.
- **`article.submission_data` doesn't exist at all** (reverse one-to-one raises `ObjectDoesNotExist` on plain attribute access) for the TA check in Task 4: must degrade to "not TA", not raise. Covered by Task 4's test using the plain `article` fixture (no `submission_data` access-mode set at all).
- **OA agreed via a non-TA code** (e.g. plain `"open-access"`) with `article.submission_data.affiliation` still set: Task 4's TA override must **not** fire — only the specific `"oa-transformative-agreement"` code triggers it. Covered by Task 4's own dedicated test.
- **A `ControlledAffiliation` row exists but its `organization` (or the organization's `location`) is unset**: this is the actual shape of "affiliation guaranteed to exist, but incomplete" that Task 5's EO warning exists for — not "no `ControlledAffiliation` row at all". Covered by Task 5's test, which creates a bare `ControlledAffiliation` with no `organization`.

---

### Task 1: Document envelope (`<article_set>`/`<!DOCTYPE>` wrapper)

**Files:**
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/metadata_export/jcap_metadata_export.xml` (already edited, uncommitted — see Global Constraints)
- Modify: `wjs/plugins/wjs_review/tests/test_metadata_export.py:975-1022` (`test_template_renders_from_hand_built_dto`, `test_template_escapes_abstract_special_characters`)

**Interfaces:**
- Produces: no code interface change — this is a template/test-only task. The rendered XML's root element is now `<article_set>`, with `<article>` as its sole child (was: `<article>` as the root).

- [ ] **Step 1: Confirm the template edit is present**

Run: `git -C /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto diff wjs/plugins/wjs_review/templates/wjs_review/metadata_export/jcap_metadata_export.xml`

Expected: a diff adding 4 lines at the top (`<!--sl.dtd v.4.28.5-->`, `<!DOCTYPE article_set SYSTEM "sl.dtd">`, `<article_set dtd_version="4.28.5">`) and 1 line at the bottom (`</article_set>`), wrapping the existing `<article ...>...</article>` unchanged. If this diff is empty (someone reverted it), reapply it: open the file, and right after the `<?xml version="1.0" encoding="UTF-8"?>` line insert:
```xml
<!--sl.dtd v.4.28.5-->
<!DOCTYPE article_set SYSTEM "sl.dtd">
<article_set dtd_version="4.28.5">
```
and right after the file's final `</article>` line (currently the last content line) add:
```xml
</article_set>
```

- [ ] **Step 2: Write the failing tests (update the two existing template tests)**

In `wjs/plugins/wjs_review/tests/test_metadata_export.py`, find `test_template_renders_from_hand_built_dto`. It currently does:
```python
    root = ET.fromstring(rendered)
    assert root.tag == "article"
    assert root.attrib["lang"] == "en"
    assert root.attrib["ms_no"] == "JCOM_2101_2022_A01"
    assert root.attrib["rev"] == "2"
    assert root.find("article_title").text == "A Hand-Built Sample Title"
    assert root.find("history/ms_id/rev_id").text == "2", "rev_id must be decoupled from the root @rev"

    author_el = root.find("author_list/author")
    assert author_el.attrib["corr"] == "true"
    assert author_el.find("last_name").text == "Doe"
    assert author_el.find("affiliation/inst").text == "Example University"
    assert author_el.find("affiliation/country").text == "Italy"

    assert root.find("history/ms_id/received_date/year").text == "2026"
    assert root.find("history/ms_id/revised_date/year") is None or root.find(
        "history/ms_id/revised_date/year",
    ).text in (None, "")

    assert root.find("fundref_information/no_funders").text == "True"
```
Replace it with:
```python
    root = ET.fromstring(rendered)
    assert root.tag == "article_set", "IOP feedback: the whole document is wrapped in <article_set>"
    assert root.attrib["dtd_version"] == "4.28.5"

    article_el = root.find("article")
    assert article_el is not None, "the <article> element must be article_set's child, not the root"
    assert article_el.attrib["lang"] == "en"
    assert article_el.attrib["ms_no"] == "JCOM_2101_2022_A01"
    assert article_el.attrib["rev"] == "2"
    assert article_el.find("article_title").text == "A Hand-Built Sample Title"
    assert article_el.find("history/ms_id/rev_id").text == "2", "rev_id must be decoupled from the root @rev"

    author_el = article_el.find("author_list/author")
    assert author_el.attrib["corr"] == "true"
    assert author_el.find("last_name").text == "Doe"
    assert author_el.find("affiliation/inst").text == "Example University"
    assert author_el.find("affiliation/country").text == "Italy"

    assert article_el.find("history/ms_id/received_date/year").text == "2026"
    assert article_el.find("history/ms_id/revised_date/year") is None or article_el.find(
        "history/ms_id/revised_date/year",
    ).text in (None, "")

    assert article_el.find("fundref_information/no_funders").text == "True"
```

Then find `test_template_escapes_abstract_special_characters`. It currently does:
```python
    root = ET.fromstring(rendered)
    assert root.find("abstract").text == "A <p>paragraph</p> with a & an ampersand."
```
Replace it with:
```python
    root = ET.fromstring(rendered)
    article_el = root.find("article")
    assert article_el.find("abstract").text == "A <p>paragraph</p> with a & an ampersand."
```

- [ ] **Step 2b: Also add a document-envelope-specific unit test**

Add this new test right after `test_template_escapes_abstract_special_characters`:
```python
def test_template_wraps_article_in_article_set_with_doctype():
    """IOP feedback: the document envelope (DOCTYPE + <article_set> wrapper) was missing entirely."""
    dto = ArticleExportDTO()

    rendered = render_to_string(
        "wjs_review/metadata_export/jcap_metadata_export.xml",
        {"article": dto, "export_date": "2026-2-9 00:00:00.0"},
    )

    assert '<!DOCTYPE article_set SYSTEM "sl.dtd">' in rendered
    assert rendered.strip().endswith("</article_set>")

    root = ET.fromstring(rendered)
    assert root.tag == "article_set"
    assert len(root) == 1, "article_set must wrap exactly one <article> element"
    assert root[0].tag == "article"
```

- [ ] **Step 3: Run the tests to verify they fail**

First, confirm the environment works at all (see Global Constraints' environment-risk note). Then run:

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "test_template" -v`

Expected: `test_template_wraps_article_in_article_set_with_doctype` PASSES already (it tests the template, which is already edited) if Step 1's template edit is present; `test_template_renders_from_hand_built_dto` and `test_template_escapes_abstract_special_characters` PASS too, since Step 2 already updated them to match the already-edited template. **If instead the template edit from Step 1 is missing**, all three FAIL — apply Step 1's reapply-diff first, and Step 3 becomes the verification pass.

(This task is unusual: the "production" change — the template — was already made before the tests. The steps above still follow write-test-then-verify order for the *test* changes; what's being verified is that test and template now agree.)

- [ ] **Step 4: Confirm no other test parses the rendered XML's root and assumes `root.tag == "article"`**

Run: `grep -n "ET.fromstring\|root\." /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py`

Expected: only the two tests touched in Step 2, plus the new one from Step 2b, reference `root`/`ET.fromstring`. If another one shows up, apply the same `root.find("article")` fix to it before proceeding.

- [ ] **Step 5: Lint and commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto
pre-commit run --files wjs/plugins/wjs_review/templates/wjs_review/metadata_export/jcap_metadata_export.xml wjs/plugins/wjs_review/tests/test_metadata_export.py
git add wjs/plugins/wjs_review/templates/wjs_review/metadata_export/jcap_metadata_export.xml wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "fix(metadata-export): wrap the export in IOP's required <article_set>/<!DOCTYPE> envelope"
```

---

### Task 2: `author_seq` — consecutive numbering, not raw `FrozenAuthor.order`

**Files:**
- Modify: `wjs/plugins/wjs_review/metadata_export/mappers.py:193-226` (`map_author`, `map_authors`)
- Modify: `wjs/plugins/wjs_review/tests/test_metadata_export.py:364-374` (`test_map_authors_is_ordered_by_frozen_author_order`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `map_author(frozen_author, article, seq=None) -> AuthorExportDTO` — the new optional third parameter. Task 4 will touch the same function body (the `affiliation=map_affiliation(frozen_author)` line) but not this signature; do not remove the `seq` parameter later.

- [ ] **Step 1: Write the failing test**

In `wjs/plugins/wjs_review/tests/test_metadata_export.py`, find `test_map_authors_is_ordered_by_frozen_author_order` (currently uses orders `3, 1, 2` and asserts `seq == [1, 2, 3]`, which passes today too — it doesn't prove the fix). Add a new test right after it:
```python
@pytest.mark.django_db
def test_map_authors_seq_is_consecutive_even_when_order_has_gaps(article):
    """IOP feedback: @author_seq must be consecutive; FrozenAuthor.order can have gaps."""
    article.frozenauthor_set.all().delete()
    FrozenAuthor.objects.create(article=article, order=9, first_name="Third")
    FrozenAuthor.objects.create(article=article, order=1, first_name="First")
    FrozenAuthor.objects.create(article=article, order=5, first_name="Second")

    authors = mappers.map_authors(article)

    assert [a.first_name for a in authors] == ["First", "Second", "Third"], "ordering still follows .order"
    assert [a.seq for a in authors] == [1, 2, 3], "but @author_seq must be the 1-indexed position, not .order"


@pytest.mark.django_db
def test_map_authors_empty_list_for_article_with_no_authors(article):
    article.frozenauthor_set.all().delete()

    assert mappers.map_authors(article) == []
```

- [ ] **Step 2: Run the tests to verify the gap test fails**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "test_map_authors_seq_is_consecutive or test_map_authors_empty_list" -v`

Expected: `test_map_authors_seq_is_consecutive_even_when_order_has_gaps` FAILS with `assert [1, 5, 9] == [1, 2, 3]` (or similar) — the current code returns the raw `.order` values as `seq`. `test_map_authors_empty_list_for_article_with_no_authors` PASSES already (an empty list comprehension is already correct); that's fine, it's a regression pin for Task 5, not a new-behavior test here.

- [ ] **Step 3: Write the minimal implementation**

In `wjs/plugins/wjs_review/metadata_export/mappers.py`, change:
```python
def map_author(frozen_author, article) -> AuthorExportDTO:
    """Map one ``FrozenAuthor`` (in the context of its ``article``) to the export author shape."""
    is_corresponding = bool(
        frozen_author.author_id
        and article.correspondence_author_id
        and frozen_author.author_id == article.correspondence_author_id,
    )
    # Settled (export_spec.md): always FrozenAuthor.pk, never frozen_author.author_id/linked
    # Account id -- an Account's real id means nothing to IOP and risks colliding with a
    # different ID space than FrozenAuthor.pk's own.
    user_id = str(frozen_author.pk)

    # FrozenAuthor.email already falls back frozen_email -> linked Account.email; guarded against
    # None (no frozen_email and no linked account), which the property itself doesn't do -- an
    # unguarded None would render as the literal string "None" in the XML.
    email = frozen_author.email or ""

    return AuthorExportDTO(
        seq=frozen_author.order,
        is_corresponding=is_corresponding,
        user_id=user_id,
        salutation=frozen_author.name_prefix,
        first_name=frozen_author.first_name,
        middle_name=frozen_author.middle_name,
        last_name=frozen_author.last_name,
        email=email or "",
        orcid=frozen_author.frozen_orcid,
        affiliation=map_affiliation(frozen_author),
    )


def map_authors(article) -> list[AuthorExportDTO]:
    """Map every ``article.frozenauthor_set``, ordered, to the export ``<author_list>``."""
    return [map_author(frozen_author, article) for frozen_author in article.frozenauthor_set.order_by("order")]
```
to:
```python
def map_author(frozen_author, article, seq=None) -> AuthorExportDTO:
    """Map one ``FrozenAuthor`` (in the context of its ``article``) to the export author shape."""
    is_corresponding = bool(
        frozen_author.author_id
        and article.correspondence_author_id
        and frozen_author.author_id == article.correspondence_author_id,
    )
    # Settled (export_spec.md): always FrozenAuthor.pk, never frozen_author.author_id/linked
    # Account id -- an Account's real id means nothing to IOP and risks colliding with a
    # different ID space than FrozenAuthor.pk's own.
    user_id = str(frozen_author.pk)

    # FrozenAuthor.email already falls back frozen_email -> linked Account.email; guarded against
    # None (no frozen_email and no linked account), which the property itself doesn't do -- an
    # unguarded None would render as the literal string "None" in the XML.
    email = frozen_author.email or ""

    return AuthorExportDTO(
        seq=frozen_author.order if seq is None else seq,
        is_corresponding=is_corresponding,
        user_id=user_id,
        salutation=frozen_author.name_prefix,
        first_name=frozen_author.first_name,
        middle_name=frozen_author.middle_name,
        last_name=frozen_author.last_name,
        email=email or "",
        orcid=frozen_author.frozen_orcid,
        affiliation=map_affiliation(frozen_author),
    )


def map_authors(article) -> list[AuthorExportDTO]:
    """
    Map every ``article.frozenauthor_set``, ordered, to the export ``<author_list>``.

    Settled (IOP feedback): ``@author_seq`` is the author's 1-indexed **position** in this
    ordering, not the raw ``FrozenAuthor.order`` value -- ``order`` has no DB constraint keeping
    it gap-free (author reordering/removal can leave e.g. ``1, 5, 9``), and IOP requires
    ``@author_seq`` to be consecutive. ``order`` still decides the *ordering*.
    """
    return [
        map_author(frozen_author, article, seq=position)
        for position, frozen_author in enumerate(article.frozenauthor_set.order_by("order"), start=1)
    ]
```

Note: this step does **not** touch the `affiliation=map_affiliation(frozen_author)` line — Task 4 changes that line separately.

- [ ] **Step 4: Run the tests to verify they pass**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "map_author" -v`

Expected: all PASS, including the pre-existing `test_map_authors_is_ordered_by_frozen_author_order` and every other `map_author*`/`map_authors*` test.

- [ ] **Step 5: Lint and commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto
pre-commit run --files wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "fix(metadata-export): author_seq is a consecutive position, not raw FrozenAuthor.order"
```

---

### Task 3: OA custom fields — omit when not agreed, preset `Copyright/Licence Type` vocabulary

**Files:**
- Modify: `wjs/plugins/wjs_review/metadata_export/mappers.py:391-453` (`map_custom_fields`)
- Modify: `wjs/plugins/wjs_review/tests/test_metadata_export.py:525-653` (several `test_map_custom_fields_*` tests)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `map_custom_fields(article) -> list[CustomFieldExportDTO]` — same signature, but now returns 6 entries (not 8) when OA isn't agreed.

- [ ] **Step 1: Write the failing tests**

In `wjs/plugins/wjs_review/tests/test_metadata_export.py`, replace `test_map_custom_fields_oa_not_agreed_by_default`:
```python
@pytest.mark.django_db
def test_map_custom_fields_oa_not_agreed_by_default(article):
    fields = mappers.map_custom_fields(article)

    by_code = {f.code: f for f in fields}
    assert by_code["OA Agreed"].value == "No", "no submission_data at all must default to No"
    assert "OA date requested" not in by_code, "IOP feedback: omitted entirely, not just empty, when not agreed"
    assert "OA licence type" not in by_code, "IOP feedback: omitted entirely, not just empty, when not agreed"
    assert by_code["Copyright/Licence Type"].value == "Standard", "IOP's preset vocabulary, not license.short_name"
```

Replace `test_map_custom_fields_licence_fields_when_oa_not_agreed`:
```python
@pytest.mark.django_db
def test_map_custom_fields_licence_fields_when_oa_not_agreed(article):
    """IOP feedback: with OA not agreed, both OA-only fields are omitted and Copyright/Licence Type is "Standard"."""
    licence = Licence.objects.create(name="Creative Commons", short_name="CC BY 4.0", url="https://example.org")
    article.license = licence

    fields = mappers.map_custom_fields(article)
    by_code = {f.code: f for f in fields}

    assert "OA licence type" not in by_code
    assert "OA date requested" not in by_code
    assert by_code["Copyright/Licence Type"].value == "Standard", (
        "IOP's preset vocabulary only accepts Standard/Open Access -- never license.short_name"
    )
```

Replace `test_map_custom_fields_produces_all_eight_entries_in_order` with two tests, one per OA state:
```python
@pytest.mark.django_db
def test_map_custom_fields_produces_six_entries_in_order_when_oa_not_agreed(article):
    fields = mappers.map_custom_fields(article)

    assert [f.code for f in fields] == [
        "OA Agreed",
        "Copyright/Licence Type",
        "Production Comments",
        "Special Issue",
        "Section",
        "Additional Authors",
    ]


@pytest.mark.django_db
def test_map_custom_fields_produces_all_eight_entries_in_order_when_oa_agreed(article):
    access_mode = AccessMode.objects.create(name="Open Access", code="open-access")
    article.submission_data.access_mode = access_mode
    article.submission_data.save()

    fields = mappers.map_custom_fields(article)

    assert [f.code for f in fields] == [
        "OA Agreed",
        "OA date requested",
        "OA licence type",
        "Copyright/Licence Type",
        "Production Comments",
        "Special Issue",
        "Section",
        "Additional Authors",
    ]
```

Leave `test_map_custom_fields_oa_agreed_sets_date_and_is_yes`, `test_map_custom_fields_oa_agreed_for_every_oa_flavored_access_mode`, and `test_map_custom_fields_copyright_licence_type_is_open_access_when_oa_agreed` untouched — they already only assert on the OA-agreed path, which isn't changing.

- [ ] **Step 2: Run the tests to verify they fail**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "map_custom_fields_oa_not_agreed or map_custom_fields_licence_fields or map_custom_fields_produces" -v`

Expected: FAIL — `test_map_custom_fields_oa_not_agreed_by_default` on `assert "OA date requested" not in by_code` (it's currently there with `value=""`); `test_map_custom_fields_licence_fields_when_oa_not_agreed` on `Copyright/Licence Type` still being `"CC BY 4.0"`; `test_map_custom_fields_produces_six_entries_in_order_when_oa_not_agreed` on the list having 8 codes, not 6.

- [ ] **Step 3: Write the minimal implementation**

In `wjs/plugins/wjs_review/metadata_export/mappers.py`, change `map_custom_fields` from:
```python
def map_custom_fields(article) -> list[CustomFieldExportDTO]:
    """
    Map the ``<configurable_data_fields><custom_fields>`` entries, in the spec's fixed order.

    Note: export_spec.md's numbered list has 7 entries, but entry 3 ("OA licence type" **and**
    "Copyright/Licence Type") is explicitly two separate ``<custom_fields>`` elements -- so this
    produces 8 ``CustomFieldExportDTO`` rows, matching the shape of both reference fixtures (the
    JINST one has all 8; the JCAP one is missing a couple, but is documented as the
    templated/placeholder-heavy fixture, not the shape to match).
    """
    fields: list[CustomFieldExportDTO] = []

    oa_agreed = _is_oa_agreed(article)
    fields.append(CustomFieldExportDTO(code="OA Agreed", name="OA Requested?", value="Yes" if oa_agreed else "No"))

    # "OA date requested" uses the article's acceptance date, not its submission date -- per
    # export_spec.md this is the acceptance date for OA-agreed articles, not a literal "when was
    # OA requested" timestamp (Janeway has no such field).
    oa_date_requested = format_date_dd_mon_yyyy(article.date_accepted) if oa_agreed else ""
    fields.append(CustomFieldExportDTO(code="OA date requested", name="Date OA Requested", value=oa_date_requested))

    license_short_name = article.license.short_name if article.license else ""
    fields.append(CustomFieldExportDTO(code="OA licence type", name="OA Licence Type", value=license_short_name))

    # Settled (export_spec.md): no longer the same value as "OA licence type" above -- fixed
    # literal "Open Access" when OA Agreed, else the same license.short_name fallback.
    # Unconfirmed alternative (wjs/specs#3126, no verified source): this field might instead be a
    # fixed three-value controlled vocabulary (SISSA-IOP/Authors/CERN); not acted on, this rule
    # holds until a source for that vocabulary is confirmed.
    copyright_licence_value = "Open Access" if oa_agreed else license_short_name
    fields.append(
        CustomFieldExportDTO(
            code="Copyright/Licence Type",
            name="Copyright/Licence Type",
            value=copyright_licence_value,
        ),
    )

    # Explicitly deferred, per export_spec.md -- never populated from any field.
    fields.append(CustomFieldExportDTO(code="Production Comments", name="Production Comments", value=""))
```
to:
```python
def map_custom_fields(article) -> list[CustomFieldExportDTO]:
    """
    Map the ``<configurable_data_fields><custom_fields>`` entries, in the spec's fixed order.

    Re-settled (IOP feedback): when OA isn't agreed, "OA date requested" and "OA licence type"
    are **omitted from the list entirely**, not emitted with an empty value -- so this produces
    8 ``CustomFieldExportDTO`` rows when OA is agreed, 6 when it isn't.
    """
    fields: list[CustomFieldExportDTO] = []

    oa_agreed = _is_oa_agreed(article)
    fields.append(CustomFieldExportDTO(code="OA Agreed", name="OA Requested?", value="Yes" if oa_agreed else "No"))

    if oa_agreed:
        # "OA date requested" uses the article's acceptance date, not its submission date -- per
        # export_spec.md this is the acceptance date for OA-agreed articles, not a literal "when
        # was OA requested" timestamp (Janeway has no such field).
        oa_date_requested = format_date_dd_mon_yyyy(article.date_accepted)
        fields.append(
            CustomFieldExportDTO(code="OA date requested", name="Date OA Requested", value=oa_date_requested),
        )

        license_short_name = article.license.short_name if article.license else ""
        fields.append(CustomFieldExportDTO(code="OA licence type", name="OA Licence Type", value=license_short_name))

    # Re-settled (IOP feedback): IOP confirmed this field accepts only a small preset vocabulary
    # -- "Open Access" when OA Agreed, else the fixed literal "Standard" -- never an arbitrary
    # license.short_name. A third value exists (a non-standard copyright agreement) but IOP
    # notifies the journal about that case directly; this mapper never generates it.
    copyright_licence_value = "Open Access" if oa_agreed else "Standard"
    fields.append(
        CustomFieldExportDTO(
            code="Copyright/Licence Type",
            name="Copyright/Licence Type",
            value=copyright_licence_value,
        ),
    )

    # Explicitly deferred, per export_spec.md -- never populated from any field.
    fields.append(CustomFieldExportDTO(code="Production Comments", name="Production Comments", value=""))
```
(The rest of the function — Special Issue, Section, Additional Authors — is unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "map_custom_fields" -v`

Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto
pre-commit run --files wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "fix(metadata-export): omit OA-only custom fields when not agreed; preset Copyright/Licence Type"
```

---

### Task 4: TA institution — `article.submission_data.affiliation` for the corresponding author

**Files:**
- Modify: `wjs/plugins/wjs_review/metadata_export/mappers.py:159-190` (`map_affiliation`), `:220` (`map_author`'s call to it)
- Modify: `wjs/plugins/wjs_review/tests/test_metadata_export.py:250-296` (three existing `map_affiliation` tests)

**Interfaces:**
- Consumes: `map_author(frozen_author, article, seq=None)` from Task 2 — this task edits the body of `map_author`, not its signature.
- Produces: `map_affiliation(frozen_author, article) -> AffiliationExportDTO` — **signature change**, `article` is now a required second parameter (was: `map_affiliation(frozen_author)`). Task 5 consumes this exact new signature.

- [ ] **Step 1: Write the failing tests**

In `wjs/plugins/wjs_review/tests/test_metadata_export.py`, update the three existing direct calls to pass `article` too. Change:
```python
    result = mappers.map_affiliation(frozen_author)

    assert result.institution == "Primary Org", "the is_primary=True affiliation must win over others"
```
(in `test_map_affiliation_prefers_the_primary_one`) to:
```python
    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Primary Org", "the is_primary=True affiliation must win over others"
```
Change (in `test_map_affiliation_falls_back_to_first_when_no_primary`):
```python
    result = mappers.map_affiliation(frozen_author)

    assert result.institution == "Only Org", "with no is_primary=True row, the first one must be used"
```
to:
```python
    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Only Org", "with no is_primary=True row, the first one must be used"
```
Change (in `test_map_affiliation_with_no_controlled_affiliation_is_empty`):
```python
    assert (
        mappers.map_affiliation(frozen_author) == AffiliationExportDTO()
    ), "an author with no ControlledAffiliation at all must map to an all-empty affiliation"
```
to:
```python
    assert (
        mappers.map_affiliation(frozen_author, article) == AffiliationExportDTO()
    ), "an author with no ControlledAffiliation at all must map to an all-empty affiliation"
```

Then add these four new tests right after `test_map_affiliation_with_no_controlled_affiliation_is_empty`:
```python
@pytest.mark.django_db
def test_map_affiliation_uses_submission_data_affiliation_for_ta_corresponding_author(article, author, country):
    """IOP feedback: for a TA article, the corresponding author's exported institution is the TA-eligible one."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=2)
    article.correspondence_author = author
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    ta_org = _make_organization_with_location("TA-Eligible Org", "Trieste", country)
    ta_affiliation = ControlledAffiliation.objects.create(organization=ta_org)
    access_mode = AccessMode.objects.create(name="TA", code="oa-transformative-agreement")
    article.submission_data.access_mode = access_mode
    article.submission_data.affiliation = ta_affiliation
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "TA-Eligible Org", "must use the TA-eligible institution, not the author's own"


@pytest.mark.django_db
def test_map_affiliation_ignores_submission_data_affiliation_for_non_corresponding_author(article, author, country):
    """The TA override only applies to the corresponding author -- everyone else keeps their own affiliation."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    # No article.correspondence_author set -- this author is NOT corresponding.
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    ta_org = _make_organization_with_location("TA-Eligible Org", "Trieste", country)
    ta_affiliation = ControlledAffiliation.objects.create(organization=ta_org)
    access_mode = AccessMode.objects.create(name="TA", code="oa-transformative-agreement")
    article.submission_data.access_mode = access_mode
    article.submission_data.affiliation = ta_affiliation
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Author's Own Org"


@pytest.mark.django_db
def test_map_affiliation_ignores_submission_data_affiliation_when_not_ta(article, author, country):
    """A plain (non-TA) OA-agreed article does not trigger the TA institution override."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    article.correspondence_author = author
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    ta_org = _make_organization_with_location("TA-Eligible Org", "Trieste", country)
    ta_affiliation = ControlledAffiliation.objects.create(organization=ta_org)
    access_mode = AccessMode.objects.create(name="Open Access", code="open-access")
    article.submission_data.access_mode = access_mode
    article.submission_data.affiliation = ta_affiliation
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Author's Own Org", "only the oa-transformative-agreement code triggers the override"


@pytest.mark.django_db
def test_map_affiliation_falls_back_to_own_when_submission_data_affiliation_unset(article, author, country):
    """A TA article whose submission_data.affiliation is unset falls back to the author's own affiliation."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    article.correspondence_author = author
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    access_mode = AccessMode.objects.create(name="TA", code="oa-transformative-agreement")
    article.submission_data.access_mode = access_mode
    # article.submission_data.affiliation deliberately left unset (None).
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Author's Own Org"
```

- [ ] **Step 2: Run the tests to verify they fail**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "map_affiliation" -v`

Expected: the three updated tests FAIL with a `TypeError: map_affiliation() takes 1 positional argument but 2 were given` (signature not changed yet); the four new tests FAIL the same way.

- [ ] **Step 3: Write the minimal implementation**

In `wjs/plugins/wjs_review/metadata_export/mappers.py`, change:
```python
def map_affiliation(frozen_author) -> AffiliationExportDTO:
    """
    Map a ``FrozenAuthor``'s primary (or first) ``ControlledAffiliation`` to the export shape.

    Multiple affiliations are out of scope for this first pass -- only the primary/first one is
    exported, per export_spec.md. Confirmed as intentional (not a gap): this is each individual
    author's own affiliation, not a single article-level/paper affiliation.
    """
    controlled_affiliation = (
        frozen_author.controlledaffiliation_set.filter(is_primary=True).first()
        or frozen_author.controlledaffiliation_set.first()
    )
    if controlled_affiliation is None:
        return AffiliationExportDTO()

    organization = controlled_affiliation.organization
    institution = str(organization) if organization else ""
    city = ""
    country = ""
    if organization is not None:
        location = organization.location
        if location is not None:
            city = location.name or ""
            country = str(location.country) if location.country else ""

    return AffiliationExportDTO(
        institution=institution,
        department=controlled_affiliation.department or "",
        person_title=controlled_affiliation.title or "",
        city=city,
        country=country,
    )
```
to:
```python
def _affiliation_dto_from_controlled_affiliation(controlled_affiliation) -> AffiliationExportDTO:
    """Build an ``AffiliationExportDTO`` from a single ``ControlledAffiliation`` row (empty if ``None``)."""
    if controlled_affiliation is None:
        return AffiliationExportDTO()

    organization = controlled_affiliation.organization
    institution = str(organization) if organization else ""
    city = ""
    country = ""
    if organization is not None:
        location = organization.location
        if location is not None:
            city = location.name or ""
            country = str(location.country) if location.country else ""

    return AffiliationExportDTO(
        institution=institution,
        department=controlled_affiliation.department or "",
        person_title=controlled_affiliation.title or "",
        city=city,
        country=country,
    )


def _is_ta_agreed(article) -> bool:
    """Return whether the article's access mode is specifically the transformative-agreement one."""
    submission_data = getattr(article, "submission_data", None)
    if submission_data is None or submission_data.access_mode is None:
        return False
    return submission_data.access_mode.code == "oa-transformative-agreement"


def map_affiliation(frozen_author, article) -> AffiliationExportDTO:
    """
    Map a ``FrozenAuthor``'s primary (or first) ``ControlledAffiliation`` to the export shape.

    Multiple affiliations are out of scope for this first pass -- only the primary/first one is
    exported, per export_spec.md. Confirmed as intentional (not a gap): this is each individual
    author's own affiliation, not a single article-level/paper affiliation.

    Re-settled (IOP feedback): for the **corresponding author** of a **transformative-agreement**
    article (``article.submission_data.access_mode.code == "oa-transformative-agreement"``), when
    ``article.submission_data.affiliation`` is set, that ``ControlledAffiliation`` -- the one
    actually eligible for the TA -- is used instead of the per-author lookup below. It need not be
    whichever affiliation happens to be marked primary on the corresponding author's own record.
    Every other author, and every non-TA article, uses the per-author lookup unchanged.
    """
    is_corresponding = bool(
        frozen_author.author_id
        and article.correspondence_author_id
        and frozen_author.author_id == article.correspondence_author_id,
    )
    if is_corresponding and _is_ta_agreed(article):
        ta_affiliation = article.submission_data.affiliation
        if ta_affiliation is not None:
            return _affiliation_dto_from_controlled_affiliation(ta_affiliation)

    controlled_affiliation = (
        frozen_author.controlledaffiliation_set.filter(is_primary=True).first()
        or frozen_author.controlledaffiliation_set.first()
    )
    return _affiliation_dto_from_controlled_affiliation(controlled_affiliation)
```

Then, in `map_author` (touched by Task 2 — the `seq` parameter must still be there), change the one line:
```python
        affiliation=map_affiliation(frozen_author),
```
to:
```python
        affiliation=map_affiliation(frozen_author, article),
```

- [ ] **Step 4: Run the tests to verify they pass**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "map_affiliation or map_author" -v`

Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto
pre-commit run --files wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "fix(metadata-export): TA articles report the TA-eligible institution for the corresponding author"
```

---

### Task 5: Corresponding-author affiliation completeness — EO warning

**Files:**
- Modify: `wjs/plugins/wjs_review/metadata_export/mappers.py` (new function, after `map_authors`)
- Modify: `wjs/plugins/wjs_review/logic__production.py:81-82` (import), `:159-220` (`SendProductionXMLToPublisher`)
- Test: `wjs/plugins/wjs_review/tests/test_metadata_export.py` (new mapper-level tests)
- Test: `wjs/plugins/wjs_review/tests/test_production.py` (new integration test)

**Interfaces:**
- Consumes: `map_affiliation(frozen_author, article)` from Task 4 (exact signature, including the TA override — so this check reflects the *final* exported affiliation, not just the author's own raw data).
- Produces: `mappers.corresponding_author_affiliation_is_incomplete(article) -> bool`. `SendProductionXMLToPublisher` calls it; no other task depends on it.

- [ ] **Step 1: Write the failing mapper-level tests**

In `wjs/plugins/wjs_review/tests/test_metadata_export.py`, add these tests after the Task 4 tests (still in the `map_affiliation`/author section):
```python
@pytest.mark.django_db
def test_corresponding_author_affiliation_is_incomplete_when_no_correspondence_author(article):
    article.frozenauthor_set.all().delete()
    FrozenAuthor.objects.create(article=article, order=1, first_name="Solo")
    # article.correspondence_author deliberately left unset.

    assert mappers.corresponding_author_affiliation_is_incomplete(article) is True


@pytest.mark.django_db
def test_corresponding_author_affiliation_is_incomplete_with_bare_controlled_affiliation_row(article, author):
    """A ControlledAffiliation row can exist with no organization at all -- organization is nullable."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=2, first_name="Corr")
    article.correspondence_author = author
    ControlledAffiliation.objects.create(frozen_author=frozen_author, is_primary=True)  # no organization

    assert mappers.corresponding_author_affiliation_is_incomplete(article) is True


@pytest.mark.django_db
def test_corresponding_author_affiliation_is_complete_when_inst_city_country_all_present(article, author, country):
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    article.correspondence_author = author
    org = _make_organization_with_location("Complete Org", "Trieste", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=org, is_primary=True)

    assert mappers.corresponding_author_affiliation_is_incomplete(article) is False
```

Then, in `wjs/plugins/wjs_review/tests/test_production.py`, add this integration test right after `test_send_production_xml_to_publisher_sends_zip_and_logs_message` (`mock`, `Message`, `get_eo_user`, `Article`, `FrozenAuthor`, `settings` are all already imported at module level in this file — no new imports needed):
```python
@pytest.mark.django_db
def test_send_production_xml_to_publisher_logs_warning_when_corresponding_author_affiliation_incomplete(
    accepted_article: Article,
    settings,
):
    """IOP feedback: an incomplete corresponding-author affiliation must produce a second EO message."""
    settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS = {
        accepted_article.journal.code: "plugins.wjs_review.metadata_export.publishers.send_zip_to_iop",
    }
    accepted_article.frozenauthor_set.all().delete()
    FrozenAuthor.objects.create(article=accepted_article, order=1, first_name="Solo")
    # No correspondence_author set -- guarantees an incomplete affiliation.
    workflow = accepted_article.articleworkflow

    with (
        mock.patch("plugins.wjs_review.metadata_export.publishers.send_zip_to_iop"),
        mock.patch("plugins.wjs_review.logic__production.communication_utils.log_operation") as mock_log,
    ):
        SendProductionXMLToPublisher(articleworkflow=workflow).run()

    assert mock_log.call_count == 2, "one 'export prepared' message plus one incomplete-affiliation warning"
    warning_call = mock_log.call_args_list[1]
    assert warning_call.kwargs["article"] == accepted_article
    assert warning_call.kwargs["actor"] is None
    assert warning_call.kwargs["recipients"] == [get_eo_user(accepted_article)]
    assert warning_call.kwargs["verbosity"] == Message.MessageVerbosity.FULL
```

- [ ] **Step 2: Run the tests to verify they fail**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "corresponding_author_affiliation_is" -v`

Expected: FAIL with `AttributeError: module 'plugins.wjs_review.metadata_export.mappers' has no attribute 'corresponding_author_affiliation_is_incomplete'`.

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_production.py -k "logs_warning_when_corresponding_author" -v`

Expected: FAIL with `assert 1 == 2` (only the "export prepared" message is logged today).

- [ ] **Step 3: Write the minimal implementation**

In `wjs/plugins/wjs_review/metadata_export/mappers.py`, add this function right after `map_authors` (i.e. after the function Task 2 last modified):
```python
def corresponding_author_affiliation_is_incomplete(article) -> bool:
    """
    Return whether the corresponding author's exported affiliation is missing inst/city/country.

    Settled (IOP feedback): IOP requires these three fields non-empty for the corresponding
    author specifically -- an empty value means their team has to populate it manually from the
    reference PDF. ``ControlledAffiliation.organization`` is nullable (and even when set,
    ``Organization.location`` can itself be unset), so this isn't guaranteed by the data model;
    this check exists so a gap surfaces as an EO message instead of an incomplete export.
    """
    frozen_author = article.frozenauthor_set.filter(author_id=article.correspondence_author_id).first()
    if frozen_author is None:
        return True
    affiliation = map_affiliation(frozen_author, article)
    return not (affiliation.institution and affiliation.city and affiliation.country)
```

In `wjs/plugins/wjs_review/logic__production.py`, change the import:
```python
from .metadata_export.service import build_production_export_zip
```
to:
```python
from .metadata_export.mappers import corresponding_author_affiliation_is_incomplete
from .metadata_export.service import build_production_export_zip
```

Then in the `SendProductionXMLToPublisher` class, add a new method right after `_log_operation` (before `run`):
```python
    def _log_incomplete_affiliation_warning(self):
        """Log a second, separate EO message when the corresponding author's affiliation is incomplete."""
        article = self.articleworkflow.article
        message_subject = f"Corresponding author affiliation incomplete - article {article.pk}"
        message_body = (
            f"The corresponding author's affiliation for {self.articleworkflow} is missing "
            "institution, city, or country. IOP requires all three for the corresponding author. "
            "Please check and complete the author's affiliation in Janeway."
        )
        communication_utils.log_operation(
            article=article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[get_eo_user(article)],
            verbosity=Message.MessageVerbosity.FULL,
        )
```

Then change `run()` from:
```python
    def run(self) -> ArticleWorkflow:
        """Send the article's production export zip to the publisher, if the journal is configured for it."""
        send_function = self._get_send_function()
        if send_function is None:
            return self.articleworkflow
        # NOTE: this holds the transaction open across build_production_export_zip() (which can shell
        # out to pdfinfo) and the send call. Once send_zip_to_iop grows real network transport, move the
        # send (or the whole block) outside this transaction / into an async_task, rather than holding a DB
        # transaction across a network round-trip.
        with transaction.atomic():
            zip_bytes = build_production_export_zip(self.articleworkflow.article)
            send_function(self.articleworkflow.article, zip_bytes)
            self._log_operation()
        return self.articleworkflow
```
to:
```python
    def run(self) -> ArticleWorkflow:
        """Send the article's production export zip to the publisher, if the journal is configured for it."""
        send_function = self._get_send_function()
        if send_function is None:
            return self.articleworkflow
        # NOTE: this holds the transaction open across build_production_export_zip() (which can shell
        # out to pdfinfo) and the send call. Once send_zip_to_iop grows real network transport, move the
        # send (or the whole block) outside this transaction / into an async_task, rather than holding a DB
        # transaction across a network round-trip.
        with transaction.atomic():
            zip_bytes = build_production_export_zip(self.articleworkflow.article)
            send_function(self.articleworkflow.article, zip_bytes)
            self._log_operation()
            if corresponding_author_affiliation_is_incomplete(self.articleworkflow.article):
                self._log_incomplete_affiliation_warning()
        return self.articleworkflow
```

- [ ] **Step 4: Run the tests to verify they pass**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "corresponding_author_affiliation_is" -v`

Expected: PASS (3/3).

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_production.py -k "test_send_production_xml_to_publisher" -v`

Expected: PASS (3/3 — the two pre-existing tests plus the new one). The pre-existing `test_send_production_xml_to_publisher_sends_zip_and_logs_message` test must still pass with exactly one `log_operation` call for its own assertions to hold — confirm its `accepted_article` fixture (after `accepted_article.manuscript_files.clear()`, already in that test) still has a complete affiliation for whichever author ends up corresponding; if it doesn't, that test's `mock_log.assert_called_once_with(...)` will now see 2 calls and fail. If so, either add a real affiliation to that test's fixture setup or relax that assertion to check only the first call — use your judgement on which reads better, but the test must still prove the "export prepared" message's content.

- [ ] **Step 5: Full regression run**

`cd /home/yakky/Projects/projects/sissa-1.8/janeway/src && /home/yakky/.pyenv/versions/janeway-upstream/bin/python -m pytest --reuse-db -n4 ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_production.py ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_workflow.py ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_export_jcap_xml_command.py -q`

Expected: all pass, 0 failed, 0 errors (parallel mode — see Global Constraints on why sequential multi-file runs in this suite aren't a reliable signal).

- [ ] **Step 6: Lint and commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto
pre-commit run --files wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_metadata_export.py wjs/plugins/wjs_review/tests/test_production.py
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_metadata_export.py wjs/plugins/wjs_review/tests/test_production.py
git commit -m "feat(wjs_review): warn EO when the corresponding author's affiliation is incomplete"
```
