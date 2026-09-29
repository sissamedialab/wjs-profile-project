# Evaluation — blacklist-bulk-add-redirect

- **Date:** 2026-09-24
- **Branch:** bugfix/3194-fix-blacklist-bulk-add-redirect (893b6e81..HEAD + working tree)
- **Task:** wjs/specs#3194
- **Coverage:** full (2 source files + plan)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 5 | 20 | `advanced_admin/admin.py:495-500` reverses the changelist on `self.admin_site.name`; the regression test gets a 302 to the advanced-admin changelist and then a 200 |
| Testing | 4 | 15 | New test failed first with the production `NoReverseMatch`; a mutation dropping `current_app` makes it fail; it covers added, skipped and message paths; the invalid-form path stays untested (pre-existing) |
| Security | 4 | 15 | No new inputs or permission changes; the view is still wrapped by `admin_site.admin_view` (`admin.py:403`); sec-scan not run |
| Code quality | 5 | 15 | Same idiom as Django's `ModelAdmin.response_add`; pre-commit (black, flake8, isort) clean; 5-line change |
| Maintainability | 4 | 15 | The name comes from `opts`, so it survives a site or model move; the custom name `blacklisted_authoremail_bulk_add` (`admin.py:404`) still breaks Django's naming pattern |
| Error handling | 4 | 10 | The 500 after a successful save is gone; the save path is unchanged; invalid form re-renders as before |
| Documentation | 4 | 10 | The existing `bulk_add_view` docstring is still accurate; the plan and commit message record the root cause; CHANGELOG is generated from MRs at release |

## Total
**87%** — correct, minimal fix with a mutation-checked regression test. Optional follow-up: rename the custom bulk-add URL to Django's naming pattern.
