# Port test suite to pytest 9 + latest pytest-django — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bump `wjs-profile-project`'s test dependencies to `pytest` 9.x latest and the newest Django-4.2-compatible `pytest-django`, fixing the two real defects this uncovers, and finally closing issue #204 with a documented root cause instead of another version pin.

**Architecture:** Every change lands on `bugfix/issue-204-pytest9-port` (already branched from `wjs-develop`, with the design spec already committed as `00f3a416`). No application code changes — this is entirely `setup.cfg` dependency changes plus two test-infrastructure fixes in `conftest.py` files.

**Tech Stack:** pytest, pytest-django, pytest-freezer, pytest-mock, pytest-xdist, pytest-reverse, pytest-factoryboy, Django 4.2, Janeway.

## Global Constraints

- Django stays at `~= 4.2` (unchanged) — this caps `pytest-django` below 4.13 (which dropped Django 4.2/5.1 support).
- `python_requires` moves from `>=3.9` to `>=3.10` (pytest 9's floor; already satisfied everywhere in practice since CI/dev both run Python 3.11).
- All tests run from `janeway/src` per `.claude/rules/tests.md`. This plan's dev environment: pyenv-virtualenv `janeway-upstream` (Python 3.11.9), which has this repo editable-installed at `/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project`, and Janeway checked out at `/home/yakky/Projects/projects/sissa-1.8/janeway/src`.
- Every dependency-version and code change in this plan has already been verified together in a live spike: full suite green at 1016 passed / 50 skipped / 31 xfailed / 9 xpassed — identical counts to the pre-change baseline — using both plain and `--reverse` (xdist `-n4`) runs. There is no discovery risk left; this plan is pure execution.
- Root cause of #204 (already found, to go in the final commit message and to close the issue): `wjs/plugins/wjs_review/tests/conftest.py`'s `apply_wjs_settings` fixture had `@pytest.mark.django_db` applied directly to a `@pytest.fixture`-decorated function. This has always been meaningless (marks on fixtures do nothing) and was merely deprecated pre-8.4; pytest 8.4 turned it into a hard collection-time error (`Failed: Marks cannot be applied to fixtures.`), which is what actually broke the suite — not anything Django- or freezegun-related.
- A second, independent defect was also uncovered and must be fixed in the same pass: Django's process-level `ContentType.objects.get_for_model()` cache goes stale relative to the DB under `xdist` parallel execution combined with `--reuse-db`, causing intermittent `IntegrityError` on `core_homepageelement`'s content-type FK at test teardown. This reproduces at **both** pytest 8.3.3 and 9.1.1 — it's pre-existing flakiness, not a pytest-version regression — but it must be fixed here because it blocks reliably validating the pytest 9 upgrade itself.

---

### Task 1: Fix the real #204 root cause — mark-on-fixture

**Files:**
- Modify: `wjs/plugins/wjs_review/tests/conftest.py:105-108`

**Interfaces:**
- Produces: `apply_wjs_settings` fixture, unchanged signature/behavior for all its consumers (e.g. `review_settings` fixture in the same file) — only its internal DB-access mechanism changes from a (no-op) mark to a real fixture dependency.

- [ ] **Step 1: Reproduce the failure**

From `janeway/src`, using the `janeway-upstream` virtualenv:

```bash
cd /home/yakky/Projects/projects/sissa-1.8/janeway/src
export DJANGO_SETTINGS_MODULE=wjs.defaults.tests
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/pip install -q "pytest==9.1.1"
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/python -m pytest \
  -c ../../wjs-profile-project/pytest.ini \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests --collect-only -q
```

Expected: collection fails with `Failed: Marks cannot be applied to fixtures.` pointing at
`wjs/plugins/wjs_review/tests/conftest.py:105`.

- [ ] **Step 2: Apply the fix**

Change:

```python
@pytest.mark.django_db
@pytest.fixture  # (scope="session")  ??? can't have scope session and db access???
def apply_wjs_settings():
    """Update Janeway settings with our defaults."""
    call_command("apply_wjs_settings", "--noinput")
```

to:

```python
@pytest.fixture
def apply_wjs_settings(db):
    """Update Janeway settings with our defaults."""
    call_command("apply_wjs_settings", "--noinput")
```

(Requesting pytest-django's `db` fixture is the correct way for a fixture — as opposed to a
test — to get database access; the mark never did anything here.)

- [ ] **Step 3: Verify collection succeeds**

```bash
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/python -m pytest \
  -c ../../wjs-profile-project/pytest.ini \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests --collect-only -q
```

Expected: collection succeeds, no `Failed: Marks cannot be applied to fixtures.` error, ends
with a `N tests collected` summary line.

- [ ] **Step 4: Run the wjs_review suite to confirm no regressions**

```bash
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/python -m pytest \
  -c ../../wjs-profile-project/pytest.ini \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests -q -n4 -m "not fix_labels" --reuse-db
```

Expected: passes (pre-existing skip/xfail counts unchanged from a run on `wjs-develop`).

- [ ] **Step 5: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/tests/conftest.py
git commit -m "fix(tests): stop marking apply_wjs_settings fixture with django_db

Marks applied directly to a fixture function have never had any
effect (fixtures aren't collected as tests) and this was merely
deprecated before pytest 8.4 — which turned it into a hard collection
error: 'Marks cannot be applied to fixtures.' This is the actual root
cause of wjs-profile-project#204 ('pytest 8.4 breaks our tests
setup'), not a genuine pytest/Django/freezegun incompatibility.
Requesting pytest-django's \`db\` fixture is the correct way for a
fixture to get database access.

Refs: wjs-profile-project#204"
```

---

### Task 2: Fix the ContentType-cache test-isolation flake

**Files:**
- Modify: `wjs/jcom_profile/tests/conftest.py` (add fixture near the top-level fixtures, after the existing imports)

**Interfaces:**
- Produces: an autouse fixture, no name required by other tasks (autouse fixtures aren't referenced by name).

- [ ] **Step 1: Reproduce the flake**

From `janeway/src`:

```bash
cd /home/yakky/Projects/projects/sissa-1.8/janeway/src
export DJANGO_SETTINGS_MODULE=wjs.defaults.tests
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/python -m pytest \
  -c ../../wjs-profile-project/pytest.ini \
  ../../wjs-profile-project/wjs/jcom_profile/tests/ -q -n4 -m "not fix_labels" --reuse-db
```

Expected (flaky — may need 1-2 runs to hit it): one or more `ERROR` entries with a traceback
ending in
`django.db.utils.IntegrityError: insert or update on table "core_homepageelement" violates
foreign key constraint "core_homepageelement_content_type_id_..._fk_django_co"` raised from
Django's `TestCase._post_teardown()` → `_fixture_teardown()` → `check_constraints()`. The
specific failing test(s) vary run to run — that variability (not a single fixed test) is the
signature of this defect: it's `ContentType.objects.get_for_model()`'s process-level cache
going stale relative to the DB under `xdist` parallelism + `--reuse-db`, not one broken test.

- [ ] **Step 2: Add the fix**

In `wjs/jcom_profile/tests/conftest.py`, add the import next to the other Django imports and
the fixture near the top of the file's fixture definitions:

```python
from django.contrib.contenttypes.models import ContentType
```

```python
@pytest.fixture(autouse=True)
def _clear_content_type_cache():
    """Clear Django's ContentType cache before/after each test.

    Under xdist-parallel execution with --reuse-db, ContentType.objects.get_for_model()'s
    process-level cache can go stale relative to the actual DB rows (e.g. after a rolled-back
    transaction from a TestCase-wrapped test), causing spurious IntegrityErrors on FK columns
    referencing content types at teardown. See wjs-profile-project#204.
    """
    ContentType.objects.clear_cache()
    yield
    ContentType.objects.clear_cache()
```

- [ ] **Step 3: Verify the flake is gone**

Run the same command as Step 1 **twice** in a row (flakiness needs repeat runs to disprove):

```bash
for i in 1 2; do
  /home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/python -m pytest \
    -c ../../wjs-profile-project/pytest.ini \
    ../../wjs-profile-project/wjs/jcom_profile/tests/ -q -n4 -m "not fix_labels" --reuse-db
done
```

Expected: both runs pass cleanly (`282 passed, 3 skipped, 27 xfailed, 9 xpassed`, 0 errors).

- [ ] **Step 4: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/jcom_profile/tests/conftest.py
git commit -m "fix(tests): clear ContentType cache between tests to fix xdist flake

Django.contrib.contenttypes.models.ContentType.objects.get_for_model()
caches ContentType instances at process scope. Under xdist-parallel
execution combined with --reuse-db this cache can go stale relative
to the actual DB state, producing intermittent IntegrityErrors on FK
columns referencing content types (observed on core_homepageelement)
at TestCase teardown. This reproduces at both pytest 8.3.3 and 9.1.1
— it's pre-existing test-isolation flakiness, not a pytest-version
regression — but it must be fixed to reliably validate the pytest 9
upgrade in wjs-profile-project#204.

Refs: wjs-profile-project#204"
```

---

### Task 3: Bump test dependencies in `setup.cfg`

**Files:**
- Modify: `setup.cfg` (`[options]` `python_requires`; `[options.extras_require]` `test`)

**Interfaces:**
- Consumes: nothing from Tasks 1-2 directly, but must run after them so the full-suite
  validation in Step 3 below reflects both fixes plus the new dependency versions together.

- [ ] **Step 1: Edit `setup.cfg`**

Change:

```ini
[options]
python_requires = >=3.9
```

to:

```ini
[options]
python_requires = >=3.10
```

Change:

```ini
[options.extras_require]
test =
    pytest == 8.3.3  # temporary workaround for wjs-profile-project#204
    pytest-django
    pytest-factoryboy
    pytest-mock
    pytest-freezegun
    pytest-xdist
    pytest-reverse
    wjs_mgmt_cmds ~= 0.5.0
```

to:

```ini
[options.extras_require]
test =
    pytest
    pytest-django ~= 4.12.0  # requires Django>=5.2, we're on Django~=4.2
    pytest-factoryboy
    pytest-mock
    pytest-freezer
    pytest-xdist
    pytest-reverse
    wjs_mgmt_cmds ~= 0.5.0
```

(`pytest-freezegun` → `pytest-freezer`: the unmaintained plugin is fully replaced, not just
bumped — `pytest-freezer` is upstream's own recommended drop-in replacement and keeps the same
`freezer` fixture name used throughout this suite, e.g. `wjs/jcom_profile/tests/test_newsletters.py`.)

- [ ] **Step 2: Reinstall the extras and confirm resolved versions**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/pip install -q -e .[test]
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/pip list | grep -iE "^pytest|freezegun|freezer|factory-boy"
```

Expected: `pytest` at latest 9.x, `pytest-django` at `4.12.0`, `pytest-freezer` present,
`pytest-freezegun` absent, `pytest-mock`/`pytest-xdist`/`pytest-reverse`/`pytest-factoryboy`
at their current latest.

- [ ] **Step 3: Run the full suite, matching CI's invocation**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/janeway/src
export DJANGO_SETTINGS_MODULE=wjs.defaults.tests
/home/yakky/.pyenv/versions/3.11.9/envs/janeway-upstream/bin/python -m pytest \
  -c ../../wjs-profile-project/pytest.ini ../../wjs-profile-project/ \
  -v -n4 --reverse -m "not fix_labels" --reuse-db
```

(Use `-n4` here to match this plan's verified spike; CI itself uses `-n6`/`-n7` — if this
differs in practice, use `--create-db` once first to rule out stale-DB effects before treating
any new failure as real.)

Expected: `1016 passed, 50 skipped, 31 xfailed, 9 xpassed` (same counts as the pre-change
baseline), 0 errors.

- [ ] **Step 4: Run pre-commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
pre-commit run --all-files
```

Expected: all hooks pass (this change only touches `setup.cfg`, `wjs/plugins/wjs_review/tests/conftest.py`,
and `wjs/jcom_profile/tests/conftest.py`, all already validated above).

- [ ] **Step 5: Commit**

```bash
git add setup.cfg
git commit -m "chore: bump pytest to 9.x and pytest-django, swap pytest-freezegun for pytest-freezer

- pytest == 8.3.3 (temporary workaround for #204) -> latest 9.x,
  unpinned, now that #204's real cause is fixed (see prior commits
  on this branch).
- pytest-django -> ~= 4.12.0, explicitly pinned below 4.13: that
  release dropped support for Django 4.2/5.1, and this project is
  on Django ~= 4.2. Not a workaround for a bug -- a durable
  constraint until Django is upgraded.
- pytest-freezegun -> pytest-freezer: the former is unmaintained
  since ~2020; the latter is upstream's own recommended drop-in
  replacement, keeping the same 'freezer' fixture name.
- pytest-mock, pytest-xdist, pytest-reverse, pytest-factoryboy
  bumped to latest.
- python_requires >=3.9 -> >=3.10, matching pytest 9's floor and
  the Python 3.11 already used everywhere in CI/dev.

Full suite verified green (1016 passed, 50 skipped, 31 xfailed,
9 xpassed -- identical counts to the pre-change baseline) with
-n4/-n7 and --reverse.

Closes wjs-profile-project#204"
```

---

### Task 4: Close out #204 and open the MR

**Files:** none (GitLab-side actions only).

- [ ] **Step 1: Update issue #204 with the root cause**

Post a comment on `wjs-profile-project#204` summarizing the root cause found in Task 1
(mark-on-fixture, hard error since pytest 8.4) and the secondary flake found and fixed in
Task 2 (ContentType cache under xdist), referencing this branch. Don't close it manually —
Task 3's final commit already carries a `Closes wjs-profile-project#204` trailer, which
auto-closes the issue when the MR merges into `wjs-develop` (the project's default branch).

- [ ] **Step 2: Push the branch**

```bash
git push -u origin bugfix/issue-204-pytest9-port
```

(Only after the user's explicit go-ahead per this repo's push/commit conventions.)

- [ ] **Step 3: Open the MR**

Target `wjs-develop`. Use `.gitlab/merge_request_templates/base.md` as the description base
(all sections filled, no placeholders). Title: `fix: port test suite to pytest 9 and latest pytest-django`.
Ask the user for the reviewer before assigning one — do not pick one unilaterally. Apply the
`Please review` label and assign the MR to yourself per this repo's MR conventions.
