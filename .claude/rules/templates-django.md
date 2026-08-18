---
description: Django template formatting rules, sourced from this repo's djlint configuration
---

# Django Template Formatting (djlint)

The shared Django rules in
[`.claude/rules/code-style-django.md`](.claude/rules/code-style-django.md) apply too —
this file goes deeper on templates specifically, using this repo's djlint config
(`[tool.djlint]` in `pyproject.toml`) as the source of truth.

## Configuration

```toml
[tool.djlint]
profile="django"
ignore="H006"
```

- `profile="django"` scopes the active rule set to Django's template language: codes
  that only apply to Jinja (`J*`), Nunjucks, or Handlebars/meta-linting (`N*`, `M*`)
  are dropped. What's left is `H*` (generic HTML), `T*` (template-tag syntax), and
  `D*` (Django-specific) codes.
- `ignore="H006"` disables "Img tag should have height and width attributes" —
  `<img>` tags in this repo are not required to declare explicit dimensions.
- Nothing else is set, so every other djlint default applies as-is: 4-space
  `indent`, `max_line_length` 120, `max_attribute_length` 70, no
  `blank_line_before_tag`/`blank_line_after_tag`, `format_css`/`format_js` off.

**Known inconsistency:** existing templates in this repo are hand-formatted with
**2-space** indentation (e.g. `wjs/jcom_profile/templates/base/table_header_field.html`),
which contradicts djlint's un-overridden 4-space default. This isn't caught by CI:
`.pre-commit-config.yaml` only wires up the `djlint-django` hook (`djlint
--profile=django`, lint-only) — the reformatter (`djlint --reformat`) is never run
automatically, so indentation is a manual convention, not a tool-enforced one. Match
the 2-space convention of the surrounding file by hand; don't assume a passing
pre-commit run says anything about indentation, and don't run `djlint --reformat`
without `--indent 2` and a careful diff review.

## Rules the `django` profile enforces (pre-commit-checked)

- **Template-tag syntax (`T*`)** — T001 wrap `{{ var }}` / `{% tag %}` in whitespace
  (`{{ var }}`, never `{{var}}`); T002 double-quote string args inside tags
  (`{% trans "..." %}`, never `'...'`); T003 name every `{% endblock %}`
  (`{% endblock content %}`); T027 no unclosed quotes inside tag syntax; T032 no
  doubled-up internal whitespace inside a tag; T034 no `{% ... }%` typos (missing
  the closing `%`).
- **Django-specific (`D*`)** — D004 use `{% static "path/to/file" %}` instead of a
  hardcoded `/static/...` URL; D018 use `{% url "name" %}` instead of a hardcoded
  internal path in `href`/`action`/`src`.
- **Generic HTML (`H*`)** — lowercase tag and attribute names (H009/H010);
  double-quoted attribute values with no stray spaces around `=` (H008/H011/H012);
  no duplicate attributes on one tag (H037); `<img>` needs `alt` (H013 — the
  companion `height`/`width` check, H006, is the one this repo disables);
  `<html>` needs `lang` (H005) and the document needs a `<title>` (H016); avoid
  inline `style=` (H021); avoid plain-HTTP links (H022); no raw entity references
  beyond the common ones (`&lt;`, `&gt;`, `&amp;`, `&quot;`, `&nbsp;`, ...) (H023);
  no more than the configured blank-line run (H014, `max_blank_lines` defaults to 0
  — i.e. no blank lines at all are allowed by default); a line break after
  `<h1>`–`<h6>` (H015); no `type=` attribute on `<script>`/`<style>` (H024); no
  empty tag pairs or empty `class`/`id` attributes (H020/H026); lowercase form
  `method` values (H029); a `<meta name="description">`/`keywords` tag is suggested,
  not required (H030/H031).
- **Shipped but inert for this profile** — H017 (self-close void tags), H035
  (self-close `<meta>`), and H036 (avoid `<br>`) all default to `off`
  (`default: false` in djlint) and this repo doesn't opt them back in via
  `include`; T028 (suggest spaceless tags, `{%- if -%}`) explicitly excludes the
  `django` profile in djlint itself, since that's a Jinja-only feature.

## Running it

```bash
djlint --profile=django wjs/                        # lint only — matches what pre-commit/CI run
pre-commit run djlint-django --all-files             # the actual hook, same as CI

# reformatting is NOT run by pre-commit — use deliberately, review the diff, and
# pass --indent 2 to match the repo's existing convention rather than djlint's default of 4:
djlint --reformat --profile=django --indent 2 wjs/path/to/template.html
```

## Conventions this repo follows but djlint doesn't check

These aren't djlint rules — they're project convention, so nothing flags a
violation automatically:

- Application templates live under `templates/<app_name>/` — never in the global
  template namespace.
- No blank lines between `{% extends %}` / `{% load %}` tags; one blank line before
  the first `{% block %}`.
- 2-space indentation for HTML, with template tags indented as if they were HTML
  elements (see the "Known inconsistency" note above for why djlint won't enforce
  this for you).
