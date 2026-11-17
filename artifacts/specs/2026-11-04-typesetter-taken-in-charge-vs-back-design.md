# Typesetter: "Taken in charge" vs "Back to typesetter" — design

- Issue: `specs#3120` (https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3120)
- Repos touched: `wjs-profile-project` (this repo) and `wjs-themes` (sibling checkout,
  `/home/yakky/Projects/sissa/wjs-themes`, branch from latest `wjs-develop`)
- Documentation language: English

## Intent

A paper in the typesetter's hands is always labelled "Typesetter selected", whether it was just
assigned or came back with the author's corrections (or was sent back by the EO from "Ready for
publication"). The two situations need different tools, timings and checks, and typesetters must be
able to see the volume of each to plan the work. On high-volume journals (JCAP and similar) this is
mandatory. The typesetter's "Working on" list also does not show the production version, which the EO
production list already shows.

## Goals

1. While `ArticleWorkflow.state == TypesetterSelected`, show one of two labels everywhere the state
   label is shown (paper lists, article page, G8 typesetter-monitoring API `status.name`):
   - **"Taken in charge"** — the current typesetting round is round 1 (no typeset version delivered yet).
   - **"Back to typesetter"** — the current typesetting round is > 1 (author sent corrections, or the
     EO sent the paper back from "Ready for publication").
2. Each label has its own badge colour, accessible and clearly distinguishable from the other.
3. The typesetter's "Working on" list shows the production version beside the article id.
4. The "Pending issues" page shows the version beside the preprint/article id.

## Non-goals

