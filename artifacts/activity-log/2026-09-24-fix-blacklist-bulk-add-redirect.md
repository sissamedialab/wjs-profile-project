## 2026-09-24 — Fix 500 after blacklist bulk add in advanced admin
**What:** Saving the advanced admin's "Bulk add blacklisted author emails" form now redirects back to the changelist instead of returning a 500. A client-level regression test covers the view.
**Why:** wjs/specs#3194. `bulk_add_view` reversed `admin:blacklisted_authoremail_changelist`, a URL name that was never registered, and passed no `current_app`. The emails were saved and ACs re-evaluated *before* the crash, so users saw an error after a save that had worked (a retry reports them as "already existed").
**Decisions:** Used Django's own idiom (`admin:{app_label}_{model_name}_changelist` with `current_app=self.admin_site.name`, as in `ModelAdmin.response_add`) instead of hard-coding `advanced_admin:…`. It matches the template's `{% url 'admin:…' %}` style and survives moving the model to another admin site. Ran a lighter nephila-flow (systematic-debugging instead of brainstorming, no spec file) because this was a one-line bugfix.
**Gotchas:**
- Correcting the name alone is not enough. Without `current_app`, `reverse("admin:…")` resolves against Janeway's default admin instance, where the model isn't registered. A mutation test confirmed it still fails. Template `{% url %}` tags work only because admin views set `request.current_app`.
- In tests, the `journal` fixture sets the journal script prefix, so `reverse()` already returns `/JCOM/plugins/...`. Don't prepend `/{journal.code}` yourself (it gives a 404). Without the fixture, Janeway's middleware redirects to the default site.
- Locally, `pytest-freezegun` breaks on Python 3.13 (`distutils` import). Run tests with `-p no:freezegun` when they don't need freezegun.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:systematic-debugging (inline) | ~15k | ~5m |
| Implementation | superpowers:test-driven-development (inline) | ~20k | ~10m |
| Review | superpowers:requesting-code-review (general-purpose subagent) | ~58k | ~1m |
| Review | code-eval / doc-sync (inline) | ~8k | ~3m |

**Considered & dropped:** Hard-coded `reverse("advanced_admin:wjs_review_blacklistedauthoremail_changelist")`: it works, but it is tied to one site name and inconsistent with the template.
**Follow-ups:** Optionally rename the custom URL `blacklisted_authoremail_bulk_add` to Django's `wjs_review_blacklistedauthoremail_bulk_add` pattern (view and template), so the non-standard name doesn't mislead again. Fix the local `pytest-freezegun`/Python 3.13 incompatibility in the janeway venv.
**Eval:** 87% — artifacts/evaluations/2026-09-24-blacklist-bulk-add-redirect.md
**Refs:** wjs/specs#3194; plan artifacts/plans/2026-09-24-blacklist-bulk-add-redirect.md; commits a144db4c, a2455c0b.
