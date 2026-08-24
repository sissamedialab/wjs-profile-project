---
description: Code style rules for Django — formatting, i18n, models, forms, and views
---

# Django Code Style

The shared Python rules in
[`.claude/rules/code-style-python.md`](.claude/rules/code-style-python.md) apply too —
imports, quoting, indentation, and docstrings live there. This file covers what is
specific to Django.

## Formatting

- Line length: **119 chars** (`black --line-length 119`)

## i18n

- All user-facing strings use `gettext_lazy`: `from django.utils.translation import gettext_lazy as _`
- Usage: `label = _("My string")` — never bare string literals in model fields or forms

## Django models

Required field structure — always in this order:

1. Non-field attributes
2. Database fields — every field must have `verbose_name`
3. Custom manager attributes
4. `class Meta` — must define `verbose_name`, `verbose_name_plural`, and `ordering`
5. `def __str__()`
6. `def save()`
7. `def get_absolute_url()`
8. `@property` methods
9. Custom methods

## Django views

- Prefer CBV over FBV unless logic is strictly linear or the view does not handle a model
- URL names and paths use hyphens, not underscores: `my-list` / `/my-list/`
- Use reverse / reverse_lazy whenever a URL is referenced

## Settings

- Never `import` django or python packages at module level in the project's settings module, per the
  project-layout rules file
- If unavoidable, wrap in `try: ... except ImportError: pass` and fail gracefully

## Django templates

Formatting rules (tag syntax, HTML markup, what djlint enforces vs. what's just
convention) are covered in full in
[`.claude/rules/templates-django.md`](.claude/rules/templates-django.md) — that
file uses this repo's `[tool.djlint]` config as its source of truth. In brief:

- Close every block with its name: `{% endblock content %}` — never bare `{% endblock %}`
- Application templates under `templates/<app_name>/` — never in the global namespace
- Variables with spaces: `{{ var }}` — never `{{var}}`
- 2-space HTML indentation; indent templatetags as if they were HTML elements
- No blank lines between `{% extends %}` / `{% load %}` tags; one blank line before the first `{% block %}`
