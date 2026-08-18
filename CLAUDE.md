# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WJS (Web Journal System) is a set of Django apps/plugins that live **inside** [Janeway](https://www.openlibhums.org/site/janeway/), OLH's journal-management system. This repo is not a standalone Django project — it is installed (`pip install -e .`) into a Janeway checkout and wires itself into Janeway via `AppConfig.ready()` hooks, Janeway's event system, and Janeway's plugin loader. It replaces the older wjapp system for journals such as JCOM, JCOMAL, JCAP, and JQuant.

Package name: `wjs.jcom_profile` (see `setup.cfg`). Despite the package name, this repo now hosts far more than profile management — see Architecture below.

**Maintenance note:** most of this repository's code is manually crafted (not AI-generated), and it evolves faster than this file does — commands, scripts, and conventions here can go stale silently. Periodically re-run `/init` (or otherwise re-derive this file from the current state of the repo) rather than assuming it stays accurate between visits.

## Commands

### Setup
```bash
pip install -e .[test]                    # install into Janeway's virtualenv, from this repo's dir
python manage.py run_customizations       # from janeway/src: apply all WJS customizations to Janeway
./build_assets.sh                         # compile JCOM-theme frontend assets (needs inotify-tools)
```

### Tests — must run from `janeway/src`, not from this repo
```bash
# from janeway/src/
pytest --create-db -n7 ../../wjs-profile-project                      # this repo's tests
pytest path/to/test_file.py::TestClass::test_name                     # single test
```
Settings module, migration handling, test locations, parallelism, DB-reuse flags, and Docker-based CI
replication: `.claude/rules/tests.md`.

### Linting
```bash
pre-commit run --all-files
```
Tool stack, where each tool's config lives, and the do-not-use-ruff note: `.claude/rules/linting.md`.
Django template formatting (djlint rules, `[tool.djlint]` config, what's tool-enforced vs. convention):
`.claude/rules/templates-django.md`.

### Branching
Branch naming and workflow, commit-message format (Conventional Commits), MR template/description rules, and the
branching model: `.claude/rules/version-control.md`.

### Releasing
`scripts/release.sh` automates a `wjs-develop` → `wjs-production` release for this (or any `wjs-*`) package: merges develop into production, bumps the version, generates the changelog from GitLab MRs/issues, tags, merges back, and bumps the post-release dev version. Design notes: `docs/superpowers/specs/2026-07-17-release-script-design.md`; implementation walkthrough: `docs/superpowers/plans/2026-07-18-release-script-implementation.md`.

**Any edit to `scripts/release.sh` — by Claude or by hand — must keep both of those docs consistent with the script.** Update the design spec when behavior/contract changes (new step, changed guarantee, new edge case) and the implementation plan when the actual code changes (new/modified function, new task-worthy fix) — do not let either drift the way they previously did.

## Architecture

### How this plugs into Janeway
- `wjs/defaults/settings.py` is Janeway's settings module extended with WJS's `INSTALLED_APPS`, channels/ASGI config, Q-cluster/Redis config, and dozens of `WJS_*` / per-journal dict settings (many keyed by journal code, e.g. `{None: ..., "JCOM": ..., "JCOMAL": ...}` where `None` is the default journal).
- `wjs.jcom_profile.apps.JCOMProfileConfig.ready()` monkeypatches `core.forms.RegistrationForm`, inserts this app's `templates/` dir at the front of Janeway's template `DIRS` (so WJS templates can override JCOM-theme/Janeway templates), and registers hook functions (`extra_corefields`, `extra_article_metadata`, `extra_edit_profile_parameters`, `extra_edit_subscription`) into Janeway's `core.plugin_loader`.
- Each `wjs/plugins/<name>/` directory is a Janeway **plugin**: it has a `plugin_settings.py` defining a `plugins.Plugin` subclass (name, version, `janeway_version`, manager URL). Only `wjs_review` also has an `apps.py` `AppConfig` (needed because it registers event handlers and monkeypatches on startup); the simpler plugins rely solely on `plugin_settings.py`. Plugins are linked/installed into Janeway's `plugins/` dir by the `link_plugins` management command.
- Business logic hooks into Janeway's **event system** (`events.logic.Events`) rather than overriding Janeway code directly: `AppConfig.ready()` calls `register_for_event`/`unregister_for_event` to attach WJS handlers (and detach Janeway's default transactional emails) for events like `ON_ARTICLE_SUBMITTED`, `ON_REVISIONS_COMPLETE`, `ON_ARTICLE_ACCEPTED`, `ON_ARTICLE_PUBLISHED`, etc. See `wjs/plugins/wjs_review/apps.py::register_events` for the canonical example — it also defines custom events (`ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED`) that chain off Janeway's events.
- Account/queryset behavior is extended via monkeypatching rather than subclassing: `wjs_review`'s `AppConfig.ready()` attaches methods like `filter_reviewers`, `annotate_is_active_reviewer` directly onto Janeway's `AccountManager`/`AccountQuerySet`.

