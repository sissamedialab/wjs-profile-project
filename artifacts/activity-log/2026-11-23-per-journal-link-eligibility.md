## 2026-11-23 — Correspondence-author eligibility counts only the fields each journal shows
**What:** Split the shared professional-links entry of `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` into a JCOM/JCOMAL entry and a JCAP entry, and added a startup system check (`wjs_profile.E001`, `wjs/jcom_profile/checks.py`) that keeps the rules consistent with `PROFILE_FIELDS`.
**Why:** After !1587, a JCAP author with only a Facebook handle (or a JCOM author with only a SciX record) was eligible through a field the journal does not show, so they could neither see nor edit it. !1587 left this as a product decision for a separate change (wjs/specs#3392); !1623 had proposed the split and was closed.
**Decisions:** Explicit per-journal entries (plain, greppable data) plus a shared consistency check, rather than deriving one setting from the other. The check runs as a Django system `Error` and in the tests, resolves rules and visible fields with the same `None` fallback as the runtime, and also requires the fields read by validation to equal the fields highlighted in the profile. It reads the effective rules from wjs-submission's settings constant, since `get_required_field_entries` takes a single journal. Labels and help texts stay generic (decision of the !1587 review). Authors who become ineligible are accepted as is: no migration, no whitelisting.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming, writing-plans (inline, plus the earlier issue analysis) | ≈120k | ≈60m |
| Implementation | superpowers:executing-plans (inline, TDD) | ≈150k | ≈35m |
| Review | superpowers:requesting-code-review (opus subagent, whole branch) | ≈109k | ≈1.5m |
| Review | code-eval, doc-sync | ≈25k | ≈10m |

**Considered & dropped:** Deriving `PROFILE_FIELDS` from the rules (a rule typo would make a field visible); deriving the links entry from `PROFILE_FIELDS` (an override made after `defaults/settings.py` would not re-derive); a test-only guard (misses a bad override on a deployed instance); filtering by `PROFILE_FIELDS` at runtime in the validation function (step 4 labels would disagree with validation, and it crosses the wjs-submission dependency boundary). Per-journal named labels from !1623.
**Follow-ups:** The check reads `[None]` eagerly, so a deployment override without a `None` key raises KeyError instead of E001 (deferred minor). Update the "Known limitation" docstring of `is_required_field_entry_satisfied` in wjs-submission-project, and correct the spec's Constraints line about `get_required_field_entries`. Run the functional steps T-A to T-D of #3392 on a test environment. The developer posts the production counts of authors who stop being eligible on the issue. Optionally warn the editorial offices before release (open question on the issue, owner Matteo Gamboz).
**Eval:** 76% — artifacts/evaluations/2026-11-23-per-journal-link-eligibility.md
**Refs:** wjs/specs#3392, wjs/specs#2728, wjs/wjs-profile-project!1587 (merged) and !1623 (closed).
