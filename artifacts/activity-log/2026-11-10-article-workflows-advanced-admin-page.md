## 2026-11-10 — Article workflows page in the advanced admin
**What:** New `ArticleWorkflowAdmin`, registered on a proxy model in `wjs_review/advanced_admin/admin.py`. It lists workflows with the shared article search and filters, with the workflow-state filter on `state`. Users can edit `state` directly. Add and delete are disabled.
**Why:** specs#3329. The EO needed a way to unstick papers from the advanced admin. The stock `/admin/` already allows a raw state edit, because `FSMField` is not `protected`.
**Decisions:**
- **Raw edit, not a transition (option A).** The form warns that saving runs no transition, notification, workflow log or `latest_state_change` update.
- **Test fixture.** The wjs_review tests star-import the `jcom_profile` conftest, so the `unaccent` extension fixture goes there.
- **Requirements bumped.** wjs-profile now requires `wjs-themes >= 2.1.11` and `wjs-submission >= 2.1.9`.
**Follow-ups:** The warning says "no log entry", but Django's admin LogEntry is still written. Consider rewording it to "no workflow log entry".
**Refs:**
- https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3329
- wjs-themes `artifacts/activity-log/2026-11-10-shared-article-admin-search-filters.md`
- `wjs/plugins/wjs_review/CLAUDE.md`, section "Raw state edit in the advanced admin"
