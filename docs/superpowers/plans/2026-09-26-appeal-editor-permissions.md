# Appeal Editor Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each kind of editor (assigned, appeal, past, removed-for-appeal) see only the article
information the functional spec allows, as described in wjs/specs#2903.

**Architecture:** A new `PastEditorAssignment.on_appeal` flag records editors removed by an appeal. A
plain function `permissions.get_editor_type(user, article)` classifies the user. `get_review_versions`,
`EditorPermissionChecker` and two boolean template filters consume it. "Appeal granted" becomes a
system message, with a data migration for past appeals, and withdrawal after a rejection gets its own
prefill settings.

**Tech Stack:** Django (Janeway plugin `wjs_review` in `wjs-profile-project`), pytest + pytest-django,
django-fsm, Janeway settings (`utils.setting_handler`).

**Spec:** `docs/superpowers/specs/2026-09-26-appeal-editor-permissions-design.md` (read it before starting
any task; the Decisions table D1–D6 explains every choice below).

## Global Constraints

- Work only inside the worktree `/home/yakky/Projects/sissa/wjs-profile-project/.worktrees/issue-2903-appeal-editor-permissions`
  (below: `$WT`), on branch `feature/issue-2903-appeal-editor-permissions`. Never touch the main
  checkout `/home/yakky/Projects/sissa/wjs-profile-project`.
- Run tests **from `/home/yakky/Projects/sissa/janeway/src`**, pointing at the worktree, e.g.
  `pytest ../../wjs-profile-project/.worktrees/issue-2903-appeal-editor-permissions/wjs/plugins/wjs_review/tests/test_editor_type.py -v`.
  Below, `$T` stands for `../../wjs-profile-project/.worktrees/issue-2903-appeal-editor-permissions/wjs/plugins/wjs_review/tests`.
  Add `--create-db` on the first run after any model change (migrations are skipped in tests, the
  schema comes from the models). Never run the full suite (10+ minutes): run the files you touched.
- Lint stack is **black + isort + flake8 + pydocstyle + djlint**, line length 119, not ruff. Pre-commit
  hooks run on `git commit`; if they modify files, `git add` again and re-commit.
- Double quotes; docstrings on all public functions (imperative first line ending with `.`); every
  `assert` in tests has a message (`assert x == y, "why"`); user-facing strings via `gettext_lazy as _`
  (Python) or `{% translate %}` (templates); templates use 2-space indentation, and every
  `{% endblock %}` is named.
- Commit with `git -c commit.gpgsign=false commit`; message in Conventional Commits, English, ending with:
  ```
  Refs: wjs/specs#2903

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HfdgZNn9WqwzvU4GQSaP36
  ```
- Never push. Never commit migrations other than the two this plan creates in `wjs_review/migrations/`.
- Latest `wjs_review` migration on this branch is `0017_blacklisted_authoremail`; this plan adds `0018`
  (schema) and `0019` (data).
- Don't rename `SupervisorChangeEditorAssignment._log_past_editor` or other methods: import scripts
  monkeypatch them.
- Imports of `wjs_review` models inside `permissions.py` functions stay **local** (existing pattern, avoids
  circular imports).

## Review Focus

1. An editor removed and later re-assigned (has both a `PastEditorAssignment` and a current assignment)
   must be treated by their current role (ASSIGNED/APPEAL), never as PAST. Tested in Task 2.
2. An explicit custom `PermissionAssignment` DENY on an item of a past editor's round must still hide it,
   even though the new default grants it. Tested in Task 3.
3. After the author resubmits the appeal, the appeal editor must see the new round normally; only the
   rejected (appealed) round is hidden. Tested in Task 3.
4. A user with several `PastEditorAssignment`s on the same article: "Last submitted" compares against the
   **latest** `date_unassigned`. Tested in Task 4.
5. The data migration must only touch "Appeal granted" messages (subject match, correspondence author
   recipient, article with an OPEN_APPEAL decision), and must skip a journal whose system user can't be
   found instead of crashing. Tested in Task 5.

---

### Task 0: Environment (controller only, not a subagent)

Done by the controlling session before dispatching Task 1; listed so reviewers know the setup.

- [ ] Repoint the Janeway plugin symlinks to the worktree:
  ```bash
  cd /home/yakky/Projects/sissa/janeway/src/plugins
  for p in wjs_home_blocks wjs_latest_articles wjs_latest_news wjs_review wjs_stats wjs_subscribe_newsletter; do
    ln -sfn /home/yakky/Projects/sissa/wjs-profile-project/.worktrees/issue-2903-appeal-editor-permissions/wjs/plugins/$p $p
  done
  ls -la | grep wjs-profile-project
  ```
- [ ] Repoint the editable install:
  `cd $WT && pip install -e . --no-deps`, then check with
  `python -c "import wjs.jcom_profile; print(wjs.jcom_profile.__file__)"`. The path must be under `.worktrees/issue-2903-...`.
- [ ] Baseline: `pytest --create-db $T/test_logic.py::test_open_appeal -v` → PASS.
- [ ] When the whole plan is done, restore both (symlinks → main checkout, `pip install -e . --no-deps`
  from the main checkout).

---

### Task 1: Record editors removed on appeal (`on_appeal`) and appeal fixtures

**Files:**
- Modify: `$WT/wjs/plugins/wjs_review/models.py` (class `PastEditorAssignment`, ~line 1813)
- Create: `$WT/wjs/plugins/wjs_review/migrations/0019_pasteditorassignment_on_appeal.py` (generated)
- Modify: `$WT/wjs/plugins/wjs_review/logic.py` (`BaseDeassignEditor` ~3781, `SupervisorChangeEditorAssignment._deassign_current_editor` ~3893)
- Modify: `$WT/wjs/plugins/wjs_review/tests/conftest.py` (appeal fixtures ~282-331)
- Test: `$WT/wjs/plugins/wjs_review/tests/test_appeal_permissions.py` (new)

**Interfaces:**
- Produces: `PastEditorAssignment.on_appeal: bool` (default `False`); `BaseDeassignEditor.appeal: bool = False`;
  fixtures `appeal_editor -> JCOMProfile` (section editor named "appeal_editor"),
  `under_appeal_article_new_editor -> Article` (rejected by `section_editor`, appeal opened by EO handing it
  to `appeal_editor`), `appeal_submitted_article -> Article` (the previous one after the author resubmits,
  state `EditorSelected`).

- [ ] **Step 1: Write the failing tests.** Create `test_appeal_permissions.py`:

