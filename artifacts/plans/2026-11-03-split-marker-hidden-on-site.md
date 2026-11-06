# Hide the `[...]` split marker on the site — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Messages shown on the site no longer display the email split marker `[...]` of `SYSTEM` messages and
reminders; the email teaser no longer crashes on bodies with several markers.

**Architecture:** One static helper on `Message` removes the marker; a `display_body` property on `Message`
(SYSTEM only) and on `Reminder` (always) exposes the cleaned text; the five site templates that print a stored body
switch to `display_body`. `render_message` splits at the first marker only. Stored data is untouched.

**Tech Stack:** Django templates, pytest + pytest-django.

**Spec:** `artifacts/specs/2026-11-03-split-marker-hidden-on-site-design.md`

## Global Constraints

- All code under `wjs/plugins/wjs_review/` in wjs-profile-project.
- Tests run from `janeway/src`: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/<file>.py -k <name>`
  (never the whole suite: 10+ minutes).
- Marker string is `Message.SPLIT_MARKER` (`"[...]"`); never hardcode `"[...]"` in code or templates.
- Only `Message.MessageTypes.SYSTEM` messages are stripped; `USER`, `NOTE`, `HIJACK` are shown verbatim.
- Email teaser = text before the **first** marker + `_render_read_more(recipient)`, as today.
- No changes to settings, reminder templates content, migrations or stored data.
- Assertions carry a message (`assert x, "why"`), per `.claude/rules/tests.md`.

## Review Focus

1. A `SYSTEM` body with the marker repeated (two settings concatenated, or a copy-paste) → site shows all text, no
   marker; email teaser stops at the first one, no `ValueError`. Covered in Task 1.
2. A `USER` message where a person typed `[...]` to mark an omission in a quote → kept on the site. Covered in
   Task 1 (property) and Task 3 (view).
3. Marker glued to HTML (`as possible [...].</p>`, `[...]<br>`) → removed without breaking the surrounding markup.
   Covered in Task 1 (test input uses markup around the marker).
4. Empty body / body that is only the marker → `display_body` returns `""` without error. Covered in Task 1.
5. Messages already stored with the marker (e.g. local message 3619587, JCOM workflow 110954) → fixed with no data
   change; check manually at
   `http://jcom.local:8000/plugins/wjs-review-articles/messages/110954/#message-item-3619587` after Task 3.

---

### Task 1: `Message` marker helper, `display_body`, first-marker email split

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py` (`Message`, near `SPLIT_MARKER` at line 1612 and `render_message` at
  lines 1846-1851)
- Test: `wjs/plugins/wjs_review/tests/test_communications.py`

**Interfaces:**
- Produces: `Message.strip_split_marker(text: str) -> str` (`@staticmethod`, returns `text` with every
  `SPLIT_MARKER` occurrence removed); `Message.display_body -> str` (`@property`: `strip_split_marker(self.body)`
  when `message_type == SYSTEM`, else `self.body`).

- [ ] **Step 1: Write the failing tests** in `test_communications.py`:

```python
@pytest.mark.parametrize(
    "body,expected",
    (
        ("<p>Thank you for submitting [...] the paper.</p>", "<p>Thank you for submitting  the paper.</p>"),
        ("<p>as soon as possible [...].</p><p>more [...]<br>end</p>", "<p>as soon as possible .</p><p>more <br>end</p>"),
        ("<p>No marker</p>", "<p>No marker</p>"),
        ("[...]", ""),
        ("", ""),
    ),
)
def test_strip_split_marker(body, expected):
    assert Message.strip_split_marker(body) == expected, "every split marker must be removed, nothing else"


@pytest.mark.parametrize(
    "message_type,marker_kept",
    (
        (Message.MessageTypes.SYSTEM, False),
        (Message.MessageTypes.USER, True),
        (Message.MessageTypes.NOTE, True),
        (Message.MessageTypes.HIJACK, True),
    ),
)
def test_message_display_body_strips_marker_only_for_system(message_type, marker_kept):
    msg = Message(body=f"<p>before</p>{Message.SPLIT_MARKER}<p>after</p>", message_type=message_type)
    assert "<p>before</p>" in msg.display_body and "<p>after</p>" in msg.display_body, "full text is shown"
    assert (Message.SPLIT_MARKER in msg.display_body) is marker_kept, "marker stripped only for SYSTEM messages"
```

And a DB test modelled on `test_emit_message_email_reduced`, `test_emit_message_email_with_several_markers`:
body `f"{msg1}{Message.SPLIT_MARKER}{msg2}{Message.SPLIT_MARKER}{msg3}"` (three `<p>` paragraphs), default
(`SYSTEM`) type, one recipient, `msg.emit_notification()`; assert one email sent, `msg1` and `"read more"` in the
HTML alternative, `msg2` and `msg3` and `Message.SPLIT_MARKER` not in it.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_communications.py -k "split_marker or display_body or several_markers"`
Expected: FAIL — `AttributeError` on `strip_split_marker` / `display_body`; `ValueError: too many values to unpack`
for the several-markers test.

