# Remove "salutation" from registration and author names (all journals)

- Issue: wjs/specs#3271
- Date: 2026-10-27
- Status: draft, awaiting approval
- Language: English

## Intent

The `salutation` value (Dr, Mr, Prof., Ms, ...) must never be asked of users and must never
appear in how an author's name is rendered. Today it leaks into the landing page of published
articles (see the screenshot in the issue).

Must hold, for every journal:

- the registration form and the profile edit page never ask for a salutation;
- the author name is never built with a salutation in: the article listing, the public article
  page, the article status page, and the LaTeX preamble embedded in the TeX files.

## How the leak happens (verified)

- `core.Account.salutation` is a Janeway core field. Janeway's `FrozenAuthor.full_name()` joins
  `name_prefix`, first, middle, last name and suffix; it does not read `salutation`.
- Janeway's `Account.snapshot_as_author()` sets `FrozenAuthor.name_prefix` to the account's
  `name_prefix`, or, when that is empty, to the account's **salutation**. WJS calls it on
  submission, in the review workflow, on import and from the profile views.
- Result: salutations are copied into `FrozenAuthor.name_prefix` and then rendered by every
  `full_name` consumer, including the per-journal `LatexPreamble` templates (JCOM, JCOMAL and
  JQuant all use `au.full_name`).

Data in the production copy (checked 2026-10-27, read-only):

- 1,200 accounts have a salutation (Dr 488, Mr 217, Prof. 178, Ms 147, Miss 94, Mrs 74, Mx 2).
- No account has a `name_prefix`.
- 213 of 11,312 frozen authors have a `name_prefix` (JCOM 178, JCOMAL 22, JQuant 13); all 213 equal
  the author account's salutation. So no real prefix exists in the data.

## Goals

1. Remove `salutation` from `JCOMRegistrationForm` (`wjs/jcom_profile/forms.py`).
2. A data migration clears the existing values, so nothing is rendered any more.
3. Tests prove the name is rendered without salutation on every surface above.

## Non-goals

- No change to Janeway core code, forms or templates, including the admin register form
  (`admin/core/accounts/register.html`): not overridden, to avoid patching Janeway.
- No change to `snapshot_as_author` (decision: once account values are cleared it copies nothing).
- No change to the profile edit form: `WjsPersonalInfoForm` already does not include `salutation`.
- No change to the XML metadata export, which has its own `salutation` value (frozen author
  `name_prefix`, default "Dr."). After the cleanup it always outputs "Dr."; revisit separately if wanted.
- No change to the per-journal `LatexPreamble` templates: they only use `full_name`.
- The `salutation` column is not dropped (core model).

## Anti-goals

- A migration that touches real prefixes: only a `name_prefix` equal to the author's salutation is cleared.
- A migration that can fail on large tables or hold long locks: use set-based `update()` calls.

## Design

### Registration form

Remove `"salutation"` from `JCOMRegistrationForm.Meta.fields`.

### Data migration (`wjs/jcom_profile/migrations/0045_...`)

Depends on `0044_alter_staffworkloadparameters_brake_on` and on the latest `core` and `submission`
migrations the code relies on. `RunPython`, in this order:

1. `FrozenAuthor.objects.exclude(name_prefix="").filter(author__salutation=F("name_prefix")).update(name_prefix="")`
   (must run first: it needs the salutations still in place).
2. `Account.objects.exclude(salutation="").update(salutation="")`.

Reverse: no-op (the values cannot be restored). Uses `apps.get_model` historical models.

### Residual risk (accepted)

An admin or the Janeway API can still set a salutation. It would reach `FrozenAuthor.name_prefix` on
the next author snapshot. Accepted: we don't override `snapshot_as_author` or the admin form.

## Tests

- Registration form has no `salutation` field.
- Migration: clears account salutations; clears frozen-author prefixes equal to the salutation; leaves a
  frozen-author prefix that differs from the salutation untouched; is idempotent (second run no-op).
- Rendering, after the migration with a salutation set on the account before it ran: `full_name` of the
  frozen author, the article listing, the public article page, the status page and the rendered LaTeX
  preamble do not contain the salutation.

## Decided by the builder (not by the user)

- Migration name/number and exact dependencies: to be taken from the repo state at implementation time.
- Test layout: follows the existing structure under `wjs/jcom_profile/tests/` and
  `wjs/plugins/wjs_review/tests/`.
