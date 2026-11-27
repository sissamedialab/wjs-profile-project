# Per-journal professional links in correspondence-author eligibility

Issue: wjs/specs#3392 (main). Related: wjs/specs#2728, wjs/specs#2918 (closed, merged as !1477), wjs/wjs-profile-project!1587 (merged), !1623 (closed, reference only).

Language: English (confirmed).

The functional spec, Expected behaviour (A to D) and functional test steps live in the issue description; this document
records only the design decisions for the implementation.

## Intent and why

JCOM, JCOMAL and JCAP share one "professional links" entry in `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`
(`wjs/defaults/settings.py`) that counts all seven link fields, while each journal shows only some of them
(`PROFILE_FIELDS`). A JCAP author with only a Facebook handle, or a JCOM author with only a SciX record, is eligible
through a field they cannot see or edit. Eligibility must depend only on fields the journal shows.

## Goals, non-goals, anti-goals

Goals:
- The links entry of each journal lists only that journal's visible link fields (JCOM/JCOMAL: `facebook`, `twitter`,
  `linkedin`, `records_other`; JCAP: `records_arxiv`, `records_inspire`, `records_scix`, `records_other`).
- A check, run at startup (Django system check, `Error`) and in the test suite, keeps the rules and `PROFILE_FIELDS`
  consistent for every journal.

Non-goals (decided in the issue):
- No change to labels or help texts (generic text from !1587 stays).
- No migration, notification or whitelisting of authors who become ineligible.
- No change to the validation-function injection points, to wjs-submission logic, or to `PROFILE_FIELDS`.

Anti-goals: a check so strict that it rejects legitimate rules (entries without a profile hint, such as active account
and affiliation, must be skipped); any change to whitelist behaviour.

## Constraints

- wjs-submission must not depend on `wjs.jcom_profile` (specs #30, #3033): the check lives in wjs-profile-project and
  reads the effective rules from wjs-submission's settings constant
  (`plugins.wjs_submission.settings.CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`), because `get_required_field_entries`
  takes a single journal and cannot list all codes; it applies the same `None` fallback.
- A `jcomprofile.*` path stays first in each links entry, so a user without a JCOMProfile still raises.
- Translatable labels are unchanged (`PROFESSIONAL_LINKS_LABEL`, `PROFESSIONAL_LINKS_GROUP_HELP`).

## Approaches

1. **Explicit per-journal entries + shared check (chosen).** Data stays plain and greppable; the check catches drift,
   including project-level overrides at startup. Picked by the product owner after comparing the options below.
2. Explicit entries + test only: cheaper, but a bad override on a deployed instance goes unnoticed.
3. Derive the links entry from `PROFILE_FIELDS`: less duplication, but an override of `PROFILE_FIELDS` made after
   `defaults/settings.py` is evaluated would not re-derive, so it needs the check anyway.
4. Derive `PROFILE_FIELDS` from the rules: a rule typo would make a field visible and change article-page display.
5. Filter at runtime by `PROFILE_FIELDS` in the validation function: the wjs-submission step 4 list would disagree
   with the validation result, and it crosses the dependency boundary.

## Design

**Entries.** Replace `_PROFESSIONAL_LINKS_ENTRY` with `_JCOM_PROFESSIONAL_LINKS_ENTRY` (used by `_JCOM_REQUIRED_FIELDS`,
so JCOM and JCOMAL) and `_JCAP_PROFESSIONAL_LINKS_ENTRY` (used by the JCAP list). `paths` and `profile.fields` list
the same fields. Remove the "Accepted limitation" comment above the entry.

**Check.** A pure function in a new module `wjs/jcom_profile/checks.py` takes the rules and `PROFILE_FIELDS` as
arguments (so tests can feed bad data) and returns a list of messages. For every journal code present in either dict
(including `None`), it resolves the rules with the wjs-submission fallback (`rules.get(code, rules[None])`) and the
visible fields with the `is_field_available` fallback (`PROFILE_FIELDS.get(code, PROFILE_FIELDS[None])`). For every
entry with a non-empty `profile.fields` it reports: a field not shown by the journal; and a set of `paths` leaf
names (last dotted segment) different from `profile.fields`. The opposite direction (a visible field no rule
requires) is not checked. A thin registered check calls it with the live settings and returns
`django.core.checks.Error` items; it is registered from `JCOMProfileConfig.ready()`.

**Tests.** Update the frozen references and equivalence sweeps of `test_account_validation.py` to the per-journal
rules; add the per-journal pass/fail cases; add tests for the check function (shipped settings pass; a hidden field,
a paths/fields mismatch and a fallback journal such as JQuant fail) and one for the registered check.

## Left to the builder

- Exact names of the new constants, the check ids and the error message wording.
- Whether the extra profile-form test is needed or already covered by `test_profile_required_fields.py`.
- The wjs-submission docstring cleanup ("Known limitation" in `is_required_field_entry_satisfied`) is out of this
  repo: not part of this change unless separately approved.