```python
import pytest
from django.http import HttpRequest
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from ..logic import HandleEditorDeclinesAssignment, SupervisorChangeEditorAssignment
from ..models import PastEditorAssignment, WjsEditorAssignment


@pytest.mark.django_db
def test_open_appeal_with_new_editor_flags_past_assignment(
    under_appeal_article_new_editor: Article, section_editor: JCOMProfile
):
    """The editor removed when the EO opens an appeal gets a PastEditorAssignment flagged on_appeal."""
    past = PastEditorAssignment.objects.get(
        article=under_appeal_article_new_editor, editor=section_editor.janeway_account
    )
    assert past.on_appeal is True, "Editor removed by OpenAppeal must be flagged on_appeal"


@pytest.mark.django_db
def test_supervisor_change_editor_does_not_flag_on_appeal(
    assigned_article: Article, normal_user: JCOMProfile, eo_user: JCOMProfile, fake_request: HttpRequest
):
    """A plain editor change by the EO does not flag the past assignment."""
    normal_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = eo_user.janeway_account
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    old_editor = assignment.editor
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=assignment,
        new_editor=normal_user.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    past = PastEditorAssignment.objects.get(article=assigned_article, editor=old_editor)
    assert past.on_appeal is False, "Plain editor change must not flag on_appeal"


@pytest.mark.django_db
def test_editor_decline_does_not_flag_on_appeal(
    assigned_article: Article, section_editor: JCOMProfile, fake_request: HttpRequest
):
    """An editor declining the assignment does not flag the past assignment."""
    fake_request.user = section_editor.janeway_account
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    HandleEditorDeclinesAssignment(
        assignment=assignment,
        editor=section_editor.janeway_account,
        request=fake_request,
        form_data={"decline_reason": PastEditorAssignment.DeclineReasons.BUSY, "decline_text": ""},
    ).run()
    past = PastEditorAssignment.objects.get(article=assigned_article, editor=section_editor.janeway_account)
    assert past.on_appeal is False, "Editor decline must not flag on_appeal"


@pytest.mark.django_db
def test_appeal_submitted_article_fixture(appeal_submitted_article: Article, appeal_editor: JCOMProfile):
    """The appeal_submitted_article fixture yields an article back with the appeal editor after resubmission."""
    assert appeal_submitted_article.reviewround_set.count() == 2, "Resubmission must open a new review round"
    current = WjsEditorAssignment.objects.get_current(appeal_submitted_article)
    assert current.editor == appeal_editor.janeway_account, "Appeal editor must be the current editor"
```

Before running, check the real signature of `HandleEditorDeclinesAssignment` in `logic.py` (~line 4060)
and the expected `form_data` keys (look at an existing test with `grep -n "HandleEditorDeclinesAssignment(" $T/*.py`).
Adapt the call to match, keeping the assertion. Do the same for `SupervisorChangeEditorAssignment` (fields
listed at `logic.py` ~3883).

- [ ] **Step 2: Add the fixtures** in `tests/conftest.py`. Next to `under_appeal_article` add:

```python
@pytest.fixture
def appeal_editor(create_jcom_user, roles, journal, keywords) -> JCOMProfile:
    """Return a section editor, different from `section_editor`, to be assigned on appeal."""
    user = create_jcom_user("appeal_editor")
    user.add_account_role("section-editor", journal)
    return user


@pytest.fixture
def under_appeal_article_new_editor(fake_request, rejected_article, eo_user, appeal_editor) -> Article:
    """Return an under appeal article whose appeal has been assigned to a new editor (`appeal_editor`)."""
    return _under_appeal_article(rejected_article, fake_request, eo_user, appeal_editor)
```

Replace the broken `appeal_submitted_article` fixture (it depends on a non-existent `open_appeal_article`
and passes the `rejected_article` function object) with:

```python
@pytest.fixture
def appeal_submitted_article(fake_request: HttpRequest, under_appeal_article_new_editor: Article) -> Article:
    """Return an article whose author resubmitted after an appeal assigned to `appeal_editor`."""
    return _appeal_submitted_article(under_appeal_article_new_editor, fake_request)
```

First `grep -rn "appeal_submitted_article" $WT/wjs` to confirm no test uses the old fixture. If one does,
report it rather than silently changing its meaning. Make sure `JCOMProfile` is imported in conftest
(it probably is).

- [ ] **Step 3: Run the tests and verify they fail**

Run: `pytest --create-db $T/test_appeal_permissions.py -v`
Expected: the three `on_appeal` tests FAIL (`AttributeError`/`FieldError` on `on_appeal`);
`test_appeal_submitted_article_fixture` PASSES (if it fails, fix the fixture before continuing).

- [ ] **Step 4: Add the field.** In `PastEditorAssignment`, after `decline_text`:

```python
    on_appeal = models.BooleanField(
        default=False,
        verbose_name=_("Removed on appeal"),
        help_text=_("The editor was removed when the Editorial Office opened an appeal."),
    )
```

- [ ] **Step 5: Generate the migration** (from `janeway/src`):
`python manage.py makemigrations wjs_review -n pasteditorassignment_on_appeal`.
Check that exactly one file, `$WT/wjs/plugins/wjs_review/migrations/0019_pasteditorassignment_on_appeal.py`,
was created with a single `AddField`. Delete any other migration file the command created (in any app),
and check `git -C $WT status` and `git -C /home/yakky/Projects/sissa/janeway status` for strays.

- [ ] **Step 6: Propagate the flag.** In `BaseDeassignEditor`, add the field after `request`:

```python
    appeal: bool = False
```

and in `_delete_assignment` pass it:

```python
        past = PastEditorAssignment.objects.create(
            editor=self.assignment.editor,
            article=self.assignment.article,
            date_assigned=self.assignment.assigned,
            date_unassigned=timezone.now(),
            on_appeal=self.appeal,
        )
```

In `SupervisorChangeEditorAssignment._deassign_current_editor`:

```python
        past_assignment = BaseDeassignEditor(
            assignment=self.assignment,
            editor=self.assignment.editor,
            request=self.request,
            appeal=self.appeal,
        ).run()
```

- [ ] **Step 7: Run the tests and verify they pass**

Run: `pytest --create-db $T/test_appeal_permissions.py $T/test_logic.py -k "appeal or deassign or decline or change_editor" -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
cd $WT && git add wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/logic.py \
  wjs/plugins/wjs_review/migrations/0019_pasteditorassignment_on_appeal.py \
  wjs/plugins/wjs_review/tests/conftest.py wjs/plugins/wjs_review/tests/test_appeal_permissions.py
git -c commit.gpgsign=false commit -m "feat(review): flag past editor assignments removed on appeal" -m "Refs: wjs/specs#2903" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfdgZNn9WqwzvU4GQSaP36"
```

---

### Task 2: `EditorType` and `get_editor_type`

**Files:**
- Modify: `$WT/wjs/plugins/wjs_review/permissions.py` (add after `is_past_article_editor`, ~line 470)
- Test: `$WT/wjs/plugins/wjs_review/tests/test_editor_type.py` (new)

**Interfaces:**
- Consumes: `PastEditorAssignment.on_appeal`, fixtures `under_appeal_article_new_editor`, `appeal_editor`,
  `under_appeal_article` (same-editor appeal), `assigned_article` (Task 1 / existing conftest).
- Produces:
  ```python
  class EditorType(models.TextChoices):
      ASSIGNED = "assigned", _("Assigned editor")
      APPEAL = "appeal", _("Appeal editor")
      PAST = "past", _("Past editor")
      REMOVED_FOR_APPEAL = "removed_for_appeal", _("Removed editor for appeal")

  def get_editor_type(user: Account, article: Article) -> Optional[EditorType]: ...
  ```
  Both live in `plugins.wjs_review.permissions` (import in code as `from . import permissions` /
  `from .permissions import EditorType, get_editor_type`).

- [ ] **Step 1: Write the failing tests.** Create `test_editor_type.py`:

