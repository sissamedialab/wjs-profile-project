## 2026-10-13 — Drop the duplicate "Issues after acceptance" EO notification
**What:** When the acceptance checks hold a paper in Accepted, `VerifyProductionRequirements` no longer sends its generic "Issues after acceptance - article N not ready for typesetters." message: the check function that held the paper notifies the EO (for JCAP TA papers: "[JCAP] Action required: confirm production readiness…"). Two regression tests in `test_ready_for_typesetter_timeline.py`.
**Why:** specs#3176 (JCAP go-live feedback #3162): the EO got two mails on acceptance of every JCAP TA paper. QA still saw both on dev on 2026-10-07 because nothing had fixed it yet: MR !1543 (#3174) only mentioned this issue.
**Decisions:**
- Check functions own their EO notification; the contract is documented in `_perform_checks`, not enforced. Today only the JCAP check can hold a paper.
- Kept clear of the open !1556 (#3131), which rewrites and renames `jcap_ta_not_yet_confirmed`: no change to `checks_after_acceptance.py` or settings, no new test module that !1556 also adds, tests read the JCAP checks from `settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]` and the docstring doesn't name the function. Branch/MR survey found no other overlap (!1564, !1559, !1554 touch distant hunks).
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming + MR/branch overlap analysis (inline) | ≈90k | ≈20m |
| Design | superpowers:writing-plans (inline) | ≈25k | ≈8m |
| Implementation | superpowers:executing-plans (inline, TDD) | ≈45k | ≈20m |
| Review | code-reviewer subagent (opus) | ≈77k | ≈1m |
| Review | code-eval + doc-sync (inline) | ≈10k | ≈4m |

**Considered & dropped:** keeping the generic message as a fallback only when no check notified the EO (machinery for a case that doesn't exist today).
**Follow-ups:**
- Whichever of this MR and !1556 merges second: re-run `test_ready_for_typesetter_timeline.py`.
- Gotchas: the janeway pyenv had a stale non-editable `wjs.jcom_profile` (fixtures failed with `NoReverseMatch: wjs_article_messages`); fixed with `pip install -e . --no-deps`. `pytest -n7` hits Postgres `max_locks_per_transaction` while creating the test DBs: run serially. Six `test_production.py` galley/publication tests fail on a clean `wjs-develop` too.
- Deferred review minors: the first new test keys on the subject prefix (the count test covers it); `docs/superpowers/specs/2026-09-02-iop-xml-export-design.md:225` still cites the removed method (historical).
**Refs:** wjs/specs#3176, #3162, #3174, MR !1556; spec `artifacts/specs/2026-10-13-drop-acceptance-issues-notification-design.md`; plan `artifacts/plans/2026-10-13-drop-acceptance-issues-notification.md`.
Eval: 87% — artifacts/evaluations/2026-10-13-drop-acceptance-issues-notification.md
