---
description: Code style rules shared by all Python projects — imports, quoting, docstrings
---

# WJS Python Code Style

Rules that hold for every Python project regardless of framework. The stack's own
code-style file (`code-style-django.md`, `code-style-fastapi.md`) adds to these and
is the authority wherever it is more specific.

**Line length is set per stack, not here** — Django projects use 119 characters,
FastAPI projects use Black's default 88. Take the number from the stack file.

## Imports

- Avoid local imports unless they are needed to solve a circular dependency

## Formatting

- **Double quotes** `"` only — never single quotes `'`
- 4 spaces indentation; no tabs
- Python 3 only — no `u"..."` string prefix, no encoding pragma (`# -*- coding: utf-8 -*-`)

## Docstrings (pydocstyle)

- `"""Triple double quotes"""` — never single or double-single
- First line: imperative mood, ends with `.`, `?`, or `!`
- First word properly capitalised
- No blank lines immediately before or after the docstring in functions (D201/D202)
- All public methods and functions must have a docstring (D102/D103)