### Sibling packages: `wjs-submission` and `wjs-themes`
Two other packages, developed in the same GitLab group (`wjs/`) by the same team, are hard dependencies of this repo. They are separate projects — **not vendored here** — normally checked out and `pip install -e`'d side-by-side with this repo in the same Janeway virtualenv (see `setup-docs/ansible/wjs-setup-user.yml`), and version-pinned in `setup.cfg`'s `install_requires`.

- **`wjs-submission`** (repo `wjs-submission-project`, pinned `>= 2.0.9.dev1`) is a Janeway plugin (`plugins.wjs_submission`) implementing WJS's custom submission/revision workflow: `ArticleSubmission`, `RevisionStorage`, access-mode selection, funding ("step7") views, revision views. `wjs_review` imports from it throughout (`models.py`, `logic.py`, `logic__production.py`, `events/handlers.py`, `states.py`, `unique_check.py`, ...) — treat it as load-bearing, not optional, even though its source isn't in this checkout. Whether WJS's custom flow (vs. Janeway's stock one) is used at all is itself journal-configurable via `WJS_USE_WJS_SUBMISSION` in `wjs/defaults/settings.py`. Its tests run the same way as this repo's, against the sibling checkout: `pytest --create-db -n7 ../../wjs-submission-project` from `janeway/src` (see `.claude/rules/tests.md`).
- **`wjs-themes`** (repo `wjs-themes`, pinned `>= 2.0.13`) is the Django app (`wjs.themes`, listed in `INSTALLED_APPS`) providing the JCOM graphical theme/templates this project's own templates are designed to override (README: "needs JCOM graphical theme"). It contributes the `wjs.themes.context_processors.wjs_themes_version` context processor wired in `wjs/defaults/settings.py`. It also contains **`wjs-bootstrap`**, the Bootstrap5-based base theme shared as the common Janeway template/CSS foundation across all `wjs-*` packages — registered in `CORE_THEMES` and referenced as the `BOOTSTRAP5["css_url"]` (`/static/wjs-bootstrap/css/base.css`) in `wjs/defaults/settings.py`. Unlike `wjs-submission`, WJS logic doesn't import Python from it directly — it's a template/static-asset dependency, pulled in purely through Django app registration.

When tracing behavior that isn't in this repo, check the sibling checkout (or the package installed in Janeway's virtualenv) rather than assuming the code is missing.