```python
import pytest
from django.http import HttpRequest
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from ..logic import AssignToEditor, SupervisorChangeEditorAssignment
from ..models import WjsEditorAssignment
from ..permissions import EditorType, get_editor_type


@pytest.mark.django_db
def test_assigned_editor(assigned_article: Article, section_editor: JCOMProfile):
    """The current editor of an article without appeal is an assigned editor."""
    result = get_editor_type(section_editor.janeway_account, assigned_article)
    assert result == EditorType.ASSIGNED, "Current editor without appeal must be ASSIGNED"


@pytest.mark.django_db
def test_appeal_editor(under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile):
    """The new editor assigned on appeal is an appeal editor."""
    result = get_editor_type(appeal_editor.janeway_account, under_appeal_article_new_editor)
    assert result == EditorType.APPEAL, "Editor assigned on appeal must be APPEAL"


@pytest.mark.django_db
def test_removed_for_appeal_editor(under_appeal_article_new_editor: Article, section_editor: JCOMProfile):
    """The editor removed when the appeal was opened is a removed-for-appeal editor."""
    result = get_editor_type(section_editor.janeway_account, under_appeal_article_new_editor)
    assert result == EditorType.REMOVED_FOR_APPEAL, "Editor removed on appeal must be REMOVED_FOR_APPEAL"


@pytest.mark.django_db
def test_same_editor_on_appeal_is_assigned(under_appeal_article: Article, section_editor: JCOMProfile):
    """The editor who rejected and is re-assigned to the appeal is treated as assigned editor (spec D2)."""
    result = get_editor_type(section_editor.janeway_account, under_appeal_article)
    assert result == EditorType.ASSIGNED, "Same editor on appeal must be ASSIGNED"


@pytest.mark.django_db
def test_past_editor(
    assigned_article: Article,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """An editor replaced outside an appeal is a past editor."""
    normal_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = eo_user.janeway_account
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        new_editor=normal_user.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    result = get_editor_type(section_editor.janeway_account, assigned_article)
    assert result == EditorType.PAST, "Replaced editor must be PAST"


@pytest.mark.django_db
def test_past_editor_reassigned_is_current(
    assigned_article: Article,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
):
    """A current assignment wins over past ones: a replaced-then-reassigned editor is ASSIGNED."""
    normal_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = eo_user.janeway_account
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        new_editor=normal_user.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    SupervisorChangeEditorAssignment(
        article=assigned_article,
        assignment=WjsEditorAssignment.objects.get_current(assigned_article),
        new_editor=section_editor.janeway_account,
        request=fake_request,
        deassignment_message="bye",
        assignment_message="hello",
    ).run()
    result = get_editor_type(section_editor.janeway_account, assigned_article)
    assert result == EditorType.ASSIGNED, "Re-assigned editor must be treated by the current role"


@pytest.mark.django_db
def test_non_editor(assigned_article: Article, normal_user: JCOMProfile):
    """A user with no editor assignment, present or past, has no editor type."""
    assert get_editor_type(normal_user.janeway_account, assigned_article) is None, "Non editor must be None"
```

`AssignToEditor` is imported for completeness only; remove the import if flake8 flags it as unused.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest $T/test_editor_type.py -v`
Expected: collection ERROR `ImportError: cannot import name 'EditorType'`.

- [ ] **Step 3: Implement.** In `permissions.py`, make sure `from django.db import models`,
`from django.utils.translation import gettext_lazy as _`, `from typing import Optional` and the
`Article` type are available (check the existing imports at the top and reuse them; `Article` may only
be imported under `TYPE_CHECKING`, in which case use the string annotation `"Article"`). Then add, after
`is_past_article_editor`:

```python
class EditorType(models.TextChoices):
    """Kinds of editor relationship a user can have with an article (see specs#2903 glossary)."""

    ASSIGNED = "assigned", _("Assigned editor")
    APPEAL = "appeal", _("Appeal editor")
    PAST = "past", _("Past editor")
    REMOVED_FOR_APPEAL = "removed_for_appeal", _("Removed editor for appeal")


def get_editor_type(user: Account, article: "Article") -> Optional[EditorType]:
    """
    Return the kind of editor the user is for the given article.

    A current assignment always wins over past ones:

    - current editor, article has an OPEN_APPEAL decision and the user made no REJECT decision: APPEAL;
    - any other current editor (including the editor who rejected and is re-assigned on appeal): ASSIGNED;
    - past assignment flagged ``on_appeal``: REMOVED_FOR_APPEAL;
    - other past assignment: PAST;
    - otherwise ``None``.

    :param user: The user to classify.
    :type user: Account
    :param article: The article to check.
    :type article: Article
    :return: The editor type, or None if the user is not a current or past editor of the article.
    :rtype: Optional[EditorType]
    """
    from .models import ArticleWorkflow, EditorDecision, PastEditorAssignment, WjsEditorAssignment

    if WjsEditorAssignment.objects.get_all(article).filter(editor=user).exists():
        decisions = EditorDecision.objects.filter(workflow__article=article)
        has_open_appeal = decisions.filter(decision=ArticleWorkflow.Decisions.OPEN_APPEAL).exists()
        rejected_by_user = decisions.filter(decision=ArticleWorkflow.Decisions.REJECT, editor=user).exists()
        if has_open_appeal and not rejected_by_user:
            return EditorType.APPEAL
        return EditorType.ASSIGNED
    past_assignments = PastEditorAssignment.objects.filter(article=article, editor=user)
    if past_assignments.filter(on_appeal=True).exists():
        return EditorType.REMOVED_FOR_APPEAL
    if past_assignments.exists():
        return EditorType.PAST
    return None
```

Check that `WjsEditorAssignment.objects.get_all()` accepts an `Article` (it's called with both `Article`
and `ArticleWorkflow` in `logic__visibility.py`). If it only accepts one, pass the right object.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest $T/test_editor_type.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT && git add wjs/plugins/wjs_review/permissions.py wjs/plugins/wjs_review/tests/test_editor_type.py
git -c commit.gpgsign=false commit -m "feat(review): classify editors of an article by appeal status" -m "Refs: wjs/specs#2903" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfdgZNn9WqwzvU4GQSaP36"
```

---

### Task 3: Review versions for past editors and the appeal editor

**Files:**
- Modify: `$WT/wjs/plugins/wjs_review/models.py` (`ArticleWorkflow.get_review_versions`, ~line 1290)
- Modify: `$WT/wjs/plugins/wjs_review/logic__visibility.py` (`EditorPermissionChecker.check_default`, ~line 282)
- Test: `$WT/wjs/plugins/wjs_review/tests/test_appeal_permissions.py` (append)

**Interfaces:**
- Consumes: `permissions.get_editor_type`, `permissions.EditorType` (Task 2); fixtures from Task 1.
- Produces: no new public names. Behaviour: past editors get every round in their
  `PastEditorAssignment.review_rounds`. The appeal editor loses rounds holding an OPEN_APPEAL decision.

Background you need (see spec §2):
- `get_review_versions(user)` loops rounds newest first and builds a `ReviewVersion` only when
  `has_permission` is true. `ReviewVersion.file_container` is the current round's `EditorRevisionRequest`
  (version files), `ReviewVersion.cover_letter.object` is the **previous** round's `EditorRevisionRequest`
  (checked with `secondary_permission=True`), and `final_decision()` is the editor report.
- The template `details/elements/review_version.html` checks each element with
  `PermissionChecker` via `{% user_has_access_to %}`, so element visibility is decided by
  `EditorPermissionChecker.check_default` (custom `PermissionAssignment` rows are checked first by
  `BasePermissionChecker.check`).
