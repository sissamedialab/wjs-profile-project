# WJS 5-Repo Django 5.2 Migration — Cross-Repo Handover Notes

All five `wjs-*` plugin repos (`wjs-themes`, `wjs-search-user`, `wjs-utils-project`,
`wjs-submission-project`, `wjs-profile-project`) have been ported to Django 5.2 / Python 3.11+,
following the core `janeway-5.2` migration. Each repo's own spec, implementation plan, and SDD
ledger (`.superpowers/sdd/2026-08-26-django-5.2-migration/progress.md`, gitignored) carry the
full detail; this note collects what needs an owner's attention before these branches merge —
items no single repo's spec fully captured, because they only became visible once all five were
verified together.

All work lives on each repo's `docs/django-5.2-migration-spec` branch, in a dedicated worktree
(`.worktrees/django-5.2-migration-spec`) — the main checkout of each repo is untouched, still on
its original default branch (`wjs-develop` for four repos, `master` for `wjs-utils-project`).

## Real Django 5.2 / Python 3.13 bugs found and fixed

Static tooling (`django-upgrade`, `ruff`) found real code changes in only one repo
(`wjs-submission-project`, two mechanical modernizations). Every other genuine compatibility bug
was found by **live verification** — installing each package into the real, already-migrated
`janeway-5.2` project and either rendering pages or running the real test suite:

- **`wjs-themes`**: the `length_is` template filter (removed in Django 5.1) broke the homepage and
  issue pages under both themes with a 500 error. Fixed (`length_is:"1"` negation → `length != 1`).
  Also found a genuine `admin.E109` regression (Django 5.x tightened the check to flag reverse-FK
  query names, not just M2M fields) that aborted every management command unless `--skip-checks`
  was used — fixed by renaming a `list_display` entry to a display method.
- **`wjs-submission-project`**: found **twice**, in two different functions — Django 5.0 finished a
  deprecation cycle that turns filtering a related field by an *unsaved* model instance into a
  hard `ValueError` instead of a warning. The first instance (`step1/forms.py`) was caught by the
  live test suite; a second, structurally identical instance (`workflow.py`'s three
  `is_revision_*` helpers) was hidden from that same test suite by a config value
  (`comments_to_the_editor`) that defaults to `True` and short-circuited an `or`-chain before the
  buggy code ever ran in any existing test. Both fixed with the same guard pattern
  (`if not X or not X.pk:`).
