# Step-8 double submission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize step-8 submissions of the same article, disable the Submit button on click, and add a management
command that reports and (on request) repairs articles damaged by past double submissions.

**Architecture:** Part 1 (wjs-submission-project) locks the article row with `select_for_update()` at the start of
`SubmissionStep8View.post`, inside one `transaction.atomic()`, and redirects already-submitted articles to the status
page. Part 2 (wjs-profile-project) adds threaded race regression tests through the real view and a
`report_double_submissions` command in `wjs_review` that detects damage from database data only.

**Tech Stack:** Django, PostgreSQL row locks, pytest / pytest-django (`transaction=True` + threads), vanilla JS.

**Spec:** `artifacts/specs/2026-11-03-step8-double-submit-design.md` (same file in both repos).

## Global Constraints

- Two branches `bugfix/issue-3233-step8-double-submit`, two MRs, **sequential**: wjs-submission is merged and released
  first; the wjs-profile MR then raises `wjs-submission >= …` in `setup.cfg` to that release. No skipped/gated tests.
- No data migration; nothing repairs data unless `--repair ID [ID …]` is given explicitly.
- Detection uses database data only — never message subjects (they come from per-journal settings).
- A single, normal submission must keep its current behaviour (same events, messages, final state).
- Follow each repo's `.claude/rules/` (ruff + ruff-format, Django/Python style, djlint for templates, tests).
- Documentation and code comments in English.
- Running tests locally (from `janeway/src`; the local venv lacks setuptools, so disable the broken freezegun plugin):
  - wjs-submission: `pytest -p no:freezegun ../../wjs-submission-project/tests/<file>`
  - wjs-profile: `pytest -c ../../wjs-profile-project/pytest.ini -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/<file>`
  - `janeway/src/plugins/wjs_submission` and `wjs_review` are symlinks to the two working trees, so wjs-profile tests
    run against the wjs-submission branch code.

## Review Focus

1. A user who is a co-author but neither owner nor correspondence author POSTs step 8 → still 404, never the
   "already completed" redirect (pinned in Task 1).
2. A POST on an already-submitted article of **another journal** → 404 (pinned in Task 1).
3. A `ValidationError` raised inside the submission (missing submission requirements) still re-renders the form with
   the error, now inside the outer transaction (pinned in Task 1).
4. Going back to step 8 from the browser after submitting must not leave a permanently disabled button (`pageshow`
   with `event.persisted`) (Task 2, manual/Playwright check).
5. `--repair` with an id that does not exist or is not broken → skipped with a message, other ids still processed,
   non-zero exit (pinned in Task 5).

---

## Part 1 — wjs-submission-project

### Task 1: Serialize step-8 POSTs

**Files:**
- Modify: `wjs/plugins/wjs_submission/step8/views.py` (imports; new methods on `SubmissionStep8View`)
- Test: `tests/test_step8_double_submit.py` (new)

**Interfaces:**
- Produces: `SubmissionStep8View.post(request, *args, **kwargs)` (locks, then `super().post`);
  `SubmissionStep8View._is_already_submitted() -> bool`.
- Copy pinned by the spec: INFO message `"This submission has already been completed."`; redirect to
  `reverse("wjs_submission_0", kwargs={"article_id": <id>})`.