- [ ] **Step 3: Implement** `strip_split_marker` and `display_body` on `Message`; in `render_message` split at the
  first marker only (`str.partition` or `split(self.SPLIT_MARKER, 1)`), rest of the method unchanged. Docstrings:
  say the marker is the email teaser cut point and the site shows the full text.

- [ ] **Step 4: Run Task 1 tests plus the existing email tests**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_communications.py -k "split_marker or display_body or several_markers or emit_message"`
Expected: PASS (including the unchanged `test_emit_message_email_reduced`).

- [ ] **Step 5: Commit** (only if per-change commits were chosen in flow step 4)

```bash
git add wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/tests/test_communications.py
git commit -m "fix(wjs_review): add Message.display_body and split email teaser at the first marker"
```

### Task 2: `Reminder.display_body`

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py` (`Reminder`, class at line 2359)
- Test: `wjs/plugins/wjs_review/tests/test_reminders.py`

**Interfaces:**
- Consumes: `Message.strip_split_marker(text: str) -> str` (Task 1).
- Produces: `Reminder.display_body -> str` (`@property`, `Message.strip_split_marker(self.message_body)`;
  reminders always become `SYSTEM` messages, see `Reminder.create_message`).

- [ ] **Step 1: Write the failing test** `test_reminder_display_body_strips_split_marker` in `test_reminders.py`
  (no DB needed, unsaved instance):

```python
def test_reminder_display_body_strips_split_marker():
    reminder = Reminder(message_body=f"<p>due date expires today. {Message.SPLIT_MARKER}</p><p>Please</p>")
    assert Message.SPLIT_MARKER not in reminder.display_body, "reminder preview must hide the split marker"
    assert "<p>Please</p>" in reminder.display_body, "full reminder text is shown"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_reminders.py -k display_body`
Expected: FAIL with `AttributeError: 'Reminder' object has no attribute 'display_body'`.

- [ ] **Step 3: Implement** `Reminder.display_body` as in Interfaces.

- [ ] **Step 4: Run it to verify it passes** (same command). Expected: PASS.

- [ ] **Step 5: Commit** (only with per-change commits)

```bash
git add wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/tests/test_reminders.py
git commit -m "fix(wjs_review): add Reminder.display_body hiding the split marker"
```

### Task 3: Site templates use `display_body`

**Files:**
- Modify (replace the raw body with `display_body`, keep `|safe` and every other filter/argument as is):
  - `wjs/plugins/wjs_review/templates/wjs_review/article_messages/elements/message.html:20` — `message.body`
  - `wjs/plugins/wjs_review/templates/wjs_review/messages_overview/elements/message.html:60` — `message.body`
  - `wjs/plugins/wjs_review/templates/wjs_review/write_message/write_messages.html:126` — `source_message.body`
  - `wjs/plugins/wjs_review/templates/wjs_review/evaluate_review/review_evaluate.html:71` —
    `assignment.workflowreviewassignment.editor_invite_message.body`
  - `wjs/plugins/wjs_review/templates/wjs_review/reminders/elements/reminder.html:25` — `reminder.message_body`
- Test: `wjs/plugins/wjs_review/tests/test_views_base.py`

**Interfaces:**
- Consumes: `Message.display_body`, `Reminder.display_body` (Tasks 1-2).

- [ ] **Step 1: Write the failing view test** `test_wjs_article_messages_hides_split_marker`, next to
  `test_wjs_article_messages` (same `assigned_article`, `client` fixtures and URL). Create two messages on the
  article with the correspondence author as recipient: a `SYSTEM` one with body
  `f"<p>Thank you for submitting {Message.SPLIT_MARKER} the paper.</p>"` and a `USER` one with body
  `f"<p>quoting {Message.SPLIT_MARKER} omitted</p>"`. Log in as the correspondence author, GET the page; assert
  status 200, `"Thank you for submitting"` and `"the paper."` in the content, `f"submitting {Message.SPLIT_MARKER}"`
  not in the content, `f"quoting {Message.SPLIT_MARKER} omitted"` in the content.

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_views_base.py -k wjs_article_messages`
Expected: FAIL on the `submitting [...]` assertion.

- [ ] **Step 3: Edit the five templates** as listed in Files. Then grep the plugin templates for any other
  `message.body`, `source_message.body`, `.message_body` rendered to users; notes in
  `lists/elements/*/table_item.html` and `lists/elements/issue/*` are `NOTE` messages and may stay as they are.

- [ ] **Step 4: Run the view tests and the template-touching tests**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_views_base.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_communications.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_reminders.py -n7`
Expected: PASS.

- [ ] **Step 5: Manual check** (Review Focus 5): open
  `http://jcom.local:8000/plugins/wjs-review-articles/messages/110954/#message-item-3619587` on the local
  runserver; the text reads "Thank you for submitting the Addendum …" with no `[...]`.

- [ ] **Step 6: Commit** (only with per-change commits)

```bash
git add wjs/plugins/wjs_review/templates wjs/plugins/wjs_review/tests/test_views_base.py
git commit -m "fix(wjs_review): hide the split marker in messages shown on the site"
```
