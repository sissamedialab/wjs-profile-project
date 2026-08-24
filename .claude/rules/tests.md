# Testing rules

This repo is a set of Django apps/plugins that live **inside Janeway** — tests cannot run from this directory in isolation. They need a full Janeway environment.

## Normal local workflow — run from `janeway/src`

Run pytest **from the `janeway/src` directory**, pointing at the target repo by relative path. `pytest.ini` is auto-picked from the target repo's rootdir.

```bash
# from janeway/src/
pytest --create-db -n7 ../../wjs-profile-project       # this repo's tests
pytest --create-db -n7 ../../wjs-submission-project    # sibling repo's tests
```

The repo must be installed into Janeway's virtualenv first: `pip install -e .[test]`.

**The full suite takes 10+ minutes to run, even with `-n7`.** Avoid running it
wholesale — prefer a selector that targets the file/class/test relevant to the
change (see *Useful invocations* below), and reserve a full run for cases that
genuinely need it (e.g. pre-release, suspected cross-test ordering issues).

## Useful invocations

- **Single test:** `pytest path/to/test_file.py::TestClass::test_name`
- **Reuse DB (default):** `--reuse-db`; force a rebuild with `--create-db`
- **Parallel:** `-n<N>` (e.g. `-n7`)
- **Skip one-shot data-migration command tests:** `-m "not fix_labels"` (CI uses this)

## Key facts (`pytest.ini`)

- `DJANGO_SETTINGS_MODULE = wjs.defaults.tests` — merges Janeway's global settings with ours.
- `addopts = --reuse-db --ignore=api --ignore=plugins` — Janeway's own `api/` and `plugins/` test dirs are deliberately ignored (they fail to import outside their app registry).
- Tests run in parallel and reversed (`pytest-reverse`) in CI to catch ordering dependencies.
- Migrations are skipped in tests (`IN_TEST_RUNNER` + `SkipMigrations` in `wjs/defaults/tests.py`); data normally created by migrations must be recreated via fixtures in `conftest.py`.
- Test locations: `wjs/jcom_profile/tests/` and `wjs/plugins/wjs_review/tests/`.

## Docker = CI replication only

The `docker-compose-test-*.yml` files reproduce the CI test environment. They are **not** part of the day-to-day workflow — use them only when local tests and CI disagree and you need to reproduce CI exactly. Inside the container, `setup_environment` is the bootstrap (also what CI runs).

> **Never commit migrations generated inside the docker env** — they are spurious (they exist only to sync translation fields there).

## TDD cycle

1. Write a failing test (red)
2. Write the minimal code to make it pass (green)
3. Refactor — keep tests green throughout

## Pytest conventions

- Test files: `test_<module>.py`
- Test functions: `test_<description>`
- Fixtures in `conftest.py`
- Use descriptive assertions — no bare `assert` without a message

## Coverage

- New code must be covered; aim for 90%+
- Run: `tox -e coverage`

## Integration vs unit

- **Unit tests**: mock external services; test logic in isolation
- **Integration tests**: use a real database — never mock the Django ORM; test views and model interactions end-to-end