- On an editor change `_migrate_review_assignments` moves the current round's review assignments to the
  new editor. That's why the appeal editor must lose the whole appealed round, not just the
  `WjsEditorAssignment` branch.

- [ ] **Step 1: Write the failing tests.** Append to `test_appeal_permissions.py` (add the imports at the top of the file):

```python
from django.contrib.contenttypes.models import ContentType

from ..logic__visibility import PermissionChecker
from ..models import ArticleWorkflow, EditorDecision, PermissionAssignment


@pytest.mark.django_db
def test_appeal_editor_does_not_see_appealed_round(
    under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile
):
    """The appeal editor does not get the rejected round that holds the OPEN_APPEAL decision."""
    workflow = under_appeal_article_new_editor.articleworkflow
    versions = workflow.get_review_versions(appeal_editor.janeway_account)
    assert [v.number for v in versions] == [-1], "Appeal editor must only see the initial fake version"


@pytest.mark.django_db
def test_appeal_editor_sees_rounds_after_resubmission(appeal_submitted_article: Article, appeal_editor: JCOMProfile):
    """After the author resubmits, the appeal editor sees the new round but still not the appealed one."""
    workflow = appeal_submitted_article.articleworkflow
    numbers = [v.number for v in workflow.get_review_versions(appeal_editor.janeway_account)]
    assert 2 in numbers, "Appeal editor must see the round opened by the resubmission"
    assert 1 not in numbers, "Appeal editor must not see the appealed round"


@pytest.mark.django_db
def test_same_editor_on_appeal_sees_rejected_round(under_appeal_article: Article, section_editor: JCOMProfile):
    """The editor who rejected and is re-assigned on appeal keeps seeing the rejected round (spec D2)."""
    workflow = under_appeal_article.articleworkflow
    numbers = [v.number for v in workflow.get_review_versions(section_editor.janeway_account)]
    assert 1 in numbers, "Same editor on appeal must keep the rejected round"


@pytest.mark.django_db
def test_removed_for_appeal_editor_sees_their_round_and_report(
    appeal_submitted_article: Article, section_editor: JCOMProfile
):
    """The editor removed on appeal sees the round they handled with its editor report, not later rounds."""
    workflow = appeal_submitted_article.articleworkflow
    user = section_editor.janeway_account
    versions = {v.number: v for v in workflow.get_review_versions(user)}
    assert 1 in versions, "Removed editor must see the round they handled"
    assert 2 not in versions, "Removed editor must not see rounds after their removal"
    decision = versions[1].final_decision()
    assert decision is not None, "Round 1 must have a final (reject) decision"
    assert PermissionChecker()(
        workflow, user, decision, permission_type=PermissionAssignment.PermissionType.NO_NAMES, review_round=1
    ), "Removed editor must see the editor report of their round"


@pytest.mark.django_db
def test_past_editor_sees_decision_they_did_not_take(
    appeal_submitted_article: Article, appeal_editor: JCOMProfile, section_editor: JCOMProfile
):
    """A past editor sees editor decisions of rounds they handled even when another user took them."""
    workflow = appeal_submitted_article.articleworkflow
    open_appeal = EditorDecision.objects.get(workflow=workflow, decision=ArticleWorkflow.Decisions.OPEN_APPEAL)
    assert open_appeal.editor != section_editor.janeway_account, "Precondition: OPEN_APPEAL is taken by the EO"
    assert PermissionChecker()(
        workflow,
        section_editor.janeway_account,
        open_appeal,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        review_round=1,
    ), "Past editor must see decisions of the rounds they handled"


@pytest.mark.django_db
def test_past_editor_custom_deny_wins(appeal_submitted_article: Article, section_editor: JCOMProfile):
    """An explicit DENY custom permission hides an item even in a round the past editor handled."""
    workflow = appeal_submitted_article.articleworkflow
    user = section_editor.janeway_account
    decision = EditorDecision.objects.get(workflow=workflow, decision=ArticleWorkflow.Decisions.OPEN_APPEAL)
    PermissionAssignment.objects.create(
        content_type=ContentType.objects.get_for_model(decision),
        object_id=decision.pk,
        user=user,
        permission=PermissionAssignment.PermissionType.DENY,
        permission_secondary=PermissionAssignment.BinaryPermissionType.DENY,
    )
    assert not PermissionChecker()(
        workflow, user, decision, permission_type=PermissionAssignment.PermissionType.NO_NAMES, review_round=1
    ), "Custom DENY must win over the past-editor default"


@pytest.mark.django_db
def test_editor_without_past_rounds_unchanged(appeal_submitted_article: Article, normal_user: JCOMProfile):
    """Users who never edited the article gain nothing from the past-round rule."""
    normal_user.add_account_role("section-editor", appeal_submitted_article.journal)
    workflow = appeal_submitted_article.articleworkflow
    decision = EditorDecision.objects.get(workflow=workflow, decision=ArticleWorkflow.Decisions.OPEN_APPEAL)
    assert not PermissionChecker()(
        workflow,
        normal_user.janeway_account,
        decision,
        permission_type=PermissionAssignment.PermissionType.NO_NAMES,
        review_round=1,
    ), "An unrelated editor must not see the decision"
```

Also add a reviewer-report test using the existing review-assignment fixtures. First read
`grep -n "def review_assignment\|def assigned_article_with_reviewer\|def _submit_review\|complete" $T/conftest.py`
and pick the fixture/helper that produces a **completed** review on the round before the editor is
replaced. Then: replace the editor with `SupervisorChangeEditorAssignment` (as in Task 2's
`test_past_editor`) and assert
`PermissionChecker()(workflow, old_editor, review_assignment, permission_type=PermissionAssignment.PermissionType.ALL, review_round=<n>)`
is True, and the same check on an **incomplete** assignment of the same round is False. Name it
`test_past_editor_sees_completed_reviews_only`.

Custom-permission model field names (`permission`, `permission_secondary`, `DENY` members) must be checked
against `models.py` `class PermissionAssignment` (~line 2094) before running.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest $T/test_appeal_permissions.py -v`
Expected: `test_appeal_editor_does_not_see_appealed_round`, `test_appeal_editor_sees_rounds_after_resubmission`
(the "1 not in" assertion) and `test_past_editor_sees_decision_they_did_not_take` FAIL. The others may
already pass; note which ones.

- [ ] **Step 3: Implement the `EditorPermissionChecker` defaults.** In `logic__visibility.py`, add these
private helpers to `EditorPermissionChecker` (make sure `ReviewRound` is imported from `review.models`,
and reuse existing imports where present):

```python
    def _past_review_rounds(self) -> "QuerySet[ReviewRound]":
        """Return the review rounds the user handled as a (now removed) editor of the article."""
        return ReviewRound.objects.filter(
            pasteditorassignment__editor=self.user,
            pasteditorassignment__article=self.workflow.article,
        ).distinct()

    def _in_past_review_rounds(self, review_round: Optional[ReviewRound]) -> bool:
        """Check if the review round is one the user handled as a past editor."""
        return review_round is not None and self._past_review_rounds().filter(pk=review_round.pk).exists()

    def _revision_in_past_review_rounds(self, revision: RevisionRequest, secondary_permission: bool) -> bool:
        """
        Check if a revision request belongs to a round the user handled as a past editor.

        The author cover letter of a version lives on the previous round's revision request, so for the
        secondary permission the revision request of the round before a past round is also granted.
        """
        review_round = getattr(revision, "review_round", None)
        if review_round is None:
            return False
        numbers = set(self._past_review_rounds().values_list("round_number", flat=True))
        if review_round.round_number in numbers:
            return True
        return secondary_permission and review_round.round_number + 1 in numbers
