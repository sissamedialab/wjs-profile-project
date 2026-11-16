# Correspondence-author validation driven by a per-journal required-fields dict

Date: 2026-11-09 (revised after the first approval: direction changed). Language: English.
Repos: `wjs-profile-project` (P), `wjs-submission-project` (S). Both on `feature/issue-2728`.

## Intent

Today there are two parallel mechanisms per journal: boolean `*_correspondence_author_validation`
functions and `*_correspondence_author_missing_fields` functions (added to list missing profile
fields in submission step 4). They encode the same rules twice and the validation functions are
identical in shape. Replace the rules with **one overridable dict of required fields per journal**
(public labels included) and one shared helper that evaluates it. The `*_validation` functions stay as
the injection point (open/closed): a journal needing different logic injects its own function through
the existing validation setting without refactoring anything else.

## Goals

1. S: `CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` (read from `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`
   in Django settings, default `{None: [...]}` = the default rules). Keys are journal codes with a `None`
   fallback; an override replaces the default entirely (so it repeats the `None` key).
2. Entry format, plain data usable in settings:
   `{"label": <lazy str>, "paths": [<dotted attribute path on the user>, ...], "allowed": <optional collection>}`.
   Evaluation: each path is resolved by attribute traversal (callables are called, e.g. `affiliation`).
   Without `allowed`, the entry is satisfied if **any** path value is truthy (this models "name = last
   or first name" and "any of the professional links"). With `allowed`, it is satisfied if **any** path
   value is `in allowed` (no truthiness test; this models "profession is one of PROFESSIONS").
   Unsatisfied entries contribute their `label`, in dict order.
3. S shared helper `missing_required_fields(user, journal) -> list[str]`.
4. Validation functions get the signature `validation(user: Account, journal: Journal) -> bool`
   (`not missing_required_fields(user, journal)`): `default_correspondence_author_validation` in S;
   `jcom_correspondence_author_validation` and `jcap_correspondence_author_validation` stay in P as thin
   functions. `is_user_eligible_for_correspondence_author` = whitelisted, or the journal's validation
   function (looked up in the existing validation setting) passes it `(user, journal)`.
5. Step 4 keeps `get_correspondence_author_missing_fields(journal, user)` (whitelisted: `[]`, else
   `missing_required_fields`). The `*_correspondence_author_missing_fields` functions and the
   `*_MISSING_FIELDS_FUNCTION` settings are removed.
6. **Prove exact equivalence.** Frozen copies in tests of (a) the old boolean validators and (b) the
   current `*_missing_fields` functions (HEAD of the branch) are compared with the new validation and with
   `missing_required_fields` over an exhaustive sweep (see Equivalence test).

## Non-goals / anti-goals

- No change to which fields each journal requires or to label text (current committed labels).
- No per-field validation functions, no change to the profile form (marking fields required is a
  follow-up; the dict's paths/labels are what it will build on).
- Whitelist logic, `ProfileCompletionStatus` and the completion function are untouched.
- Do not relax the equivalence test to make it pass; a difference is a bug in the new code.

## Equivalence test

Reference implementations are copied verbatim into the test modules, with a comment naming the commit
(old validators: `f84bae5c` in P for JCOM/JCAP, `23be0c8^` in S for default; old missing_fields:
the version at HEAD before this work). Stub users are `types.SimpleNamespace` objects exposing only the
attributes the rules read (including `jcomprofile` and a callable `affiliation()`), so the sweep needs no
database. Journal stubs are `SimpleNamespace(code=...)`. Sweep: full cartesian product of is_active,
first_name, last_name, email, biography, profession (None, "", every value in `PROFESSIONS`, and an
out-of-choices value), affiliation/institution, and "no link set / each link alone". Assertions:
`bool(old_validation(user)) == new_validation(user, journal)` and
`[str(l) for l in old_missing(user)] == [str(l) for l in missing_required_fields(user, journal)]`.

## Constraints

- S must not import `wjs.jcom_profile`; P supplies its entries through the S setting (labels via
  `gettext_lazy`, allowed values from `wjs.jcom_profile.constants.PROFESSIONS` at settings load time).
- The injection contract changes (extra `journal` argument) and the old settings disappear: accepted
  breaking change, both repos are ours; note it in each `CHANGELOG.md` (no towncrier).
- A journal that injects a custom validation function should also override its dict entry, else step 4 lists
  fields from the dict that the custom logic may not use.

## Decisions

User's: keep the `*_validation` functions as injection point; shared helper reading an overridable dict
with labels; allowed-values per entry for profession; validation takes `(user, journal)`.
Mine (check): setting name `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`; entries are plain dicts;
the jcom/jcap functions are one-line wrappers (equal to the default) kept on purpose; old references live
only in tests; changelog note instead of towncrier.

## Left to the builder

Test layout/fixture names, helper internals (path traversal), docstring wording, which docs to touch
(grep both repos for the removed names, excluding `.claude/`, `node_modules`).
