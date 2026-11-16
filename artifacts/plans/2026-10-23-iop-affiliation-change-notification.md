# IoP affiliation-change notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When "Submit revision" / "Update metadata" changes the paper's affiliation on an IoP journal, send the
EO an unread system message (timeline + email).

**Architecture:** A new business-logic dataclass `AffiliationChangedInRevisionNotification` in
`wjs_review/logic.py` renders two new `wjs_review` settings and calls `communication_utils.log_operation`.
`PopulateRevisionStep4.run()` captures the old affiliation / corresponding author before overwriting them and
invokes the notification when the trigger holds.

**Tech Stack:** Django, Janeway settings (`create_customization_setting`, `render_template_from_setting`),
pytest (run from `janeway/src`).

**Spec:** `artifacts/specs/2026-10-23-iop-affiliation-change-notification-design.md`

## Global Constraints

- Only journals whose code is in `plugins.wjs_submission.settings.IOP_JOURNALS` (default `["JCAP"]`).
- Compare by `ControlledAffiliation` pk; corresponding-author change counts; old `None` → new value counts.
- No message when `revision_storage.data["affiliation_pk"]` is falsy.
- Recipients `[get_eo_user(article)]`, verbosity FULL (default), `flag_as_read_by_eo=False` (default),
  `actor=None` (system).
- Setting names: `affiliation_changed_in_revision_subject` (text), `affiliation_changed_in_revision_body`
  (rich-text), `is_translatable: False`, group `wjs_review`. Default values verbatim from the spec.
- Rendered subject must stay under 111 chars (`Message.subject` max_length).
- Code style: black/isort/flake8 at 119, double quotes, docstrings imperative; `pre-commit run --all-files`.
- Tests run from `/Users/sfedoruk/Projects/sissa/janeway/src`:
  `pytest --reuse-db ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_logic.py -k <selector>`
  (see memory/`.claude/rules/tests.md`; use `--create-db` once if the new settings are not picked up).

## Review Focus

