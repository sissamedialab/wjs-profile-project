## 2026-11-13 — Papers lists show the warning and mail attention icons side by side
**What:** `lists/elements/attention.html` now reads `article_attention` (from the listing prefetch) instead of the
legacy single-message flag: a warning icon for the active normal ACs and, independently, a mail icon for unread
messages.
**Why:** QA on wjs/specs#3272 (note 82017): with both a delay and unread messages only one icon was shown. Neither
the backend MR (!1620) nor the frontend MR (!1622) had touched the lists, so they still showed only the most urgent
AC (for EO, unread messages first).
**Decisions:**
- Two plain Bootstrap dropdowns on the existing `wjs-attention-dropdown-menu` (popover-looking, styled in
  wjs-themes) instead of Bootstrap popovers with a custom script. It matches how !1622 built the article panel
  (Bootstrap classes and existing theme classes only, no new JS or wjs-themes change). Bootstrap already gives
  click/Enter/Space opening, no hover, Esc with focus back on the icon, closing on outside click and Tab, and
  `aria-expanded`.
- Minimal diff: only `attention.html` changes. The six `*/table_item.html` keep the legacy `attention_flag` for the
  row highlight and bold cells; it is non-empty exactly when `has_active` is true (`legacy_message` picks from
  active + unread), so LST-7…9 already hold.
- `wjs/plugins/wjs_review/CLAUDE.md` is left as it is: !1622 rewrites the same paragraph, editing it here would
  conflict.
**Considered & dropped:** Bootstrap popovers plus a small `attention_popovers.js` (written first, matching the
spec's wording): dropped to stay consistent with !1622 and avoid new JS. Moving the six row templates to
`article_attention` too: same behaviour, larger diff, postponed to the legacy cleanup.
**Follow-ups:** after !1622 is merged into the backend branch, move the row highlight to `attention.has_active`,
drop `article_requires_attention_tt` + `_prefetched_acs` once nothing uses them, and update the CLAUDE.md read-path
paragraph. Icon size is Bootstrap's `p-2 fs-6`, not exactly LST-4's 24/16 px; a pixel-exact badge would need a
wjs-themes change.
**Refs:** wjs/specs#3272 (note 82017), wjs/wjs-profile-project!1620, !1622.