### Package layout
- `wjs/jcom_profile/` — the core app: extended `Account`/profile model (`JCOMProfile`), newsletter subscription (`newsletter/`), reviewer/editor dashboards (`dashboard/`), wjapp data-import tooling (`import_*.py`), and most `manage.py` customization commands (`management/commands/`).
- `wjs/plugins/wjs_review/` — the review-workflow plugin; the largest and most central piece of business logic (see below).
- `wjs/plugins/wjs_home_blocks/`, `wjs_latest_articles/`, `wjs_latest_news/`, `wjs_stats/`, `wjs_subscribe_newsletter/` — smaller, self-contained Janeway plugins (homepage widgets, stats, newsletter opt-in).
- `wjs/channels/` — Django Channels/ASGI routing (`ASGI_APPLICATION = "wjs.channels.asgi.application"`), used for realtime features (e.g. synctex).
- `wjs/defaults/` — the merged settings module used both in production (`settings.py`) and tests (`tests.py`, which layers `SkipMigrations` etc.).
- `wjs/conf/` — Apache redirect includes for legacy Drupal/wjapp URLs.

### `wjs_review`: the review-workflow engine
This plugin models the entire editorial pipeline (submission → editor assignment → peer review → decision → revision → typesetting → proofreading → publication) as a `django-fsm` state machine:
- `ArticleWorkflow` (`models.py`) is a `OneToOneField` companion to Janeway's `submission.Article`, holding an `FSMField` (`ArticleWorkflow.ReviewStates`, ~19 states from `EditorToBeSelected` through `Published`) with ~29 `@transition`-decorated methods driving state changes. `ArticleWorkflow.Decisions` models editor decisions (accept/reject/minor/major/technical revision/not suitable/open appeal).
- `states.py` maps states to the **actions** available to each role in that state (who can do what, which view to link to) — this is the source of truth for role-based UI affordances, not the views themselves.
- `logic.py` (~4900 lines) holds the transition-triggering business logic; `logic__production.py` and `logic__visibility.py` split out typesetting/production logic and reviewer-visibility logic respectively. Corresponding `forms__production.py`/`forms__visibility.py`/`views__production.py`/`views__visibility.py` follow the same split.
- `events/` wires this plugin's handlers into Janeway's and `wjs_submission`'s event systems (see `apps.py::register_events`); `events/assignment.py` and `events/checks.py`/`checks_after_acceptance.py` implement the **per-journal pluggable functions** referenced by the `WJS_ARTICLE_ASSIGNMENT_FUNCTIONS`, `WJS_REVIEW_CHECK_FUNCTIONS`, `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` settings dicts in `wjs/defaults/settings.py` — different journals (JCOM, JCOMAL, JCAP) can plug in different assignment/check behavior without branching core logic.
- `models.py` also defines the messaging system (`Message`, `MessageRecipients`, `MessageThread`) used for in-app editor/author/reviewer communication, `Reminder` (automated nudges, e.g. `WJS_REMINDER_LATE_AFTER`), and `PermissionAssignment` (fine-grained per-article role permissions, backed by `permissions.py`/`conditions.py`).
- `role_cache.py` + the `ROLE_FOR_ARTICLE_CACHE_*` settings cache per-article role lookups in Redis (`CACHES` **must** stay Redis-backed — `django_cache.delete_pattern()` is used and only works with Redis; see the comment in `wjs/defaults/settings.py`).
- `conversion.py`/`synctex/` integrate external services (`YAKUNIN_URL`, `JCOMASSISTANT_URL`) that compile submitted sources to PDF/galleys; `prophy.py` integrates the Prophy reviewer-suggestion API.

### Multi-journal configuration pattern
Many settings and event handlers are journal-specific dicts keyed by journal code (`None` = default/fallback), e.g. `WJS_ARTICLE_ASSIGNMENT_FUNCTIONS`, `WJS_REVIEW_CHECK_FUNCTIONS`, `WJS_ARTICLE_LANGUAGES`, `PROFILE_FIELDS`. When adding journal-specific behavior, follow this pattern rather than branching on journal code inline in views/logic.

### wjapp import tooling
`wjs/jcom_profile/import_logic.py`/`import_utils.py`/`import_file_manager.py` and the `import_articles_from_wjapp*` management commands migrate legacy data (articles, users, files) from the old wjapp MariaDB databases (`WJAPP_<JOURNAL>_IMPORT_CONNECTION_PARAMS`/`..._LOGIN_PARAMS` settings) into Janeway/WJS.