- [ ] **Step 1: Write the failing tests** in `tests/test_step8_double_submit.py`. Set the article's
  `submission_data.access_mode` as `test_submission_step8_access_mode_verify` does; patch
  `events.logic.Events.raise_event` (wjs_review is not installed in this repo's CI) and use the mock to count
  `ON_ARTICLE_SUBMITTED`.
  - `test_late_resubmit_redirects_to_status_page`: POST step 8 twice with `client.force_login(article.owner)`.
    Second response: 302 to `wjs_submission_0` for the article; messages contain
    `"This submission has already been completed."`; `ON_ARTICLE_SUBMITTED` raised exactly once; `date_submitted`
    unchanged after the second POST.
  - `test_resubmit_by_coauthor_is_404`: after the first POST, the co-author (not owner nor correspondence author)
    POSTs → 404.
  - `test_resubmit_other_journal_is_404`: submitted article, POST from a request on another journal → 404.
  - `test_validation_error_rerenders_form`: `CompleteSubmission.run` patched to raise `ValidationError` →
    200, form error shown, article still `Unsubmitted` (patch `render_to_response` as existing step-8 tests do).
  - `test_concurrent_first_submissions_are_serialized` (`@pytest.mark.django_db(transaction=True)`): two threads, each
    calling `SubmissionStep8View.as_view()` with its own `create_rich_fake_request(journal, settings, user=owner)`
    (`method="POST"`) and closing its DB connection at the end. B is paused inside a wrapper around
    `SubmissionStep8View.get_form_kwargs` (after the original returns) until released; A is started while B is
    paused. Assert: A is still alive 3 s after start (blocked on the lock); after releasing B, both return 302,
    `ON_ARTICLE_SUBMITTED` raised exactly once, `date_submitted` set once.

- [ ] **Step 2: Run them, verify they fail**
  Run: `pytest -p no:freezegun ../../wjs-submission-project/tests/test_step8_double_submit.py -v`
  Expected: late-resubmit → 404 instead of 302; concurrent → A not blocked / event raised twice. The 404 and
  validation tests may already pass (they pin existing behaviour).

- [ ] **Step 3: Implement** `post()` and `_is_already_submitted()` in `step8/views.py` as in the spec's validated
  prototype: `with transaction.atomic():` → `self.get_queryset().select_for_update().filter(pk=...).first()`; if
  `None` and `_is_already_submitted()` → INFO message + redirect; else `return super().post(...)`.
  `_is_already_submitted()`: article with that pk, `journal=self.request.journal`,
  `Q(owner=user) | Q(correspondence_author=user)`, stage not in `(STAGE_UNSUBMITTED, STAGE_UNDER_REVISION)`.
  Docstring on `post` explains why (overlapping POSTs, specs#3233).

- [ ] **Step 4: Run the new tests and the existing step-8/view/form tests**
  Run: `pytest -p no:freezegun ../../wjs-submission-project/tests/test_step8_double_submit.py ../../wjs-submission-project/tests/test_views.py ../../wjs-submission-project/tests/test_forms.py -v`
  Expected: all PASS.

- [ ] **Step 5: Commit** (per the commit strategy chosen) — `fix(step8): serialize concurrent submissions of the same article`

### Task 2: Disable the Submit button on submit

**Files:**
- Modify: `wjs/plugins/wjs_submission/templates/wjs_submission/step8/article_form.html` (`{% block js %}`)

**Interfaces:**
- Consumes: the existing `#submit-btn` button(s) in the step-8 template and their enclosing `<form>`.

- [ ] **Step 1: Implement** a small inline script in the `js` block: on the `submit` event of
  `document.getElementById("submit-btn")?.form`, set the button `disabled = true`; on `window` `pageshow` with
  `event.persisted`, set it back to `false`. No-op when the button is absent (invalid revision data shows no button).
- [ ] **Step 2: Lint** — `djlint --profile=django` on the template; expected: no new issues.
- [ ] **Step 3: Verify** in the browser per the Playwright mode chosen at flow start: click Submit → button disabled
  and one request sent; browser Back to step 8 → button enabled.
- [ ] **Step 4: Commit** — `fix(step8): disable the submit button while the submission is in progress`

## Part 2 — wjs-profile-project

### Task 3: Race regression tests through the real view

**Files:**
- Test: `wjs/plugins/wjs_review/tests/test_step8_double_submit.py` (new)

**Interfaces:**
- Consumes: Task 1's `SubmissionStep8View.post` behaviour (via the symlinked wjs_submission working tree).
- Fixtures: `editor_revision`, `access_mode_oa`, `settings`; revision set up with
  `RevisionStartConfirmView()._init_revision_flow(article_id=...)` and `RevisionStorage.data` updated with
  `{"comments_editor": "author note", "submission_requirements": True}`.

- [ ] **Step 1: Write the tests** (port the validated harness; all `@pytest.mark.django_db(transaction=True)`). A helper
  snapshots stage, workflow state, `date_submitted`, review-round and editor-assignment counts, and the article's
  `Message` subjects.
  - `test_race_is_serialized[after_form_kwargs|before_form_kwargs]`: B paused at that point; A must be blocked; both
    return 302 to `wjs_submission_0`; revision `date_completed` set; no `RevisionStorage`; stage `Assigned`;
    `date_submitted` unchanged; no message with the article's submission-acknowledgement subject (render it from the
    `subject_submission_acknowledgement` setting rather than hard-coding).
  - `test_free_race_double_submit[0..2]`: two threads released by a `threading.Barrier`; same final-state assertions.
  - `test_sequential_resubmit_redirects`: second POST → 302 with `"already been completed"` in messages.
  - `test_single_submit_state_is_unchanged`: one POST; same final state as above (guards the "single submission
    unchanged" constraint).
- [ ] **Step 2: Run** with the wjs-submission branch checked out: all PASS. Sanity check once: with the Task 1
  change stashed in wjs-submission, the race tests FAIL (A not blocked / `RevisionStorage.DoesNotExist` /
  `TransitionNotAllowed`); restore it.
  Run: `pytest -c ../../wjs-profile-project/pytest.ini -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_step8_double_submit.py -v`
- [ ] **Step 3: Commit** — `test(review): cover concurrent step-8 revision submissions`

### Task 4: `report_double_submissions` — detection and report

**Files:**
- Create: `wjs/plugins/wjs_review/management/commands/report_double_submissions.py`
- Test: `wjs/plugins/wjs_review/tests/test_report_double_submissions.py` (new)

**Interfaces:**
- Produces, in the command module:
  - `@dataclass(frozen=True) class Problem`: `code: str` (`"resubmitted"`, `"stage_inconsistent"`,
    `"stage_manual"`), `description: str`, `evidence: str`, `field: str | None`, `current: Any`,
    `proposed: Any` (`None` ⇒ not repairable), `warnings: tuple[str, ...]`.
  - `candidate_articles() -> QuerySet[Article]`: union of the A and B pre-filters below.
  - `find_problems(article: Article) -> list[Problem]`.
- Detection (spec "Detection"):
  - **A** `resubmitted`: `date_submitted > Min("revisionrequest__date_requested")`. Proposed value: `date_time` of the
    first `ArticleStageLog` with `stage_from=STAGE_UNSUBMITTED` (order by `date_time`); none ⇒ `proposed=None` and
    description "no reliable original date: manual fix". Warnings: acknowledgements re-sent to authors and
    co-authors, Prophy re-upload, possible EO access-mode notification; plus "published: check public metadata" when
    `date_published` is set.
  - **B** `stage_inconsistent`: `articleworkflow__state == EDITOR_SELECTED`, no `revisionrequest` with
    `date_completed__isnull=True`, stage not in `(STAGE_ASSIGNED, STAGE_UNDER_REVIEW)`. Proposed:
    `STAGE_UNDER_REVIEW` if `ArticleWorkflow.objects.with_pending_reviews().filter(article=article).exists()` else
    `STAGE_ASSIGNED`.
  - `stage_manual`: stage `STAGE_UNDER_REVISION`, no open revision request, workflow state ≠ `EDITOR_SELECTED`;
    `proposed=None`, "needs manual inspection".

- [ ] **Step 1: Write the failing tests** (build states directly on `editor_revision` / `assigned_article`):
  - A positive: `date_submitted` moved after the revision request, stage log with the original date → one
    `resubmitted` problem, `proposed` equals the stage-log `date_time`.
  - A without an `Unsubmitted` stage-log row → `proposed is None`.
  - A on a published article → warning mentions public metadata.
  - B positive (`Under Revision`, revision completed, `EditorSelected`, no pending reviews) → `proposed == STAGE_ASSIGNED`;
    with a pending review in the latest round → `STAGE_UNDER_REVIEW`; stage `wjs_review_plugin` → also detected.
  - `stage_manual`: stage `Under Revision`, all revision requests completed, workflow state `ToBeRevised` → one
    `stage_manual` problem with `proposed is None`.
  - Negatives: a genuine article under revision (open request) and a normal assigned article → no problems.
  - `test_report_mode_writes_nothing`: `call_command("report_double_submissions", stdout=out)` → output contains the
    article id, journal code, each problem's current/proposed value and the `--repair <ids>` line; no field changed
    (compare a snapshot before/after).
- [ ] **Step 2: Run, verify failures** (`ModuleNotFoundError` / command not found).
  Run: `pytest -c ../../wjs-profile-project/pytest.ini -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_report_double_submissions.py -v`
- [ ] **Step 3: Implement** `Problem`, `candidate_articles()`, `find_problems()` and the report path of
  `Command.handle` (no arguments). Report per article: id, journal code, title, stage, workflow state, then per
  problem description, evidence, `field: current → proposed` (or the manual note), warnings; final line with the
  count and the `--repair` command for repairable ids. `help` text explains both modes.
- [ ] **Step 4: Run tests** — expected PASS.
- [ ] **Step 5: Commit** — `feat(review): add report_double_submissions command (report mode)`

### Task 5: `report_double_submissions --repair ID [ID …]`

**Files:**
- Modify: `wjs/plugins/wjs_review/management/commands/report_double_submissions.py`
- Test: `wjs/plugins/wjs_review/tests/test_report_double_submissions.py`

**Interfaces:**
- Consumes: Task 4's `find_problems(article) -> list[Problem]`.
- CLI: `--repair` with `nargs="+"`, `type=int`.

- [ ] **Step 1: Write the failing tests:**
  - `--repair <id>` on an A+B article → `date_submitted` and stage set to the proposed values; output shows
    old → new for each field; a second report run finds nothing (idempotent).
  - Only listed ids are touched: two broken articles, repair one → the other unchanged.
  - Not-broken id, non-existent id and a non-repairable (`proposed=None`) case → skipped with a reason, the other ids
    still repaired, `CommandError` (non-zero exit) raised at the end.
  - Repair does not raise events or add `Message` / `ArticleStageLog` rows (counts unchanged).
- [ ] **Step 2: Run, verify failures.**
- [ ] **Step 3: Implement**: for each id, load the article, re-run `find_problems`; skip with reason if none or none
  repairable; otherwise in `transaction.atomic()` apply each repairable problem with
  `Article.objects.filter(pk=...).update(<field>=<proposed>)`, print and `logger.info` `article, field, old → new`.
  Collect skipped ids and raise `CommandError` listing them after processing all ids.
- [ ] **Step 4: Run all command tests** — expected PASS.
- [ ] **Step 5: Verify against the restored production dump (read-only report):** run
  `python manage.py report_double_submissions` against the local `janeway` DB (production dump of 2026-11-03).
  Expected: exactly 3836 and 4351 reported as `resubmitted`, proposed dates 2026-02-11 13:44:21 and
  2026-08-11 14:56:41 (Europe/Rome), 3836 with the published-metadata warning; no `stage_inconsistent`. Do **not**
  run `--repair` there.
- [ ] **Step 6: Commit** — `feat(review): add --repair to report_double_submissions`

### Task 6: Require the wjs-submission release with the fix (after that release)

**Files:**
- Modify: `setup.cfg` (`wjs-submission >= 2.1.7` → `>= <released version>`)

- [ ] **Step 1:** Blocked until the wjs-submission MR is merged and released; the version is provided by whoever cuts
  the release. Update the requirement, reinstall, rerun Task 3's tests.
- [ ] **Step 2: Commit** — `build: require wjs-submission with serialized step-8 submissions`
