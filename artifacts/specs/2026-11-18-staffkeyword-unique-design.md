# StaffKeyword: unique (parameters, keyword) — design

Issue: wjs/specs#3436 (related: wjs/specs#2316, JCAP import). Language: English.

## Intent and why
During the JCAP import, unexpected upstream data linked some editors to the same keyword twice.
`StaffKeyword` (`wjs.jcom_profile.models`) has no uniqueness on `(parameters, keyword)`, so the database accepts it.
Duplicates are harmful, not just untidy:
- `users._keyword_score_expression` sums `weight × article_weight` over all rows, so a duplicate counts twice in the
  editor's `keyword_score` and skews automatic assignment ranking.
- `get_shared_keywords` lists the keyword twice.
- `StaffWorkloadParametersForm.save()` uses `get_or_create`, which raises `MultipleObjectsReturned` on duplicates.

## Goals
- The database refuses a second `StaffKeyword` for the same `(parameters, keyword)`.
- The wjapp importers never fail with `IntegrityError` because of duplicate keywords in the source data.

## Non-goals
- Cleaning existing duplicates: current data is already clean (confirmed by the owner). No dedupe in the migration and
  no management command.
- Changing the ranking formula or the keyword weight cap.

## Anti-goals
- A migration that silently deletes rows. If a database still has duplicates, the migration must fail loudly.

## Approaches
- A. Dedupe inside the migration, then add the constraint. Rejected: deletes rows during a deploy for a case that
  does not exist in current data.
- B. Constraint migration only (picked). Smallest change; fails loudly if duplicates exist. A dedupe command was
  considered and dropped as dead code, since the data is clean.
- C. Code guards only, no constraint. Rejected: other write paths (admin, new code) could still create duplicates.

## Design (my calls unless marked)
- `StaffKeyword.Meta` (new): `verbose_name`, `verbose_name_plural`, `ordering = ["parameters", "keyword"]` per the
  repo's model rules (my call), and
  `constraints = [UniqueConstraint(fields=["parameters", "keyword"], name="unique_staffkeyword_parameters_keyword")]`
  (my call: `UniqueConstraint` rather than the legacy `unique_together`).
- Migration `jcom_profile/0046`: `AlterModelOptions` + `AddConstraint`. No data step. Reverse is the default.
- `import_editor_keywords.py`: drop the "duplicates found, left as-is" branch. If the source lists the same keyword
  twice for an editor, merge into one row keeping the highest weight and log a warning.
- `import_logic.py` (editor parameters import, delete-then-create): same merge before creating rows.
- `StaffWorkloadParametersForm.save()` keeps `get_or_create` (safe once duplicates cannot exist). The weights formset
  and admin need no change.

## Tests (TDD)
- Creating a second `StaffKeyword` for the same `(parameters, keyword)` raises `IntegrityError`.
- Same keyword with a different `parameters` is still allowed.
- Both importers merge duplicate source keywords, keep the highest weight, log a warning, and do not raise.
- The existing deactivated-keywords and import tests keep passing.

## Left to the builder
- Exact warning wording, and the helper used to merge duplicates in the two importers (shared if it stays small).