```

`review_round` on a Janeway `RevisionRequest` exists only on the `EditorRevisionRequest` subclass. If
the instance is a plain `RevisionRequest`, try `revision.editorrevisionrequest.review_round` (multi-table
child accessor) before giving up; check how `EditorRevisionRequest` is declared (`models.py` ~1846).

Then change the three branches in `check_default`:

```python
        if isinstance(self.instance, EditorDecision):
            return self.instance.editor == self.user or self._in_past_review_rounds(self.instance.review_round)
        if isinstance(self.instance, RevisionRequest):
            return self.instance.editor == self.user or self._revision_in_past_review_rounds(
                self.instance, secondary_permission
            )
        if isinstance(self.instance, ReviewAssignment):
            return self.instance.editor == self.user or (
                self.instance.date_complete is not None and self._in_past_review_rounds(self.instance.review_round)
            )
```

Update the `check_default` docstring with one sentence on the past-rounds rule.

- [ ] **Step 4: Implement `get_review_versions`.** In `models.py`, before the loop:

```python
        editor_type = permissions.get_editor_type(user, self.article)
        past_round_ids = set(
            PastEditorAssignment.objects.filter(article=self.article, editor=user).values_list(
                "review_rounds", flat=True
            )
        )
        past_round_ids.discard(None)
```

At the start of the loop body, right after `decisions = ...`:

```python
            # Appeal editors must not see the rejected round the appeal was opened on (specs#2903).
            # Skipping the whole round is required: review assignments of that round were moved to them.
            is_appealed_round = any(
                decision.decision == ArticleWorkflow.Decisions.OPEN_APPEAL for decision in decisions
            )
            if editor_type == permissions.EditorType.APPEAL and is_appealed_round:
                continue
```

and just before `if has_permission:`:

```python
            # Past editors keep the rounds they handled (PastEditorAssignment.review_rounds)
            has_permission = has_permission or review_round.pk in past_round_ids
```

`PastEditorAssignment` is defined later in the same module; that's fine at call time. `ArticleWorkflow`
is `self.__class__`, so use `self.Decisions.OPEN_APPEAL` if the name isn't resolvable there. The
`for ... else` still appends the fake version, because `continue` doesn't break the loop.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `pytest $T/test_appeal_permissions.py $T/test_visibility.py $T/test_permissions.py -v`
(skip any of those files that doesn't exist)
Expected: all PASS. Then run the view-level suites that render versions:
`pytest $T/test_views.py -k "version or detail or past" -v` → all PASS.

- [ ] **Step 6: Commit**

```bash
cd $WT && git add wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/logic__visibility.py \
  wjs/plugins/wjs_review/tests/test_appeal_permissions.py
git -c commit.gpgsign=false commit -m "feat(review): restrict review versions by editor appeal status" -m "Refs: wjs/specs#2903" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfdgZNn9WqwzvU4GQSaP36"
```

---

### Task 4: Listing row and "Last submitted" for former editors

**Files:**
- Modify: `$WT/wjs/plugins/wjs_review/templatetags/wjs_review.py` (add two filters near `is_user_past_article_editor`, ~line 510)
- Modify: `$WT/wjs/plugins/wjs_review/templates/wjs_review/lists/elements/editor/table_item.html`
- Modify: `$WT/wjs/plugins/wjs_review/templates/wjs_review/details/elements/metadata_main.html` (~line 44)
- Test: `$WT/wjs/plugins/wjs_review/tests/test_appeal_permissions.py` (append)

**Interfaces:**
- Consumes: `permissions.get_editor_type`, `permissions.EditorType` (Task 2); `get_version_submission_date`
  (existing filter in the same module); fixtures from Task 1.
- Produces: template filters `is_user_former_article_editor(workflow: ArticleWorkflow, user: Account) -> bool`
  and `hide_last_submitted(workflow: ArticleWorkflow, user: Account) -> bool`.

- [ ] **Step 1: Write the failing tests.** Append:

```python
import datetime

from django.template.loader import render_to_string
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone

from ..templatetags.wjs_review import hide_last_submitted, is_user_former_article_editor


@pytest.mark.django_db
def test_former_editor_filter(
    under_appeal_article_new_editor: Article, section_editor: JCOMProfile, appeal_editor: JCOMProfile
):
    """Removed-for-appeal editors are former editors, appeal editors are not."""
    workflow = under_appeal_article_new_editor.articleworkflow
    assert is_user_former_article_editor(workflow, section_editor.janeway_account), "Removed editor is former"
    assert not is_user_former_article_editor(workflow, appeal_editor.janeway_account), "Appeal editor is current"


@pytest.mark.django_db
def test_hide_last_submitted_after_unassignment(appeal_submitted_article: Article, section_editor: JCOMProfile):
    """Last submitted is hidden to a former editor when the revision came after their removal."""
    workflow = appeal_submitted_article.articleworkflow
    assert hide_last_submitted(workflow, section_editor.janeway_account), "Newer submission must be hidden"


@pytest.mark.django_db
def test_hide_last_submitted_uses_latest_unassignment(appeal_submitted_article: Article, section_editor: JCOMProfile):
    """With several past assignments, the latest date_unassigned is the reference."""
    workflow = appeal_submitted_article.articleworkflow
    user = section_editor.janeway_account
    existing = PastEditorAssignment.objects.get(article=appeal_submitted_article, editor=user)
    later = PastEditorAssignment.objects.create(
        article=appeal_submitted_article,
        editor=user,
        date_assigned=existing.date_assigned,
        date_unassigned=timezone.now() + datetime.timedelta(days=1),
    )
    assert not hide_last_submitted(workflow, user), "Submission older than the latest unassignment is shown"
    later.delete()


@pytest.mark.django_db
def test_hide_last_submitted_never_for_current_editors(appeal_submitted_article: Article, appeal_editor: JCOMProfile):
    """Current editors always see Last submitted."""
    workflow = appeal_submitted_article.articleworkflow
    assert not hide_last_submitted(workflow, appeal_editor.janeway_account), "Current editor always sees it"


@pytest.mark.django_db
def test_listing_row_for_former_editor(
    client: Client, under_appeal_article_new_editor: Article, section_editor: JCOMProfile
):
    """A former editor sees the article as Unassigned, without reviewers and issue, in the archived list."""
    client.force_login(section_editor.janeway_account)
    response = client.get(reverse("wjs_review_archived_papers"))
    assert response.status_code == 200, "Archived list must load"
    content = response.content.decode()
    assert "Unassigned" in content, "Former editor must see the fixed Unassigned status"
