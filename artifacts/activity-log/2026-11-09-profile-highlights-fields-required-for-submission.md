## 2026-11-09 — Profile highlights the fields required for submission
**What:** When an author reaches the profile from the submission ("Back to submission"), the profile pages mark the fields the journal requires (bold Required help text), show a banner listing what is still missing, and show count badges on the profile tabs. Computed per request from the required-fields dict of part 1; entries carry an optional `profile` hint (tab url name and form field names).
**Why:** An author blocked at step 4 had to guess which fields and tabs mattered. Other profile users (editors, reviewers, EO, authors not coming from submission) must not see fields that are irrelevant to them.
**Decisions:** Banner + badge (mockup A), no orange field outline; Required follows wjs-submission's `required_help_text` pattern and stays on required fields filled or not, while banner/badges track only the missing ones. Explicit `profile` hint on dict entries instead of deriving it from attribute paths. Whitelisted users and entries without a hint (Active account) are never shown. "At least one of" groups count 1 and say so on optional fields only (a review finding: `last_name` is always required by the form). Bootstrap classes instead of new CSS because the profile CSS lives in wjs-themes (outside the approved repos). wjs-submission got three public helpers; the profile code does not use private functions. CHANGELOG.md is release-generated, not edited.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming + visual companion + writing-plans | ≈70k | ≈35m |
| Implementation | superpowers:executing-plans (inline) | ≈160k | ≈45m |
| Review | superpowers:requesting-code-review (opus subagent) | ≈101k | ≈2m |
| Review | nephila code-eval, doc-sync | ≈15k | ≈5m |

**Considered & dropped:** Orange field outline (rejected by the user); showing highlights always on the profile (irrelevant to other roles); tying the highlight to a journal-prefixed `next` (breaks journals served on their own domain, where URLs have no code prefix).
**Follow-ups:** Session `next` is not bound to a journal and persists until "Back to submission" is clicked, so a stale `next` shows the highlights later or on another journal (inherited from the back-link mechanism). Minors deferred: memoize requirements per request (computed twice on form pages), reuse `safe_back_to_submission_url` in `ProfileBackToSubmissionView`, `context.get("request")` in the tag, derive the links `fields` from `paths`, broaden the "no next" test to password/keywords/assignment pages and editor/reviewer/EO users. Six `test_set_notify_hijack_normal_user` failures appear when both repos' suites run in one pytest session; they also fail on the clean branch.
**Eval:** 75% — artifacts/evaluations/2026-11-09-profile-required-fields-highlight.md
**Refs:** branch feature/issue-2728-submission-required-highlight (stacked on feature/issue-2728); spec `artifacts/specs/2026-11-09-profile-required-fields-highlight-design.md`, plan `artifacts/plans/2026-11-09-profile-required-fields-highlight.md`.
