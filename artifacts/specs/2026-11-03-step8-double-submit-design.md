# Step-8 double submission: serialize submits and repair affected articles — design

- **Date:** 2026-11-03
- **Issue:** wjs/specs#3233 ("Internal Server Error: /plugins/wjs-submission/submission/4351/8/")
- **Projects:** wjs-submission-project (`wjs/plugins/wjs_submission`), wjs-profile-project (`wjs/plugins/wjs_review`) — one branch and one MR per project
- **Language:** English

## Intent

Two overlapping POSTs to the step-8 "Review & submit" view for the same article (typically a double click on
"Submit" while the slow submission is still running) corrupt the article. Depending on the timing, the second request:

1. raises `RevisionStorage.DoesNotExist` in `AuthorHandleRevision.__post_init__` (the error in the issue), after
   having already committed an outdated full-row save of the article (`SubmissionStep8Form.save()` →
   `ModelForm.save()`) that reverts `Article.stage` to `Under Revision`;
2. silently runs the *first-submission* branch of `CompleteSubmission` (because `is_revision()` is evaluated after the
   first request deleted the `RevisionStorage`): it overwrites `Article.date_submitted`, moves the stage, and re-sends
   the submission acknowledgements;
3. raises `TransitionNotAllowed` (`author_submits_again`) when both requests run the revision handler.

There is no request-level transaction (`ATOMIC_REQUESTS` is off), no lock and no client-side protection.

The full analysis is on the issue. The race and all three outcomes were reproduced with threaded tests through the
real view; the row-lock fix described below was validated against them.

Production data (dump of 2026-11-03), searched with the criteria in "Detection" below: **two** articles were
affected, both by outcome 2 — 3836 (JCOM, published) and 4351 (JCOMAL, accepted). On 4351, the 500 of the issue was
followed 9 s later by a resubmission from the wizard, which ran the first-submission branch. No article is currently
stuck in outcome 1.

## Goals

1. **Serialize step-8 submissions** of the same article on the server, so a second overlapping or late request
   neither fails nor re-runs the submission.
2. **Disable the Submit button** on click in step 8, to reduce double submissions at the source.
3. **A management command** that lists broken articles, explains the problem and the proposed fix for each, and
   repairs only the articles explicitly given to it.

## Non-goals

- No data migration and no unattended/automatic repair.
- No production log analysis: detection uses the database only.
- No correction of already-sent duplicate emails, and no automatic update of published metadata (article page, JATS,
  Crossref) — the command only reports these.
- No change to `AuthorHandleRevision` or `CompleteSubmission` logic beyond what the fix needs.
- No change to other wizard steps.

## Anti-goals

- The command must never modify an article that was not passed to `--repair`, nor one that is no longer detected as
  broken at repair time.
- The lock must not change the behaviour of a single, normal submission (same events, messages, final state).

## Part 1 — wjs-submission-project

### Serialize submissions (`step8/views.py`)

`SubmissionStep8View.post` wraps the whole request in `transaction.atomic()` and, as its first operation, locks the
article row with `select_for_update()` on the view's own queryset (`get_queryset()`, which already restricts to the
user's articles in `Unsubmitted` / `Under revision` of the current journal), filtered by the URL `article_id`.

- The lock must be taken **before** anything reads or saves the article (`_verify_step`, `get_object`,
  `get_form_kwargs`/`is_revision`, `form.save()`), so it lives in `post()`, not in `CompleteSubmission`.
- If the locked query returns no row and the article belongs to the user (owner or correspondence author, same
  journal) but its stage is no longer `Unsubmitted` / `Under revision`, the article has already been submitted: add an
  INFO message ("This submission has already been completed.") and redirect to `wjs_submission_0` for that article
  (the same destination as a successful submission).