```

Also add a test for the appeal editor's row showing the real state label. It must reach the article
where the appeal editor actually sees it: check `EditorPending`/`EditorArchived._apply_base_filters`
in `views.py` (UNDER_APPEAL is in the archived list for current assignments), and assert the
workflow's state label (`workflow.get_state_display()`, or whatever `table_item_status.html` prints)
is in the content and "Unassigned" is not. For the issue cell, assign an issue to the article in the
test (look for an issue fixture: `grep -n "def .*issue" $T/conftest.py ../../wjs-profile-project/.worktrees/issue-2903-appeal-editor-permissions/wjs/jcom_profile/tests/conftest.py`)
and assert its title is absent for the former editor. The URL `reverse` needs the journal-scoped
client: copy the host/journal setup used by existing `client.get` tests in `test_views.py` if the
plain call returns 404.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest $T/test_appeal_permissions.py -k "former or last_submitted or listing" -v`
Expected: ImportError / FAIL.

- [ ] **Step 3: Implement the filters** in `templatetags/wjs_review.py`, after `is_user_past_article_editor`:

```python
@register.filter
def is_user_former_article_editor(workflow: ArticleWorkflow, user: Account) -> bool:
    """Return if the user is a past editor (removed on appeal or not) and not a current editor of the article."""
    return permissions.get_editor_type(user, workflow.article) in (
        permissions.EditorType.PAST,
        permissions.EditorType.REMOVED_FOR_APPEAL,
    )


@register.filter
def hide_last_submitted(workflow: ArticleWorkflow, user: Account) -> bool:
    """
    Return if the "Last submitted" date must be hidden to the user.

    Former editors must not see submissions that happened after their latest removal.
    """
    if not is_user_former_article_editor(workflow, user):
        return False
    latest_unassigned = (
        PastEditorAssignment.objects.filter(article=workflow.article, editor=user)
        .order_by("-date_unassigned")
        .values_list("date_unassigned", flat=True)
        .first()
    )
    return get_version_submission_date(workflow.article) > latest_unassigned
```

Import `PastEditorAssignment` from `..models` if the module doesn't already import it.

- [ ] **Step 4: Update the templates.** In `table_item.html` add `i18n` to the `{% load %}` line, and
replace the status, reviewers and issue cells with:

```django
    {% with former_editor=workflow|is_user_former_article_editor:user %}
      <td class="position-relative {% if attention_flag %}fw-bolder align-bottom{% endif %}">
        {% if former_editor %}
          {% translate "Unassigned" %}
        {% else %}
          {% include "wjs_review/lists/elements/table_item_status.html" %}
        {% endif %}
      </td>
      <td>
        {% if workflow.state == "EditorSelected" and not former_editor %}
          {% include "wjs_review/lists/elements/table_item_reviewer.html" %}
        {% endif %}
      </td>
      <td title="Issue (if available)">
        {% if not former_editor %}{{ article.primary_issue|internal_title|default:"—" }}{% endif %}
      </td>
    {% endwith %}
```

Check that the `user` variable is the logged-in user in this template (it's already used as
`article_requires_attention_tt:user`). If `request.user` is what's actually available, use that.
In `metadata_main.html` change the "Last submitted" condition to:

```django
    {% if article|get_version_submission_date != article.date_submitted and not workflow|hide_last_submitted:request.user %}
```

