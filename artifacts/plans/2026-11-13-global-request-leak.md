# Global request leak between tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `test_staff_disable_notification` deterministic by resetting Janeway's global request around every test,
and make `get_hijacker()` return `None` when the hijacker account no longer exists (wjs-profile-project#302).

**Architecture:** A context manager, `isolated_global_request()`, clears `utils.middleware.context_request` and then
restores it. An autouse fixture in the `jcom_profile` conftest wraps every test in it, and the `wjs_review` conftest
inherits the fixture through its existing import. `get_hijacker()` gains an `Account.DoesNotExist` branch that logs a
warning and returns `None`.

**Tech Stack:** Django, pytest, pytest-django, pytest-xdist, django-hijack, Janeway (`core.middleware`,
`utils.middleware`, `utils.logger`).

**Spec:** `artifacts/specs/2026-11-13-global-request-leak-design.md`

## Global Constraints

- Tests run from `janeway/src`: `pytest -c ../../wjs-profile-project/pytest.ini <paths relative to janeway/src>`.
- No retries, no `flaky` markers, no cleanup only inside `test_hijack_notifications` (anti-goals).
- No test may depend on test order or on xdist distribution (anti-goal).
- Logger convention: `from utils.logger import get_logger`, then `logger = get_logger(__name__)`.
- Assertions carry a message (`.claude/rules/tests.md`).
- Documentation language: English.

## Review Focus

- A test that sets a request in its body, so the helper must restore the pre-test value. Covered by the helper test
  (Task 1, step 1).
- An exception raised inside the wrapped block must still restore the request (`finally`). Covered by
  `test_isolated_global_request_restores_on_exception` (Task 1).
- `wjs_review` tests inherit the fixture only through `from wjs.jcom_profile.tests.conftest import *`. Covered by the
  fixture-active test in `wjs_review` (Task 1).
- `get_hijacker()` with an empty `hijack_history` or a request that isn't hijacked must still return `None` without a
  warning. The existing `test_hijack_notifications` covers the not-hijacked path. Task 2 asserts that the warning
  appears only in the missing-account case.
- `profile/forms.py` reads `context_request.get()` directly. Now that every test starts with `None`, a form test that
  relied on a leaked request would fail. The full-suite run in Task 3 surfaces this.

---

### Task 1: `isolated_global_request()` helper and autouse fixture

**Files:**
- Create: `wjs/jcom_profile/tests/global_request.py`
- Modify: `wjs/jcom_profile/tests/conftest.py` (new fixture next to `clear_cache`, around line 131)
- Test (create): `wjs/jcom_profile/tests/test_global_request.py`
- Test (create): `wjs/plugins/wjs_review/tests/test_global_request_isolation.py`

**Interfaces:**
- Produces: `isolated_global_request() -> Iterator[None]` (a `contextlib.contextmanager`) in
  `wjs.jcom_profile.tests.global_request`, and an autouse fixture `clear_global_request` in the `jcom_profile` conftest.

- [ ] **Step 1: Write the failing tests**

In `wjs/jcom_profile/tests/test_global_request.py`:

- `test_isolated_global_request_clears_and_restores(fake_request, rf)`:
  1. Call `GlobalRequestMiddleware.process_request(fake_request)`.
  2. Assert that `GlobalRequestMiddleware.get_current_request() is fake_request`.
  3. Inside `with isolated_global_request():`, assert that `get_current_request() is None`. Then set
     `inner = rf.get("/")` with `process_request(inner)`, and assert that `get_current_request() is inner`.
  4. After the block, assert that `get_current_request() is fake_request`.
- `test_isolated_global_request_restores_on_exception(fake_request)`: set `fake_request`. Then run
  `with pytest.raises(ValueError): with isolated_global_request(): raise ValueError`, and assert that
  `get_current_request() is fake_request`.
- `test_clear_global_request_fixture_is_active(request)`: assert that
  `"clear_global_request" in request.fixturenames`.

In `wjs/plugins/wjs_review/tests/test_global_request_isolation.py`:

- `test_clear_global_request_fixture_is_active_in_wjs_review(request)`: the same assertion.

- [ ] **Step 2: Run the tests and check that they fail**

Run (from `janeway/src`):
`pytest -c ../../wjs-profile-project/pytest.ini -p no:xdist ../../wjs-profile-project/wjs/jcom_profile/tests/test_global_request.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_global_request_isolation.py -v`
Expected: an `ImportError` on `wjs.jcom_profile.tests.global_request` (collection error) for the jcom_profile file, and
FAIL on the `fixturenames` assertion in `wjs_review`.

- [ ] **Step 3: Implement the helper and the fixture**

- In `global_request.py`, write a `@contextmanager def isolated_global_request() -> Iterator[None]` that does
  `token = context_request.set(None)`, then `try: yield`, then `finally: context_request.reset(token)`. Import with
  `from utils.middleware import context_request`. Add a docstring explaining why it exists (#302).
- In `conftest.py`, add `@pytest.fixture(autouse=True) def clear_global_request():` with `with
  isolated_global_request(): yield`. Its docstring explains that the ContextVar is never reset, that a request left
  by one test (for example a hijacked one) leaks into the next tests on the same worker, and references
  wjs-profile-project#302.

- [ ] **Step 4: Run the tests and check that they pass**

Run: the same command as step 2. Expected: 4 passed.

- [ ] **Step 5: Run the reproduction from the issue**

Run (from `janeway/src`):
`pytest -c ../../wjs-profile-project/pytest.ini -p no:xdist "../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_communications.py::test_hijack_notifications[True-False-False]" ../../wjs-profile-project/wjs/jcom_profile/tests/test_staff_parameters.py::test_staff_disable_notification`
Expected: 3 passed (2 failed and 1 passed before this task).

- [ ] **Step 6: Commit (only if per-change commits were chosen)**

`test: reset the global request around every test (#302)`

### Task 2: `get_hijacker()` handles a missing hijacker account

**Files:**
- Modify: `wjs/jcom_profile/permissions.py` (imports at the top, and `get_hijacker()` around lines 303-321)
- Test (create): `wjs/jcom_profile/tests/test_get_hijacker.py`

**Interfaces:**
- Consumes: nothing from Task 1. The autouse fixture is simply active.
- Produces: the unchanged signature `get_hijacker() -> Optional[Account]`.

- [ ] **Step 1: Write the failing test**

`test_get_hijacker_missing_hijacker_account(fake_request, normal_user, caplog)`. Build a hijacked request the same way
as `test_hijack_notifications` in `wjs_review/tests/test_communications.py`, but with a pk that doesn't exist:

- `missing_pk = (Account.objects.order_by("-pk").values_list("pk", flat=True).first() or 0) + 1000`
- `fake_request.session["hijack_history"] = [str(missing_pk)]`
- `login(fake_request, normal_user)`
- `GlobalRequestMiddleware.process_request(fake_request)`
- `HijackUserMiddleware(BaseHandler.get_response).process_request(fake_request)`
- Assert that `fake_request.user.is_hijacked`, as a precondition with a message.

Then, under `caplog.at_level(logging.WARNING, logger="wjs.jcom_profile.permissions")`:

- Assert that `get_hijacker() is None`.
- Assert that one record is at `WARNING` and has `str(missing_pk)` in its message.

- [ ] **Step 2: Run the test and check that it fails**

Run: `pytest -c ../../wjs-profile-project/pytest.ini -p no:xdist ../../wjs-profile-project/wjs/jcom_profile/tests/test_get_hijacker.py -v`
Expected: FAIL with `Account.DoesNotExist` ("Account matching query does not exist.").

- [ ] **Step 3: Implement**

- In `permissions.py`, add the module logger (see Global Constraints).
- In `get_hijacker()`, wrap `Account.objects.get(pk=hijacker_id)` in `try` / `except Account.DoesNotExist:`. Log
  `logger.warning(...)` with the missing `hijacker_id`, and return `None`. Leave the `AttributeError` handling as it
  is. Add a line to the docstring saying that a deleted hijacker account gives `None`.

- [ ] **Step 4: Run the test and check that it passes**

Run: the same command as step 2. Expected: 1 passed. Also run
`../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_communications.py -k hijack` and expect all to pass.

- [ ] **Step 5: Commit (only if per-change commits were chosen)**

`fix: get_hijacker returns None when the hijacker account is missing (#302)`

### Task 3: Full-suite verification

- [ ] **Step 1: Run the full suite in parallel**

Run (from `janeway/src`): `pytest -c ../../wjs-profile-project/pytest.ini -n7 -m "not fix_labels" ../../wjs-profile-project`
Expected: no failures beyond those already present on `wjs-develop`. List every new failure for the user, because
each one is a test that depended on a leaked request. Don't fix them silently.

- [ ] **Step 2: Run the affected packages without xdist**

Run: the same command with `-p no:xdist` instead of `-n7`, limited to `wjs/jcom_profile/tests` and
`wjs/plugins/wjs_review/tests`, if time allows (the rules say the full suite takes 10+ minutes). Expected: the same as
step 1.

- [ ] **Step 3: Report**

Give the user the counts from both runs and any new failures, with their cause.
