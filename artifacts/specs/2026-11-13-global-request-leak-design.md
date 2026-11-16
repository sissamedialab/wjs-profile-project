# Global request leak between tests — design

Issue: wjs/wjs-profile-project#302 (https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/302)
Size: small change (short design).

## Intent

`wjs/jcom_profile/tests/test_staff_parameters.py::test_staff_disable_notification` fails at random in CI because the
request stored by Janeway's `GlobalRequestMiddleware` (ContextVar `utils.middleware.context_request`) is never reset
between tests. A hijacked request left by `wjs_review/tests/test_communications.py::test_hijack_notifications[True-*]`
makes `get_hijacker()` look up an account that was rolled back. That raises `Account.DoesNotExist`, which
`send_staff_assignment_change()` swallows, so no email is sent. Goal: make the suite deterministic with respect to the
global request, and make `get_hijacker()` robust to a hijacker account that no longer exists.

## Goals

1. Every test in `jcom_profile` and `wjs_review` starts with no global request, and the previous value is restored
   after the test (fix 1 of the issue).
2. `get_hijacker()` returns `None` and logs a warning when the hijacker account in `hijack_history` doesn't exist,
   instead of raising `Account.DoesNotExist` (fix 2 of the issue).

## Non-goals

- Other global state shared between tests (only the stored request is in scope).
- The unrelated wjs-submission pipeline pinning problem, and the duplicate `0024` migrations seen in MR !1621.

## Anti-goals

- Hiding the problem: no retries, no `flaky` markers, no cleanup only inside `test_hijack_notifications`.
- Tests whose result depends on the order of the tests or on how xdist distributes them.

## Approaches considered (for testing fix 1)

- **A. Helper plus a check that the fixture is active (chosen).** The reset logic lives in a context manager,
  `isolated_global_request()`. The autouse fixture only does `with isolated_global_request(): yield`. One test checks
  the helper (clears, then restores), and one test per package checks that the fixture is in `request.fixturenames`.
  Deterministic, fast, no new plugins.
- **B. Nested pytest session with `pytester`.** A real end-to-end check of a leak between tests, but it needs the
  `pytester` plugin and an inner session that has to deal with pytest-django and the large conftest. Slower and can
  break on its own. Rejected as too heavy for this fix.
- **C. Two tests next to each other, or `xdist_group`.** Two adjacent tests only catch the leak when xdist puts them
  on the same worker. `xdist_group` requires `--dist loadgroup` for the whole suite. Rejected.

## Design

### Fix 1 — reset the global request around every test

- New context manager `isolated_global_request()` in a test-utility module under `wjs/jcom_profile/tests/` (the
  builder picks the module). It does `token = context_request.set(None)`, then `yield`, then
  `context_request.reset(token)` in a `finally`.
- New autouse fixture `clear_global_request` in `wjs/jcom_profile/tests/conftest.py`, next to the existing cleanup
  fixtures, with a docstring explaining the leak and referencing #302. `wjs/plugins/wjs_review/tests/conftest.py`
  already imports that conftest, so the fixture applies to both packages.
- Autouse fixtures run before other fixtures of the same scope, so fixtures that set a request for a single test
  (`fake_request`, `create_rich_fake_request`, ...) keep working. Checked: no session-, module- or class-scoped fixture
  in the repo sets the request.

### Fix 2 — `get_hijacker()` handles a missing hijacker account

- In `wjs/jcom_profile/permissions.py::get_hijacker()`, catch `Account.DoesNotExist` around the account lookup, log a
  warning that includes the missing pk, and return `None`. Add a module logger using the convention
  `from utils.logger import get_logger`, then `logger = get_logger(__name__)`.
- Every caller already treats `None` as "not hijacked": `logic.py`, `views.py`, `jpbridge/views_fake_jp.py` and
  `import_wjapp/import_logic.py`.

### Tests (written first, Red → Green)

- Helper test: set a fake request with `GlobalRequestMiddleware.process_request(...)`. Inside
  `isolated_global_request()`, `get_current_request()` is `None`. Setting another request inside is allowed. After
  exit, the original request is back.
- Fixture-active test in `jcom_profile` and in `wjs_review`: `"clear_global_request" in request.fixturenames`.
- `get_hijacker()` test: a hijacked fake request whose `hijack_history` points to a pk that doesn't exist.
  `get_hijacker()` returns `None` and logs the warning (`caplog`).

### Verification

- Reproduction from the issue (`-p no:xdist`, `test_hijack_notifications[True-False-False]` followed by
  `test_staff_disable_notification`): expect 3 passed (2 failed and 1 passed before the change).
- Full suite with the fixture, with and without xdist, compared with `wjs-develop`. Any new failure is a test that
  depended on a leaked request. Report it rather than fix it silently (for example, `profile/forms.py` reads
  `context_request.get()` directly).

## MR description requirement (decided by the user)

The draft MR description must contain a section stating, as discussed:

- **What the tests guarantee:**
  - The helper test fails if the reset or the restore breaks.
  - The fixture-active tests fail if the fixture is removed or the conftest import breaks.
  - With the fixture, every test starts without a leaked request, whatever the test order.
- **What they don't guarantee:**
  - Tests that only passed thanks to a leaked request will now fail every time. This is correct, but may surface new
    failures.
  - A future session- or module-scoped fixture that sets a request would be wiped.
  - Fix 2 makes a future leak quiet in tests: only a warning is logged, and nothing breaks.
  - Other global state is not covered.

## Calls made by Claude (open to correction)

- No test-side check that turns the `get_hijacker()` warning into a test failure. Fix 1 prevents the leak, and the
  warning stays in the log.
- The location of the helper module and the exact log message are left to the builder.