Confirm `workflow` is in the template context (it's used a few lines below as `workflow|is_user_article_reviewer`).

- [ ] **Step 5: Status badge pin.** Add a test proving the article page doesn't show the state badge to a
former editor, and does show it to the appeal editor. Render `wjs_review/details/sections/title.html`
with `render_to_string(template, {"workflow": workflow, "article": workflow.article, "request": request, "user": user})`,
where `request` is a `fake_request` with `.user` set. Look at how `test_views.py:~1727` calls
`render_to_string` and copy its context setup. Assert on the badge's state text. This is a regression
pin only: no template change is expected. If it fails for the former editor, stop and report.

- [ ] **Step 6: Run the tests and verify they pass**

Run: `pytest $T/test_appeal_permissions.py $T/test_views.py -k "former or last_submitted or listing or badge or archived or pending" -v`
Expected: all PASS. Lint templates: `djlint --profile=django $WT/wjs/plugins/wjs_review/templates/wjs_review/lists/elements/editor/table_item.html $WT/wjs/plugins/wjs_review/templates/wjs_review/details/elements/metadata_main.html`,
with no new errors compared to `git stash`-ed originals.

- [ ] **Step 7: Commit**

```bash
cd $WT && git add wjs/plugins/wjs_review/templatetags/wjs_review.py \
  wjs/plugins/wjs_review/templates/wjs_review/lists/elements/editor/table_item.html \
  wjs/plugins/wjs_review/templates/wjs_review/details/elements/metadata_main.html \
  wjs/plugins/wjs_review/tests/test_appeal_permissions.py
git -c commit.gpgsign=false commit -m "feat(review): hide current article data from former editors" -m "Refs: wjs/specs#2903" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfdgZNn9WqwzvU4GQSaP36"
```

---

### Task 5: "Appeal granted" as a system message, data migration, and timeline pins

**Files:**
- Modify: `$WT/wjs/plugins/wjs_review/logic.py` (`OpenAppeal._log_author`, ~line 4167)
- Create: `$WT/wjs/plugins/wjs_review/migrations/0020_appeal_granted_system_actor.py`
- Test: `$WT/wjs/plugins/wjs_review/tests/test_appeal_permissions.py` (append); adjust `test_logic.py::test_open_appeal` only if it asserts the actor

**Interfaces:**
- Consumes: fixtures from Task 1; `communication_utils.get_messages_related_to_me` and `get_system_user`
  (existing; read their signatures in `communication_utils.py` before writing tests).
- Produces: migration function `set_system_actor_on_appeal_granted(apps, schema_editor)` in
  `0020_appeal_granted_system_actor`.

- [ ] **Step 1: Write the failing tests.** Append:

```python
import importlib

from django.apps import apps as django_apps
from django.db.models import Q

from ..communication_utils import get_messages_related_to_me, get_system_user
from ..models import Message


def _appeal_granted_messages(article: Article):
    return Message.objects.filter(object_id=article.pk, subject="Appeal granted")


@pytest.mark.django_db
def test_appeal_granted_sent_by_system_user(under_appeal_article_new_editor: Article):
    """The Appeal granted message is logged by the system user."""
    message = _appeal_granted_messages(under_appeal_article_new_editor).get()
    assert message.actor == get_system_user(under_appeal_article_new_editor.journal), "Actor must be system user"
    assert under_appeal_article_new_editor.correspondence_author in message.recipients.all(), "Author is recipient"


@pytest.mark.django_db
def test_appeal_editor_timeline_excludes_appeal_granted(
    under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile
):
    """The appeal editor does not see the Appeal granted message in the article timeline."""
    messages = get_messages_related_to_me(appeal_editor.janeway_account, under_appeal_article_new_editor)
    assert not messages.filter(subject="Appeal granted").exists(), "Appeal editor must not see Appeal granted"
```

Check the real signature of `get_messages_related_to_me` (`communication_utils.py` ~line 58) and adapt
the call (it may take `journal`/`article` keyword arguments and return a queryset or list). Also add, in
the same style:
- `test_removed_for_appeal_editor_gets_no_notifications`: after `under_appeal_article_new_editor`, no
  message on the article has `section_editor` as recipient **created by the appeal flow** (no
  deassignment, no open-appeal message). Assert on
  `Message.objects.filter(object_id=article.pk, recipients=section_editor.janeway_account, created__gte=<time captured before opening the appeal>)`.
  Since the fixture opens the appeal, build the scenario inline instead: take `rejected_article`,
  capture `now()`, call `_under_appeal_article(rejected_article, fake_request, eo_user, appeal_editor)`
  (import it from `.conftest`), then assert.
- `test_timeline_excludes_hijack_for_editors`: a pin that messages with `message_type=Message.MessageTypes.HIJACK`
  are absent for each editor type (existing behaviour). Create one with the `create_user_message`
  fixture or `Message.objects.create(...)`, adding the editor as recipient.

Migration tests:

```python
MIGRATION = "plugins.wjs_review.migrations.0020_appeal_granted_system_actor"


@pytest.mark.django_db
def test_migration_moves_appeal_granted_to_system_user(
    under_appeal_article_new_editor: Article, appeal_editor: JCOMProfile
):
    """Existing Appeal granted messages sent by the appeal editor are reassigned to the system user."""
    article = under_appeal_article_new_editor
    message = _appeal_granted_messages(article).get()
    message.actor = appeal_editor.janeway_account  # simulate data from before this change
    message.save()
    importlib.import_module(MIGRATION).set_system_actor_on_appeal_granted(django_apps, None)
    message.refresh_from_db()
    assert message.actor == get_system_user(article.journal), "Old message must now be sent by the system user"


@pytest.mark.django_db
def test_migration_leaves_other_messages(
    under_appeal_article_new_editor: Article, assigned_article: Article, appeal_editor: JCOMProfile
):
    """Messages with other subjects, other recipients, or on articles without appeal are untouched."""
    ...
```

Fill in `test_migration_leaves_other_messages` concretely. Create three messages with
`communication_utils.log_operation` and `actor=appeal_editor.janeway_account`:
(a) subject "Appeal granted" on `assigned_article` (no OPEN_APPEAL decision), recipient its correspondence author;
(b) subject "Something else" on the appealed article, recipient its correspondence author;
(c) subject "Appeal granted" on the appealed article, recipient `appeal_editor`.
Run the migration function and assert all three still have `actor == appeal_editor.janeway_account`.
Add `test_migration_skips_journal_without_system_user`: change the `general/support_email` setting for
the journal (`utils.setting_handler.save_setting("general", "support_email", journal, "nobody@example.com")`),
run the function, and assert no exception is raised and the message is unchanged.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest $T/test_appeal_permissions.py -k "appeal_granted or timeline or migration or notifications" -v`
Expected: `test_appeal_granted_sent_by_system_user`, `test_appeal_editor_timeline_excludes_appeal_granted`
and the migration tests FAIL (`ModuleNotFoundError` for the migration). The pins pass.

- [ ] **Step 3: Change the sender.** In `OpenAppeal._log_author`, remove the `actor=self.new_editor,` line
from the `communication_utils.log_operation(...)` call and add a comment:

```python
        # No actor: logged by the system user, so the appeal editor doesn't see it (specs#2903)
        communication_utils.log_operation(
            article=self.article,
            message_subject=message_subject,
            message_body=message_body,
            recipients=[self.article.correspondence_author],
            # refs https://gitlab.sissamedialab.it/wjs/specs/-/work_items/1469
            flag_as_read_by_eo=True,
        )
```

- [ ] **Step 4: Write the data migration** `0020_appeal_granted_system_actor.py`:

```python
from django.db import migrations
from django.template import Context, Template
from review.const import EditorialDecisions
from utils.setting_handler import get_setting


def _system_user(Account, journal):
    """Return the system user of the journal, or None if it cannot be found."""
    email = get_setting("general", "support_email", journal=journal).value
    return Account.objects.filter(email=email).first()


def set_system_actor_on_appeal_granted(apps, schema_editor):
    """Set the system user as actor of existing "Appeal granted" messages (specs#2903)."""
    Account = apps.get_model("core", "Account")
    Article = apps.get_model("submission", "Article")
    ContentType = apps.get_model("contenttypes", "ContentType")
    EditorDecision = apps.get_model("wjs_review", "EditorDecision")
    Message = apps.get_model("wjs_review", "Message")

    article_ct = ContentType.objects.get_for_model(Article)
    article_ids = (
        EditorDecision.objects.filter(decision=EditorialDecisions.OPEN_APPEAL.value)
        .values_list("workflow__article_id", flat=True)
        .distinct()
    )
    for article in Article.objects.filter(pk__in=article_ids).select_related("journal", "correspondence_author"):
        system_user = _system_user(Account, article.journal)
        if system_user is None or article.correspondence_author_id is None:
            continue
        subject_template = get_setting("wjs_review", "eo_opens_appeal_subject", journal=article.journal).value
        subject = Template(subject_template).render(Context({"article": article})).strip()
        Message.objects.filter(
            content_type=article_ct,
            object_id=article.pk,
            subject=subject,
            recipients=article.correspondence_author_id,
        ).exclude(actor=system_user).update(actor=system_user)


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0019_pasteditorassignment_on_appeal"),
    ]

    operations = [
        migrations.RunPython(set_system_actor_on_appeal_granted, migrations.RunPython.noop),
    ]
```

Things to check and adapt before running:
- the `EditorDecision` → article path (`workflow__article_id`) against the model (~models.py 1415);
- the `OPEN_APPEAL` value: `ArticleWorkflow.Decisions.OPEN_APPEAL` is defined from `EditorialDecisions.OPEN_APPEAL.value`,
  so confirm with `grep -n "OPEN_APPEAL" $WT/wjs/plugins/wjs_review/models.py`;
- the `Message.recipients` M2M name and the through model (`MessageRecipients`). If filtering by
  `recipients=<id>` doesn't work on the historical model, use `messagerecipients__recipient_id=`.
  `.update()` on a queryset with an M2M join may fail with duplicates; if so, collect `pk`s first and update
  `Message.objects.filter(pk__in=pks)`;
- `Account` app label (`core`).

The precedent for calling `get_setting` inside a migration is `wjs/jcom_profile/migrations/0021_fill_recipient_language.py`.
`get_setting` works with real (not historical) journal objects. If it rejects the historical `Journal`, load
`journal.models.Journal.objects.get(pk=article.journal_id)` inside the loop and pass that.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `pytest $T/test_appeal_permissions.py $T/test_logic.py::test_open_appeal $T/test_communications.py -v`
Expected: all PASS. If `test_open_appeal` or a communications test asserted the old actor, update that
assertion to the system user and say so in the commit body.

Also check the migration applies on a real DB (from `janeway/src`): `python manage.py migrate wjs_review`
→ applies `0018` and `0019` without errors. Then `python manage.py migrate wjs_review 0017` → reverses
cleanly, and `python manage.py migrate wjs_review` again.

- [ ] **Step 6: Commit**

```bash
cd $WT && git add wjs/plugins/wjs_review/logic.py wjs/plugins/wjs_review/migrations/0020_appeal_granted_system_actor.py \
  wjs/plugins/wjs_review/tests/test_appeal_permissions.py