- **`wjs-profile-project`**: Python 3.13 removed support for stacking `@classmethod` on top of
  another descriptor (deprecated since 3.11). Broke `ArticleWorkflow.Decisions.decision_choices`
  (a `@classmethod @property`, used as a form's `choices=`) and a test fixture
  (`@classmethod @pytest.fixture`). Both fixed by dropping the redundant decorator.

**The pattern that matters going forward:** static analysis (syntax-level deprecation scanners)
cannot see template-filter removals, system-check tightening, or runtime deprecation-to-error
transitions. Only exercising real pages and a real test suite caught these — and even that wasn't
exhaustive by luck alone (see `wjs-submission-project`'s two-round finding above). Any future
Django/Python upgrade in this ecosystem should budget for live verification, not just tooling.

## Items needing an owner's decision before these branches merge

1. **Python floor vs. deploy targets.** All five repos now declare `python_requires >= 3.11`, but
   `wjs-submission-project`'s own `.deploy` CI template still hardcodes `image: python:3.11` for
   the deploy step — confirm the actual deploy servers run Python 3.11+ before tagging any release,
   since a 3.10 host would silently resolve to an older, pre-fix package version rather than error.

2. **CI Docker images are stale, and will read red, not just stale.** `wjs-submission-project` and
   `wjs-profile-project` share a CI test template (`.gitlab-ci-run-tests.yml`, defined in
   `wjs-profile-project`) that runs against a Janeway Docker image tagged
   `JANEWAY_VERSION: "production"` — a pre-migration (Django 4.2) build. Beyond being stale, this
   will actively fail: both repos dropped the `pytest-django < 4.13` ceiling (correct for Django
   5.2), and the resolved `pytest-django` 4.14 cannot run against the 4.2 image at all. Someone
   needs to publish a Django-5.2 Janeway image and repoint `JANEWAY_VERSION` before these
   pipelines will pass — this is expected, not a regression, but should be stated on each MR
   rather than discovered as a red pipeline.

3. **Version-release convention.** Several repos bump straight to a final `X.Y.0` on a feature
   branch (e.g. `wjs-themes` → `2.1.0`, approved during spec review) rather than the repo's own
   convention of `X.Y.0.devN` on feature branches with a separate "Release" commit + CHANGELOG
   entry. `wjs-submission-project`'s `2.1.0` will also conflict with `wjs-develop`, which has since
   moved to `2.0.18.dev1` (ordering is fine, `2.1.0 > 2.0.18.dev1`, but it's a manual merge
   resolution). Confirm with whoever owns releases whether to keep the final version numbers as-is
   or convert to `.devN` and let a release commit cut the real version + changelog entries.

4. **`wjs-search-user`'s deferred live verification is now unblocked.** Its spec deferred live
   verification because it depends on `wjs-submission-project` and `wjs-profile-project`, both of
   which are now done. Nobody has run that check yet — install `wjs.user_search` into `janeway-5.2`
   alongside all four other repos and exercise `/user-search/`, the typeahead search, collaboration
   search, and funding search endpoints.

5. **Hydra's Django 5.2 status was assumed, not verified.** `wjs-profile-project`'s CI clones a
   third-party plugin, Hydra, during `setup_environment`. Its integration into `janeway-5.2`'s own
   git history was taken as evidence it's already compatible, but no live test run in this effort
   actually exercised Hydra's own code — if the CI image update above surfaces a Hydra-specific
   failure, that's this dependency, not the wjs-* ports.

## Pre-existing bugs found along the way — not caused by this migration, fixed anyway

These predate the Django 5.2 work and are unrelated to it, but were fixed as part of this same
effort rather than only recorded (per explicit direction after the initial migration pass):

- **`wjs-utils-project`** (commit `738ab49` on that repo's `docs/django-5.2-migration-spec`
  branch, reviewed clean):
  - `management/commands/upadate_notifications.py` defined no `Command` class — the module was a
    mis-parked standalone script (guarded by `if __name__ == "__main__":`), not a real management
    command, and crashed with `AttributeError` on any invocation. **Fixed:** moved to `scripts/`
    (this repo's existing home for standalone tools), via `git mv` — content unchanged.
  - `management/commands/import_user_from_wjapp.py`'s `merge_data()` called
    `Correspondence.get_or_create(...)` on the model class directly (no such classmethod exists).
    **Fixed:** now calls `Correspondence.objects.get_or_create(...)` with `(correspondence,
    created)` tuple unpacking, matching the correct pattern already used elsewhere in the same
    file. `merge_data` has no current callers, so this was a latent bug, not an active one.
  - `import_user_from_wjapp`/`scenario_review`/`scenario_production`/`prophy_scenario` all failed
    to import due to missing, undeclared dependencies. **Fixed:** `pymysql` and `factory_boy` added
    to `setup.cfg`'s `install_requires` — this was the actual reason those commands were
    unverified, not (only) cross-plugin coupling as an earlier draft of the spec assumed. Their
    live verification is now unblocked (not yet performed — see "still open" below).
  - **Not fixed, still open:** no `[options.package_data]`/`MANIFEST.in` — templates won't ship in
    a built (non-editable) wheel. An editable-install verification strategy structurally can't
    detect this; worth fixing before anyone tags a release meant for a real (non-editable) install.
- **`wjs-profile-project`** (commit `0905d29c` on that repo's `docs/django-5.2-migration-spec`
  branch, reviewed clean):
  - `wjs/jcom_profile/management/commands/import_articles_from_wjapp_jcom_jcomal.py` imported
    `TypesetterUploadFiles` from `plugins.wjs_review.logic__production` — that name has never
    existed there (confirmed via full git history search); it's an unrelated Django view class
    living in `views__production.py`. The command has been dead (raised `ImportError` on any use)
    since commit `7ba2c9ae`. **Fixed:** renamed to `TypesettedFilesUpload`, the real class in
    `logic__production.py` whose constructor fields and monkeypatched methods
    (`_check_file_condition`, `_look_for_queries_in_archive`) exactly match what this command
    already does with it — confirmed via a live Django import that no longer raises.
  - **Not fixed, still open:** five of the six embedded plugins (`wjs_home_blocks`,
    `wjs_latest_articles`, `wjs_latest_news`, `wjs_stats`, `wjs_subscribe_newsletter` — 40 files
    total, including `wjs_stats/views.py` at 846 lines) have zero test files. All 1147 tests in
    this repo's suite come from `wjs_review` and `jcom_profile` core. The Django 5.2 compatibility
    of these five plugins rests on static analysis (clean) plus URL/import/template/migration
    checks (all clean, independently verified during the final review) rather than behavioral test
    coverage — that's a test-coverage investment, not a bug, and wasn't in scope to fix here.

**Still open, now unblocked by the fixes above:** run the previously-deferred
`wjs_mgmt_cmds/tests.py` (`import_user_from_wjapp`) and manually smoke-test the Prophy-candidate
commands/views, now that both the dependency and coupling blockers are resolved.

## Still needs human eyes, not just automated checks

`wjs-themes`' live verification (Task 5) could only do HTTP-status checks — no browser was
available in that environment (no Chrome binary, root required to install one). The `length_is`
fix is provably behavior-preserving by static analysis (verified independently against Django's
actual filter source across every input shape), but nobody has visually confirmed the language
badge still renders correctly on a real page. Recommend one human browser pass over the homepage
and an issue page, under both themes, before considering that repo's port fully closed out.

## How to pick this back up

Each repo's spec (`docs/superpowers/specs/2026-08-26-django-5.2-migration-design.md`) and plan
(`docs/superpowers/plans/2026-08-26-django-5.2-migration.md`) on the `docs/django-5.2-migration-spec`
branch record the full task-by-task history, including amendments and mid-execution corrections.
Each repo's SDD ledger (gitignored, local to the worktree that did the work) has the complete
review history and every ruling made along the way — ask whoever ran this migration for a copy if
you need it after the worktrees are cleaned up.