1. Author re-saves step 4 without touching the affiliation (storage still holds the original pk) → no message
   (the #3083-style anti-goal). Pinned in Task 2 (`unchanged` case).
2. New corresponding author has no affiliations, so `affiliation_pk` is `None` → article keeps old affiliation,
   no message. Pinned in Task 2 (`missing` case).
3. Old affiliation `None` (imported papers) → body renders "none" for "Previous", no template error. Pinned in
   Task 1 (`old_affiliation=None` test) and Task 2.
4. Non-IoP journal with a changed affiliation → affiliation still updated, no message. Pinned in Task 2.
5. Corresponding author changes in the same revision → "Previous" shows the *old* author's name, captured
   before `run()` overwrites `correspondence_author_id`. Pinned in Task 2 (`author_changed` case asserts old
   name in body).

---

### Task 1: Settings + notification class

**Files:**
- Modify: `wjs/plugins/wjs_review/plugin_settings.py` (new nested function next to
  `add_access_mode_request_notifications()` ~line 2270; register it in the `csv_writer.write_settings(...)`
  list ~line 2386)
- Modify: `wjs/plugins/wjs_review/logic.py` (new dataclass right after `AccessModeSpecialRequestNotification`
  ~line 5322)
- Test: `wjs/plugins/wjs_review/tests/test_logic.py`

**Interfaces:**
- Produces:
  ```python
  @dataclasses.dataclass
  class AffiliationChangedInRevisionNotification:
      article: Article
      old_affiliation: ControlledAffiliation | None
      old_correspondence_author: Account | None
      def run(self) -> Message: ...
  ```
  Context passed to both templates: `article`, `old_affiliation`, `new_affiliation`
  (= `article.submission_data.affiliation`), `old_correspondence_author`. Rendering mirrors
  `AccessModeSpecialRequestNotification._send_notification` (fake request,
  `render_template_from_setting(setting_group_name="wjs_review", ..., template_is_setting=True)`); `run()` wraps
  in `transaction.atomic()` and returns the `Message` from `log_operation`.
- Settings function in `plugin_settings.py`: `add_affiliation_changed_in_revision_notifications()` returning
  `(setting_1, setting_2)`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("has_old_affiliation", (True, False))
@pytest.mark.django_db
def test_affiliation_changed_in_revision_notification(editor_revision, review_settings, has_old_affiliation):
    """The notification sends an unread system message to EO with old and new affiliation."""
    article = editor_revision.article
    old_author = article.correspondence_author
    old_aff = ControlledAffiliation.objects.create(account=old_author, department="Old Dept") if has_old_affiliation else None
    new_aff = ControlledAffiliation.objects.create(account=old_author, department="New Dept")
    article.submission_data.affiliation = new_aff
    article.submission_data.save()
    Message.objects.all().delete()

    AffiliationChangedInRevisionNotification(
        article=article, old_affiliation=old_aff, old_correspondence_author=old_author
    ).run()

    message = Message.objects.get()
    assert "Corresponding author affiliation changed in revision" in message.subject, message.subject
    assert f"{article.journal.code}-{article.pk}" in message.subject
    assert len(message.subject) < 111
    assert "New Dept" in message.body
    assert ("Old Dept" if has_old_affiliation else "Previous: none") in message.body
    assert list(message.recipients.all()) == [get_eo_user(article)]
    assert not message.read_by_eo, "EO must see the message as unread"
    assert message.verbosity == Message.MessageVerbosity.FULL
    assert message.actor != old_author, "message must come from the system, not the author"
```

(Adjust the actor assertion to whatever `log_operation(actor=None)` stores — the system user; check
`communication_utils.log_operation`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest --reuse-db ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_logic.py -k test_affiliation_changed_in_revision_notification`
Expected: FAIL with ImportError / NameError on `AffiliationChangedInRevisionNotification`.

- [ ] **Step 3: Add the two settings** with the spec's default subject/body verbatim; register in the
  `write_settings` list.

- [ ] **Step 4: Implement `AffiliationChangedInRevisionNotification`** per Interfaces.

- [ ] **Step 5: Run test to verify it passes** (same command). Expected: 2 passed.

- [ ] **Step 6: Commit** (only if per-change commits were chosen)

```bash
git add wjs/plugins/wjs_review/plugin_settings.py wjs/plugins/wjs_review/logic.py wjs/plugins/wjs_review/tests/test_logic.py
git commit -m "feat(wjs_review): add EO notification for affiliation change in revision"
```

### Task 2: Trigger from `PopulateRevisionStep4`

**Files:**
- Modify: `wjs/plugins/wjs_review/logic.py` — `PopulateRevisionStep4.run()` (~line 1919); import
  `from plugins.wjs_submission import settings as wjs_submission_settings` at module level (check for circular
  import; fall back to a local import with a comment only if needed).
- Test: `wjs/plugins/wjs_review/tests/test_logic.py`

**Interfaces:**
- Consumes: `AffiliationChangedInRevisionNotification(article, old_affiliation, old_correspondence_author).run()`
  from Task 1.
- Produces: private helper on `PopulateRevisionStep4`:
  `_affiliation_changed(self, old_affiliation_id: int | None) -> bool` — True iff journal code in
  `wjs_submission_settings.IOP_JOURNALS`, storage `affiliation_pk` truthy, and
  `int(affiliation_pk) != old_affiliation_id`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize(
    ("case", "is_iop", "expect_message"),
    [
        ("same_author_new_affiliation", True, True),
        ("author_changed", True, True),
        ("old_none", True, True),
        ("unchanged", True, False),
        ("missing", True, False),
        ("same_author_new_affiliation", False, False),
    ],
)
@pytest.mark.django_db
def test_populate_revision_step4_affiliation_change_notification(
    editor_revision, review_settings, monkeypatch, case, is_iop, expect_message
):
    """PopulateRevisionStep4 notifies EO only when an IoP paper's affiliation actually changes."""
```

Setup per case (old author `A` = `article.correspondence_author`, other author `B` = a co-author from
`article.authors` or a new account; affiliations created with `ControlledAffiliation.objects.create(account=...)`):
- `same_author_new_affiliation`: article aff `A1`; storage `{"correspondence_author": A.pk, "affiliation_pk": A2.pk}`.
- `author_changed`: article aff `A1`; storage `{"correspondence_author": B.pk, "affiliation_pk": B1.pk}`.
- `old_none`: article aff `None`; storage `{"affiliation_pk": A1.pk}`.
- `unchanged`: article aff `A1`; storage `{"affiliation_pk": A1.pk}`.
- `missing`: article aff `A1`; storage `{"affiliation_pk": None}`.
- `monkeypatch.setattr(wjs_submission_settings, "IOP_JOURNALS", [article.journal.code] if is_iop else [])`.
- `RevisionStorage.objects.create(article=..., revision_flow_type=RevisionStorage.RevisionFlowType.FULL, data=...)`,
  `Message.objects.all().delete()`, run `PopulateRevisionStep4(article=..., revision=editor_revision, revision_storage=...).run()`,
  then `article.submission_data.save()` / `refresh_from_db()` as in `test_populate_revision_step7_special_request`.

Assertions:
- affiliation on article == storage value when truthy, else unchanged (`missing` keeps `A1`; non-IoP still updated).
- `Message.objects.count() == (1 if expect_message else 0)`.
- when a message is expected: recipients `[get_eo_user(article)]`, `not message.read_by_eo`; for `author_changed`
  the body contains `A.full_name()` (old) and `B.full_name()` (new).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest --reuse-db ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_logic.py -k test_populate_revision_step4_affiliation_change_notification`
Expected: the three `expect_message=True` cases FAIL (0 messages); others pass.

- [ ] **Step 3: Implement in `PopulateRevisionStep4.run()`**: at the very top capture
  `old_affiliation = self.article.submission_data.affiliation` and
  `old_correspondence_author = self.article.correspondence_author`; compute `changed = self._affiliation_changed(old_affiliation.pk if old_affiliation else None)`
  before the existing assignments; after the affiliation assignment, `if changed:` run the notification.
  Note: the template reads `article.correspondence_author` — after setting `correspondence_author_id`, make
  sure the cached relation is not stale (re-fetch / let Django reload; verify via the `author_changed` assertion).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest --reuse-db ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_logic.py -k "affiliation_change or populate_revision"`
Expected: all pass (including the existing `test_populate_revision_step7_special_request`).

- [ ] **Step 5: Regression run of revision flows**

Run: `pytest --reuse-db -n4 ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_logic.py -k "revision"`
Expected: all pass.

- [ ] **Step 6: Lint** — `pre-commit run --all-files` in `wjs-profile-project`. Expected: all hooks pass.

- [ ] **Step 7: Commit** (only if per-change commits were chosen)

```bash
git add wjs/plugins/wjs_review/logic.py wjs/plugins/wjs_review/tests/test_logic.py
git commit -m "feat(wjs_review): notify EO when IoP paper affiliation changes in revision"
```