git -c commit.gpgsign=false commit -m "feat(review): send Appeal granted message as system user" -m "Refs: wjs/specs#2903" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfdgZNn9WqwzvU4GQSaP36"
```

(Add `wjs/plugins/wjs_review/tests/test_logic.py` / `test_communications.py` if you changed them.)

---

### Task 6: Withdraw-after-appeal notification settings

**Files:**
- Modify: `$WT/wjs/plugins/wjs_review/plugin_settings.py` (new function after `author_withdraws_preprint_message`, ~line 1555; register it next to `csv_writer.write_settings(author_withdraws_preprint_message())`, ~line 2164)
- Modify: `$WT/wjs/plugins/wjs_review/views.py` (the withdraw view's `get_initial`, ~line 3871)
- Test: `$WT/wjs/plugins/wjs_review/tests/test_appeal_permissions.py` (append)

**Interfaces:**
- Consumes: fixtures from Task 1; `EditorDecision`, `ArticleWorkflow.Decisions.REJECT`.
- Produces: settings `wjs_review/author_withdraws_preprint_after_appeal_subject` and
  `wjs_review/author_withdraws_preprint_after_appeal_body`.

- [ ] **Step 1: Write the failing tests.** Find the withdraw view class and URL name: open `views.py` around
line 3820-3895 (the class whose `get_initial` renders `author_withdraws_preprint_subject`), then
`grep -n "<ClassName>" $WT/wjs/plugins/wjs_review/urls.py`. Also look at how `review_settings` creates
settings (`grep -n "def review_settings" -A 10 $T/conftest.py`) so the new settings exist in tests.
Append:

```python
from utils.setting_handler import save_setting


@pytest.mark.django_db
def test_withdraw_after_appeal_uses_dedicated_settings(
    client: Client, under_appeal_article_new_editor: Article, review_settings
):
    """Withdrawing during an appeal prefills the notification from the after-appeal settings."""
    article = under_appeal_article_new_editor
    save_setting("wjs_review", "author_withdraws_preprint_after_appeal_subject", article.journal, "After appeal subject")
    client.force_login(article.correspondence_author)
    response = client.get(reverse("<withdraw url name>", args=(article.articleworkflow.pk,)))
    assert response.status_code == 200, "Withdraw page must load"
    assert (
        response.context["form"].initial["notification_subject"] == "After appeal subject"
    ), "Withdraw during appeal must use the after-appeal subject"


@pytest.mark.django_db
def test_withdraw_without_rejection_uses_standard_settings(client: Client, assigned_article: Article, review_settings):
    """Withdrawing an article never rejected keeps the standard settings."""
    save_setting("wjs_review", "author_withdraws_preprint_after_appeal_subject", assigned_article.journal, "After appeal subject")
    client.force_login(assigned_article.correspondence_author)
    response = client.get(reverse("<withdraw url name>", args=(assigned_article.articleworkflow.pk,)))
    assert response.status_code == 200, "Withdraw page must load"
    assert (
        response.context["form"].initial["notification_subject"] != "After appeal subject"
    ), "Withdraw without rejection must use the standard subject"
```

Replace `<withdraw url name>` with the real name, and use `args` matching the URL (article pk vs
workflow pk). The `correspondence_author` may be a `JCOMProfile` in some fixtures: use `.janeway_account`
if `force_login` complains (see the FIXME in `test_logic.py::test_author_or_owner_withdraws_preprint`).
Add a third test: GET the page on `under_appeal_article_new_editor` **without** the override, and assert
the initial subject equals the standard `author_withdraws_preprint_subject` rendered value (the defaults
are identical copies until MT provides the wording).

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest $T/test_appeal_permissions.py -k withdraw -v`
Expected: FAIL (setting doesn't exist / subject mismatch).

- [ ] **Step 3: Add the settings.** In `plugin_settings.py`, inside `set_default_plugin_settings`, add after
`author_withdraws_preprint_message()`. Copy **verbatim** the subject and body default values from
`author_withdraws_preprint_message()` into `<copied subject>` and `<copied body>`:

```python
    def author_withdraws_preprint_after_appeal_message():
        # Default text is a copy of author_withdraws_preprint_*: final wording pending from MT (specs#2903)
        subject_setting: SettingParams = {
            "name": "author_withdraws_preprint_after_appeal_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for author withdrawing a preprint after an appeal."),
            "description": _(
                "The subject of the notification that is sent to the Editor/EO when a preprint is withdrawn "
                "after a rejection (during or after an appeal).",
            ),
            "is_translatable": False,
        }
        subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "<copied subject>",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_setting, subject_setting_value, subject_setting["name"], force=force
        )
        body_setting: SettingParams = {
            "name": "author_withdraws_preprint_after_appeal_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for author withdrawing a preprint after an appeal."),
            "description": _(
                "The body of the notification that is sent to the Editor/EO when a preprint is withdrawn after a "
                "rejection (during or after an appeal). The author can modify it (so don't include the editor's "
                "name).",
            ),
            "is_translatable": False,
        }
        body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """<copied body>""",
            "translations": {},
        }
        setting_2 = create_customization_setting(body_setting, body_setting_value, body_setting["name"], force=force)
        return setting_1, setting_2
```

and register it right after the existing line:

```python
        csv_writer.write_settings(author_withdraws_preprint_message())
        csv_writer.write_settings(author_withdraws_preprint_after_appeal_message())
```

Match the exact structure of the neighbouring `author_withdraws_preprint_message` (check whether it
uses `force=force` and the same helper names; mirror it exactly).

- [ ] **Step 4: Use them in the view.** In the withdraw view's `get_initial`, choose the setting names by
rejection:

```python
    def _get_withdraw_setting_names(self) -> tuple[str, str]:
        """Return subject and body setting names: withdrawals after a rejection (appeal) use dedicated texts."""
        after_rejection = EditorDecision.objects.filter(
            workflow=self.object, decision=ArticleWorkflow.Decisions.REJECT
        ).exists()
        if after_rejection:
            return "author_withdraws_preprint_after_appeal_subject", "author_withdraws_preprint_after_appeal_body"
        return "author_withdraws_preprint_subject", "author_withdraws_preprint_body"

    def get_initial(self):
        initial = super().get_initial()
        subject_setting, body_setting = self._get_withdraw_setting_names()
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name=subject_setting,
            ...
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name=body_setting,
            ...
        )
```

Keep the remaining arguments of both `render_template_from_setting` calls exactly as they are now.
Check `self.object` is the `ArticleWorkflow` (the existing code uses `self.object.article`), and that
`EditorDecision`/`ArticleWorkflow` are already imported in `views.py`.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `pytest $T/test_appeal_permissions.py -k withdraw $T/test_logic.py -k withdraw -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd $WT && git add wjs/plugins/wjs_review/plugin_settings.py wjs/plugins/wjs_review/views.py \
  wjs/plugins/wjs_review/tests/test_appeal_permissions.py
git -c commit.gpgsign=false commit -m "feat(review): dedicated notification text for withdrawal after appeal" -m "Default text copies the standard withdrawal message until MT provides the final wording." -m "Refs: wjs/specs#2903" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfdgZNn9WqwzvU4GQSaP36"
```

---

### Task 7: Branch verification (controller)

- [ ] Run every touched test module together (from `janeway/src`):
  `pytest -n4 $T/test_appeal_permissions.py $T/test_editor_type.py $T/test_logic.py $T/test_views.py $T/test_visibility.py $T/test_communications.py`
  → all PASS (report exact counts).
- [ ] `cd $WT && pre-commit run --files $(git diff --name-only origin/wjs-develop...HEAD)` → clean.
- [ ] Whole-branch review against the spec (superpowers:requesting-code-review).
- [ ] Restore the Janeway symlinks and the editable install to the main checkout (Task 0, last step), or
  leave them on the worktree if the user wants to test manually. Ask.
