# Resend a failed production zip to the publisher (EO action)

Issue: wjs/specs#3450 (main). Related: wjs/specs#2972 (send failure attention condition), wjs/wjs-profile-project!1625.

Language: English (confirmed).

## Intent and why

When a paper moves from ACCEPTED to READY_FOR_TYPESETTER, `SendProductionXMLToPublisher.run()` sends the production
export zip to the publisher (IOP for JCAP). If the send fails (for example JCAP_7961: credentials edited without
restarting the server), the failure is swallowed on purpose and flagged as the EO attention condition
`PRODUCTION_EXPORT_SEND_FAILED`. The paper moves on, IOP never receives the zip, and the EO has no way to send it
again. The only manual path is `manage.py export_production_zip <id> --send`, which needs a developer and runs
silently: no EO message, and the attention condition stays active.

Goal: the EO can resend the zip from the article page. A good resend logs the usual EO message and clears the
attention condition. A failed resend keeps the attention condition, with the new error.

## Goals, non-goals, anti-goals

Goals:
- EO "Resend to publisher" action on the article page, shown only while the attention condition is active.
- Same bookkeeping as the first send (EO message, attention condition resolved on success).
- Immediate feedback to the EO (success or error flash).

Non-goals:
- Blocking the ACCEPTED -> READY_FOR_TYPESETTER transition when the send fails. That behaviour is deliberate (see
  the docstring of `SendProductionXMLToPublisher.run()`) and stays.
- Resending when no failure is flagged (a "successful" send IOP says it never got).
- Any change to the publication bundle flow (!1625) or to `export_production_zip`.

Anti-goals:
- A resend that succeeds but leaves the attention condition active, or the reverse.
- A second code path that builds or sends the zip: the resend must go through `SendProductionXMLToPublisher`.

## Constraints

- Follows `.claude/rules/architecture-django.md`: business logic as a dataclass with `run()` in a transaction, thin
  view, composition rather than subclassing one logic class from another.
- Plugin conventions (`wjs_review/CLAUDE.md`): action visibility goes through `states.py` (`ArticleAction`,
  `permission=` and `condition=`), never hard-coded in templates; new URLs use hyphens; catch
  `(ValueError, ValidationError)` in views.
- The send is synchronous and bounded by the SFTP transport timeouts, as in the existing callers.
- The two existing callers of `SendProductionXMLToPublisher.run()` (`system_verifies_production_requirements`,
  `ConfirmProductionReadiness`/`VerifyProductionRequirements`) must keep working unchanged.

## Approaches

- **A. Dedicated resend action (chosen).** New logic class wrapping `SendProductionXMLToPublisher`, POST-only EO
  view, shared `ArticleAction`. Full bookkeeping, EO can recover alone.
- B. A button that calls `run(silent=True)`. Smaller, but no EO message and the attention condition stays active:
  it repeats the command's gap.
- C. One-off command for JCAP_7961. Fixes one paper, leaves the EO without a retry. Once A ships, 7961 is
  resent from the button.

Visibility condition (chosen: 1): the action shows only while the attention condition is active. Rejected: always
showing it in the production states (permanent clutter on every IOP paper).

## Design

- **Logic** (`logic__production.py`): `ResendProductionXMLToPublisher(articleworkflow)` dataclass, `run()` in a
  transaction. Requirements checked first, `ValidationError` on failure: the journal has a send function
  (`settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS`, already resolved by `_get_send_function`) and the
  `PRODUCTION_EXPORT_SEND_FAILED` attention condition is active for the article. Then it delegates the send to
  `SendProductionXMLToPublisher` in non-silent mode and reports the outcome. On failure the attention condition is
  refreshed with the new message and a `ValidationError` carries the reason to the caller.
- **Outcome reporting:** `SendProductionXMLToPublisher.run()` swallows `SFTPSendError` today. The smallest change
  that keeps both existing callers as they are: a keyword-only option (or a small helper split out of `run()`) that
  lets the resend learn whether the send failed. The exact shape is left to the builder.
- **Condition** (`conditions.py`): predicate "the production export send failed attention condition is active for
  this article", reading the materialised `AttentionCondition` through an `ac_service` or queryset helper (no
  filtering against `.objects` in views or conditions).
- **Action** (`states.py`): one shared `ArticleAction`, EO permission (`has_eo_role_by_article`), the condition
  above, `is_post=True`, confirmation text, added to the production states where the zip can have been sent
  (READY_FOR_TYPESETTER and every later production state before publication), next to
  `PUBLISHER_CONFIRMED_ACTION`.
- **View and URL:** POST-only EO view modelled on `SetPublisherConfirmedFlagView`, hyphenated URL name; it runs
  the logic class, flashes success or the error, and redirects to the article details page.
- **Docs:** update `wjs_review/CLAUDE.md` (attention condition section) with the new write path.

## Testing

- Logic: success sends, logs the EO message and resolves the attention condition; failure keeps it active with
  the new message and raises `ValidationError`; guards (no send function for the journal, attention condition not
  active).
- Condition and action: visible to EO only while the attention condition is active, hidden for other roles and
  once resolved.
- View: EO can POST, others are refused, success and error flashes, redirect.
- Regression: the existing `SendProductionXMLToPublisher` tests still pass unchanged.

## Changes after the independent review

- **Stage forcing.** The export mappers only accept `STAGE_ACCEPTED`, so a resend after typesetting started would
  crash. The resend sets the article stage to accepted in memory while building the zip and restores it afterwards
  (as `export_production_zip --force-accepted` does); it is never saved. The mappers were not loosened: that rule is
  a settled export decision.
- **Stale rows.** After a successful resend every row of the attention condition is resolved, not only those of the
  current EO members, so a former EO's row cannot keep the action visible.
- **States.** The action is limited to ReadyForTypesetter, TypesetterSelected and Proofreading (MR !1643 review).
  `ReadyForPublication`, `PublicationInProgress` and `Published` do not get it: the publication bundle (!1625)
  supersedes the production zip, and a leftover attention condition there can be dismissed. Blocking the move to
  ReadyForPublication while the AC is unresolved is out of scope.

## Left to the builder

Exact shape of the outcome-reporting change, the list of production states that receive the action (derive from
where the attention condition can still be active), labels and confirmation wording, and the URL name.

## Calls made without the user

- Hyphenated URL name (plugin guide advice for new entries).
- Composition (wrap, not subclass) for the logic class.
- No migration is expected: the attention condition code already exists.
