## 2026-11-13 — Reset the global request between tests; get_hijacker tolerates a missing hijacker

**What:**
- An autouse fixture, `clear_global_request`, now resets the request stored by Janeway's `GlobalRequestMiddleware`
  around every test in `jcom_profile` and `wjs_review`.
- `get_hijacker()` now returns `None` and logs a warning when the hijacker account in `hijack_history` no longer
  exists.

**Why:** `test_staff_disable_notification` failed at random in CI.
- The request lives in a ContextVar (`utils.middleware.context_request`) that nothing resets.
- A hijacked request left behind by `test_hijack_notifications[True-*]` made `get_hijacker()` look up a rolled-back
  account. `send_staff_assignment_change()` swallowed the resulting `DoesNotExist`, so no email was sent.

**Decisions:**
- **Reset logic in a helper.** It lives in `isolated_global_request()` (`wjs/jcom_profile/tests/global_request.py`),
  so it can be tested deterministically. The fixture is only a wrapper around it.
- **Fixture-active check.** One test per package asserts that the fixture is in `request.fixturenames`. Together with
  the helper test, this covers the fix without relying on test order.
- **No test-side failure on the warning.** The `get_hijacker()` warning is not turned into a test failure, by the
  user's choice: fix 1 prevents the leak, but fix 2 makes a future leak quiet in tests.

**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming (inline) | ≈25k | ≈10m |
| Design | superpowers:writing-plans (inline) | ≈10k | ≈4m |
| Implementation | superpowers:executing-plans (inline) | ≈40k | ≈50m (38m full-suite run) |
| Review | code reviewer subagent (opus) | ≈77k | ≈1m |
| Review | code-eval, doc-sync (inline) | ≈8k | ≈3m |

**Considered & dropped:**
- **Two tests next to each other** (one sets a request, the next checks it's gone): they only work when xdist puts
  them on the same worker.
- **`xdist_group`:** needs `--dist loadgroup` for the whole suite.
- **A nested `pytester` session:** too heavy, and could break on its own because of pytest-django and the large
  conftest.
- **Cleanup only in `test_hijack_notifications`, retries, or flaky markers:** they hide the problem rather than fix it.

**Follow-ups:**
- The MR description must say what the tests guarantee and what they don't (the user's requirement).
- Local environment issues, unrelated to this change:
  - The janeway virtualenv lacked `setuptools` (`pytest_freezegun` needs `distutils` on Python 3.13). It is now
    installed.
  - `opentelemetry` is missing, so `test_tracing*` fails.
  - The installed wjs-submission lacks `ArticleSubmission.cas_url`, so `wjs_review/test_logic.py` fails.
  - `--create-db -n7` runs out of Postgres shared memory (`max_locks_per_transaction`). `-n3` works.
- Deferred minors:
  - The tests repeat a `process_request` call that the `fake_request` fixture already makes.
  - `views.py:893` calls `get_hijacker()` twice, so a missing hijacker logs the warning twice.

**Eval:** 84% — artifacts/evaluations/2026-11-13-global-request-leak.md

**Refs:**
- Issue: wjs/wjs-profile-project#302
- Spec: artifacts/specs/2026-11-13-global-request-leak-design.md
- Plan: artifacts/plans/2026-11-13-global-request-leak.md