- Redesign of the typesetter flow (separate issue, see specs#3082).
- Changing `ArticleWorkflow.state` / the FSM: no new stored states, no new transitions, no migration.
- Changing `status.code` in the G8 API (stays the raw stored state, `TypesetterSelected`).
- Diversifying typesetting rounds (specs#1461).
- Counting, filtering or sorting lists by the two labels. "Seeing the volume" is met by the labels
  themselves, as the issue's Expected behaviour describes; no new filters or DB annotations.
- Translations: the existing state labels are not in `wjs/locale/*/django.po` either, so no `.po`
  changes (Claude's call).

## Anti-goals (failure even if it "works")

- Any `@transition`, permission, `states.py` action or queryset that filters on `state` changing
  behaviour. Computed states are display-only (see `wjs/plugins/wjs_review/CLAUDE.md`).
- A colour that collides visually with an existing state badge a typesetter sees in the same lists, or
  that fails contrast.
- Version format diverging between lists.

## Decisions

### Computed states (wjs-profile-project)

- Add to `ArticleWorkflow.ReviewComputedStates` (`wjs/plugins/wjs_review/models.py`):
  - `TAKEN_IN_CHARGE = "taken_in_charge", _("Taken in charge")`
  - `BACK_TO_TYPESETTER = "back_to_typesetter", _("Back to typesetter")`
- In `ArticleWorkflow.state_value`, before the existing "working on" early-return, handle
  `state == ReviewStates.TYPESETTER_SELECTED`: look at the latest `TypesettingRound` of the article
  (`TypesettingRound` is ordered by `-round_number`, so `.first()` is the latest — same lookup as the
  `versioned_number` filter):
  - latest round number `> 1` → `BACK_TO_TYPESETTER`
  - otherwise (round 1, or — defensively — no round at all) → `TAKEN_IN_CHARGE`
- Why this rule holds: round 1 is created with the first typesetting assignment
  (`_create_typesetting_round`, `logic__production.py` ≈ l.426); both `AuthorSendsCorrections` and
  `HandleEOSendBackToTypesetter` create `old_round + 1` (≈ l.999 and l.1739). Re-assigning a typesetter
  within the same round does not bump the round, so it keeps "Taken in charge" — intended.
- `state_label` needs no change: it already resolves `ReviewComputedStates` labels.
- Consumers that pick up the change with no edits: `table_item_status.html` (all lists),
  `details/sections/title.html` (article page), `issue/table_article.html` badge,
  `article_classes.state_css` (`color-state-<state_value>`), `api/serializers.py::get_status`
  (update its comment to say the distinction is now implemented). The G8 `status.name` changing from
  "Typesetter selected" to the new labels is intended: the serializer was written expecting it
  (wjs-profile-project!1503); `status.code` stays `TypesetterSelected`.
- Existing `state_value` comparisons in `templatetags/wjs_review.py` only test review-phase values, so
  they are unaffected. The builder must still grep for `state_value` comparisons against
  `TYPESETTER_SELECTED` (none found at design time) and keep it that way.

### Colours (wjs-themes)

- `JCOM-theme` no longer exists on `wjs-develop`; the only file to change is
  `wjs/themes/wjs-bootstrap/assets/sass/_badges_colors.scss`.
- Add `$color-state-taken_in_charge` / `$hover-state-…` and `$color-state-back_to_typesetter` /
  `$hover-state-…` variables, the matching `&-taken_in_charge` / `&-back_to_typesetter` blocks inside the
  `color-state` mixin (same shape as `&-TypesetterSelected`), and the two class names in the header
  comment list.
- Keep `$color-state-TypesetterSelected` and its block (still the fallback / used by
  `workflow_states` command output).
- Current palette is light backgrounds with dark text (`#1e2124`). Reds (Rejected `#f05151`, Not suitable
  `#f88a8a`), oranges (requested revisions) and purples (In review, Resubmitted major) are taken.
- **Claude's call, to be validated by the user on the test environment:**
  - Taken in charge: red, as the issue requires. Pick a hue visibly distinct from Not suitable
    (`#f88a8a`) and Rejected (`#f05151`) — e.g. a rose/crimson-leaning red such as `#fb7185` — keeping
    in mind the typesetter list also shows a 🔴 "unpublishable" marker under the badge. Dark text.
  - Back to typesetter: dark red, e.g. `#991b1b`, **white text** (block overrides text colour). Differs
    in both hue and lightness from the other label so it reads for colour-blind users.
  - Both must meet WCAG AA (≥ 4.5:1) text contrast; verify the final values with a contrast check.
- Compiled CSS: follow wjs-themes' existing build convention (`build_assets.py` / `build_assets.sh`);
  the builder checks whether compiled CSS is committed and, if so, regenerates it.

### Version numbers (wjs-profile-project templates)

- Keep the existing `versioned_number` filter format `<pk>/v<round>` (e.g. `3613/v1`), as on the EO
  production list — user decision.
- `templates/wjs_review/lists/elements/typesetter/table_item.html`: the link text `{{ article.pk }}`
  becomes `{{ article|versioned_number }}`.
- `templates/wjs_review/lists/elements/issue/table_article.html`: same replacement for the article link
  text (≈ l.17). Both templates already `{% load wjs_articles %}`.

## Testing

- Unit tests on `state_value` / `state_label` (real DB, existing fixtures/factories in
  `wjs/plugins/wjs_review/tests/conftest.py`): TypesetterSelected with round 1 → taken in charge;
  after author corrections → back to typesetter; after EO send-back from ReadyForPublication → back to
  typesetter; other states unchanged (e.g. Proofreading still returns raw state).
- Template/view tests: typesetter "Working on" list and pending-issues list render `<pk>/v<n>`.
- API test: G8 serializer `status.name` returns the new labels (extend existing serializer tests if any).
- Manual: run the issue's functional test steps on a test environment, including JCAP and JCOM.

## Left to the builder

- Exact final hex values within the decision above (after contrast check).
- Whether to prefetch typesetting rounds in the affected list views to avoid N+1 (the badge and the
  version filter each query `TypesettingRound` per row); do it only if cheap and clean.
- Test placement (new test module vs extending `test_production.py`).

## Delivery

- Two branches with the same name, one per repo, two MRs into `wjs-develop`. The wjs-themes MR must be
  deployed together with (or before) the wjs-profile-project one, otherwise the new badges render
  without a background colour. The `wjs-themes >= …` pin in `setup.cfg` is **not** changed in this MR;
  it is bumped at release time (user decision).
- MR footer: `main: specs#3120`, no closing keywords.
