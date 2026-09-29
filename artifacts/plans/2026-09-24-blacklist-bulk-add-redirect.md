# Fix blacklist bulk-add redirect (wjs/specs#3194) — Implementation Plan

**Goal:** Saving the "Bulk add blacklisted author emails" form in the advanced
admin must redirect back to the changelist instead of raising a 500.

**Branch:** `bugfix/3194-fix-blacklist-bulk-add-redirect`

## Root cause

`BlacklistedAuthorEmailAdmin.bulk_add_view`
(`wjs/plugins/wjs_review/advanced_admin/admin.py`) ends a successful POST with:

```python
return HttpResponseRedirect(reverse("admin:blacklisted_authoremail_changelist"))
```

This fails with `NoReverseMatch` for two reasons:

1. **Wrong URL name.** Django's admin names the changelist
   `{app_label}_{model_name}_changelist`, i.e.
   `wjs_review_blacklistedauthoremail_changelist`. `blacklisted_authoremail_changelist`
   was never registered.
2. **Wrong admin site instance.** The model is registered on `advanced_admin_site`
   (`AdvancedAdminSite(name="advanced_admin")`, defined in wjs-themes), not on
   Janeway's default `admin.site`. Admin URLs live under the application namespace
   `admin` with one instance namespace per site. Without `current_app`,
   `reverse("admin:…")` resolves against the default instance, where this model
   is not registered.

The template link `{% url 'admin:blacklisted_authoremail_bulk_add' %}` works
because admin views set `request.current_app`, which the `{% url %}` tag uses.
The view-level `reverse()` has no such context.

The emails are saved and the attention conditions re-evaluated **before** the
redirect runs, so the reported error happens after the data has been stored.

No existing test calls `bulk_add_view`. The two admin tests in
`test_blacklisted_author.py` only call `save_model`/`delete_model` on an admin
built with a bare `AdminSite()`.

## Fix

Use Django's own idiom (as in `ModelAdmin.response_add`):

```python
opts = self.model._meta
changelist_url = reverse(
    f"admin:{opts.app_label}_{opts.model_name}_changelist",
    current_app=self.admin_site.name,
)
return HttpResponseRedirect(changelist_url)
```

This matches the template's namespace style and works whichever site the
admin is registered on.

## Tasks (TDD)

### Task 1 — Regression test (red)

In `wjs/plugins/wjs_review/tests/test_blacklisted_author.py`, add
`test_admin_bulk_add_redirects_to_changelist`:

- Log in the `admin` fixture (superuser/staff) with `client.force_login`.
- Build the bulk-add URL with
  `reverse("admin:blacklisted_authoremail_bulk_add", current_app="advanced_admin")`.
- POST two lines (one email only, one `email, note`).
- Assert status `302` and that `Location` equals the advanced-admin changelist URL.
- Assert both `BlacklistedAuthorEmail` rows exist with the right note.
- Follow the redirect and assert `200`. This also renders `change_list.html`,
  which covers its `Bulk add` link.

Run the single test from `janeway/src` and confirm it fails with `NoReverseMatch`.

### Task 2 — Fix (green)

Apply the change above in `bulk_add_view`. Re-run the new test, then the whole
`test_blacklisted_author.py` file.

### Task 3 — Verify and lint

- `pre-commit run --files <changed files>`
- Nothing else in the repo calls `reverse()` with a bare `admin:` name for
  advanced-admin models (already checked with grep: this was the only one).

## Out of scope

- No towncrier fragment: the repo has no `changes/` directory.
- No change to the bulk import parsing logic.
