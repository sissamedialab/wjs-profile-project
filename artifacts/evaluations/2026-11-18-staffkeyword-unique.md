# Evaluation — staffkeyword-unique

- **Date:** 2026-11-18
- **Branch:** feature/issue-3436-staffkeyword-unique-together (138e7dc7..8da2739c)
- **Task:** wjs/specs#3436
- **Coverage:** full

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Constraint + migration 0046 + merge in both importers match the spec; `set_editor_parameters` path not exercised by a test |
| Testing | 4 | 15 | 13 new tests, RED seen before GREEN (3 model, 10 importer); `set_editor_parameters` untested, RED for importer tests was an ImportError |
| Security | 4 | 15 | No new input surface, no secrets, no raw SQL; migration is constraint-only, no data deletion |
| Code quality | 4 | 15 | pre-commit clean; helper `import_logic.py:492` is small and documented; `Meta.ordering` is a visible side effect (director formset row order) |
| Maintainability | 4 | 15 | One shared helper for both importers, old "left as-is" branch removed; the helper lives in `import_logic.py` (large module) |
| Error handling | 4 | 10 | Duplicates warn and merge instead of raising; migration fails loudly on dirty data by design; NULL `keywordName` still unguarded (pre-existing) |
| Documentation | 4 | 10 | Docstrings on helper, Meta and tests; no prose doc covers this area, CHANGELOG is release-generated |

## Recommendations
- Functionality/Testing: add a DB test for `set_editor_parameters` with duplicate source rows.
- Code quality: decide whether the formset should order by `keyword__word` to keep a stable, readable order.
- Maintainability: none needed now.

## Total
**80%** — meets the spec with solid tests; remaining gaps are minors (untested secondary importer, ordering side effect).
