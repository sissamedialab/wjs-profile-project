# Linting & code style rules

Code style is enforced; it is not a matter of personal preference. All `wjs-*` code must satisfy the configured tools at **line length 119**.

## Where config lives

Tool config is split across two files, not fully centralized in either:

- **`pyproject.toml`** — `[tool.black]`, `[tool.isort]`, `[tool.pydocstyle]`, `[tool.djlint]`.
- **`setup.cfg`** — `[flake8]`, `[pycodestyle]`, and (see below) a second, conflicting `[pydocstyle]` section. Everything else in `setup.cfg` is packaging metadata (`[metadata]`, `[options]`, `[options.extras_require]`, ...).

flake8 reads `setup.cfg` natively — there is **no** `[tool.flake8]` in `pyproject.toml` and **no** `Flake8-pyproject`-style shim anywhere in this repo (it isn't in `.pre-commit-config.yaml`'s flake8 hook `additional_dependencies`, and it doesn't need to be, since flake8 config isn't in `pyproject.toml`). Don't assume one exists.

> **Known inconsistency:** `pydocstyle` is configured in *both* `pyproject.toml` (`[tool.pydocstyle]`, `ignore = D101,D104,D105,D106,D212,D203,D213`) and `setup.cfg` (`[pydocstyle]`, `ignore = D102,D200,D203,D212,D213`) — with different ignore lists. Which file wins depends on pydocstyle's config-search order; don't assume either is authoritative without checking actual `pre-commit run pydocstyle` output first. If you need to change pydocstyle's ignore list, either update both sections or resolve the duplication.

## The gate: pre-commit

`pre-commit` enforces style locally and in CI.

```bash
pip install pre-commit && pre-commit install   # once
pre-commit run --all-files                      # on demand
```

Always run `pre-commit run --all-files` before committing.

## Tool stack (authoritative)

- **black** — formatter (line length 119)
- **isort** — import sorting (black profile)
- **flake8** — + broken-line, bugbear, builtins, comprehensions, eradicate, pep8-naming (exact set: `.pre-commit-config.yaml`'s flake8 hook `additional_dependencies`)
- **pydocstyle** — docstring conventions
- **pyupgrade** / **django-upgrade** (target 4.2)
- **djlint** — Django templates (rule details and the repo's `[tool.djlint]` config: `.claude/rules/templates-django.md`)
- **prettier** — js/css/scss

CI runs the identical hooks: `.gitlab-ci-pre-commit.yml` just calls `pre-commit run -a`, so there is no separate/broader lint pass to fall back on — if a rule isn't enforced locally, it isn't enforced in CI either.

## Do NOT use ruff in `wjs-*` repos

ruff is **Janeway's** (upstream) tool. The `wjs-*` repos use black + isort + flake8. There is leftover `[tool.ruff]` config in `pyproject.toml`, but it is not run — ignore it and rely on the stack above.

## Style notes

- `setup.cfg`'s `[flake8]` section sets `inline-quotes = double` (a flake8-quotes option) and `banned-modules = __future__ = ...` (a flake8-tidy-imports option), i.e. the *intended* style is double quotes and no `from __future__ import ...`. **However**, neither `flake8-quotes` nor `flake8-tidy-imports` is currently in the flake8 hook's `additional_dependencies` — as configured today, `pre-commit run` does not actually enforce either rule; the settings are inert until those plugins are added to the hook. Follow the style anyway (it's consistent with the rest of the codebase), but don't rely on pre-commit to catch violations of it.