- Otherwise (no row and not the user's article, or row locked) continue with the normal `super().post()`: inaccessible
  articles still get the existing 404.

Validated prototype:

```python
def post(self, request, *args, **kwargs):
    with transaction.atomic():
        locked = self.get_queryset().select_for_update().filter(pk=self.kwargs[self.pk_url_kwarg]).first()
        if locked is None and self._is_already_submitted():
            messages.add_message(request, messages.INFO, _("This submission has already been completed."))
            return HttpResponseRedirect(reverse("wjs_submission_0", kwargs={"article_id": self.kwargs[self.pk_url_kwarg]}))
        return super().post(request, *args, **kwargs)
```

Lock duration: production data shows a submission holds the lock for well under a second (e.g. 3836: revision
stage changes within 0.4 s, the second request's re-submission finished 2 s later), far below any worker timeout, so a
plain blocking lock is enough (no `NOWAIT` / `lock_timeout`).

ON_ARTICLE_SUBMITTED side effects of outcome 2, checked in the code: `sync_article_articleworkflow` is a no-op (it
only acts on `IncompleteSubmission`), so the workflow state, editor assignments and review rounds are untouched; the
lasting effects are `date_submitted`, the stage, the duplicate notifications and the Prophy re-upload.

PostgreSQL behaviour relied upon: a request waiting on `SELECT … FOR UPDATE` re-evaluates the `WHERE` clause on the
committed row once the lock is released, so the stage filter excludes an article the first request has submitted.
`get_queryset()` must not use `distinct()` (incompatible with `FOR UPDATE`); today it does not.

### Disable the Submit button (step-8 template)

On form submit, disable `#submit-btn` in the step-8 page (`templates/wjs_submission/step8/article_form.html`, `js`
block). Re-enable it on `pageshow` when the page is restored from the browser back/forward cache, so that going back
to the page does not leave a dead button. This is a UX measure only; correctness comes from the lock.

### Tests (this repo's CI has no `wjs_review`)

In `tests/` (pytest, transactional where needed, `raise_event` patched as existing step-8 tests do):

- a late POST for an already-submitted article redirects to `wjs_submission_0` with the INFO message, and does not
  re-run the submission (no `CompleteSubmission`, `date_submitted` unchanged);
- a POST for an article the user cannot access still gets 404;
- the lock serializes two concurrent first-submission POSTs: the second waits for the first, then is redirected
  without side effects (single `ON_ARTICLE_SUBMITTED`, `date_submitted` set once).

## Part 2 — wjs-profile-project

### Race regression tests (`wjs_review/tests/`)

Port the validated reproduction: two threads (`django_db(transaction=True)`, one DB connection each) POST to the real
`SubmissionStep8View` for an article with a `RevisionStorage` (confirm-flow revision of `editor_revision`, with
`access_mode_oa`). Request B is paused at a controlled point by wrapping `SubmissionStep8View.get_form_kwargs`:

- B paused after `get_form_kwargs` (outcome 1) and before it (outcome 2): with the lock, A must be blocked while B
  holds the lock, and both end with a redirect; final state equals a single submission (revision completed,
  `RevisionStorage` gone, stage `Assigned`, `date_submitted` unchanged, no extra "submitted" acknowledgement);
- two POSTs released together by a barrier (outcome 3), same expectations, repeated a few times;
- late sequential resubmission is redirected.

These tests pass only with a wjs-submission release that contains Part 1 (wjs-profile depends on
`wjs-submission >= 2.1.7`): see "Constraints".

### Management command `wjs_review/management/commands/<name>.py`

Called without arguments: **report only**, never writes. Called with `--repair ID [ID …]`: repairs the listed
articles only.

#### Detection (database only, no message subjects — these come from per-journal settings)

- **A — Re-submitted after a revision request** (outcome 2): `Article.date_submitted` is later than the
  `date_requested` of the article's first revision request (`review.RevisionRequest`). On production this matches
  exactly 3836 and 4351.
  - *Proposed fix:* set `date_submitted` to the `date_time` of the article's first `submission.ArticleStageLog` entry
    whose `stage_from` is `Unsubmitted` (the original submission; on production it matches the original submission
    to within a second). If there is no such entry, report "no reliable original date: manual fix" and do not repair.
- **B — Stage inconsistent with the workflow** (outcome 1, and the stage left by outcome 2): `ArticleWorkflow.state` is
  `EditorSelected`, the article has no open revision request (`date_completed` null), and `Article.stage` is neither
  `Assigned` nor `Under review` (e.g. `Under Revision` reverted by outcome 1, or `wjs_review_plugin` / `Unassigned`
  left by outcome 2). On production every `EditorSelected` article is `Assigned` or `Under review`, so this matches
  nothing today.
  - *Proposed fix:* set the stage to `Under review` if the workflow has pending reviews in the latest round
    (`ArticleWorkflow.objects.with_pending_reviews()`), else `Assigned` — the same rule as
    `restart_review_process_after_revision_submission`.
  - `Under Revision` with no open revision request and any other workflow state: report as "needs manual inspection",
    no repair. (Articles withdrawn during a revision keep an open revision request and are therefore not matched.)

An article can match both A and B; each problem is reported and repaired independently.

#### Report

For each broken article: id, journal code, title, current stage and workflow state; for each detected problem, what
went wrong, the evidence (the dates/rows that matched) and the exact change `--repair` would make (field, current
value, new value). Warnings, never repaired:

- outcome 2 sent the submission acknowledgements again to the authors (and the co-authors), re-uploaded the article
  to Prophy, and may have re-sent the EO access-mode special-request notification;
- the article is published (`date_published` set): its public metadata may carry the wrong submission date and must
  be checked by hand.

End with the count of broken articles and the `--repair` command line for the repairable ones.

#### Repair

- `--repair` accepts one or more article ids.
- For each id: re-run detection on that article; if it is not currently detected as broken, print why and skip it.
- Each article is repaired in its own transaction; print each change made (old → new value).
- The change is saved without triggering the submission events or the workflow (plain field update).
- Trace: the console output plus a `logger` line per change (article, field, old → new); no records are added inside
  the application (no messages, notes or extra log entries).
- Exit with a non-zero status if any requested id was skipped or failed.

#### Tests

- detection: positive and negative cases for A and B (including "no stage log" and "other workflow state");
- report mode writes nothing;
- `--repair` fixes only the given ids, skips ids not detected as broken, and is idempotent (a second run finds
  nothing).

## Constraints

- Two MRs, **sequential** (decided): the wjs-submission MR is merged and released first; then the wjs-profile-project
  MR carries the command and the race tests and raises the minimum `wjs-submission` version in `setup.cfg` to that
  release. No skipped or gated tests. The command therefore ships with the second MR.
- Follow each repo's `.claude/rules/` (ruff/linting, Django style, template formatting, tests).
- PostgreSQL only (row locks): this is what all environments use.

## Left to the builder

- The command name (e.g. `report_double_submissions`) and the exact report wording/format.
- How the paused-thread harness is structured, as long as it exercises the real view.
- Whether `_is_already_submitted` is a method or inlined.
