## 2026-10-06 — Timeline message for Accepted → Ready for typesetter
**What:** The transition from Accepted to Ready for typesetter now leaves a TIMELINE-only message ("Paper ready for typesetter"), both when the acceptance checks pass automatically (`VerifyProductionRequirements`) and when the EO confirms production readiness (`ConfirmProductionReadiness`). Text comes from two new `wjs_review` settings: `ready_for_typesetter_subject` and `ready_for_typesetter_body`.
**Why:** JCAP go-live feedback (specs#3178, related to #3162): the EO action "confirm production readiness" was invisible in the article history.
**Decisions:**
- One helper, `log_ready_for_typesetter(article, actor=None)`, called inside each action's atomic block right after the FSM transition.
- The helper only logs, with no role or permission check, at the user's explicit request: authorization belongs to the action (`has_transition_perm`). The parameter is a neutral `actor`, not `confirmed_by`.
- The recipient is the EO system account, marked as read, mirroring `ProductionComplete._log_operation`. No email, and no unread-message AC.
- The texts are settings rather than hardcoded strings, so they can be customized per journal.

**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming + writing-plans (inline) | ~60k | ~25m |
| Implementation | superpowers:executing-plans (inline) | ~80k | ~40m |
| Review | code reviewer subagent (opus) | ~75k | ~2m |
| Review | code-eval + doc-sync (inline) | ~10k | ~3m |

**Considered & dropped:**
- Hardcoded subject and body, dropped because they couldn't be customized.
- A test for a refused or repeated confirmation, dropped by user decision: that test covers the action's authorization, not the logging.
- Adjusting the message counts in `test_handle_editor_decision`. Instead the test asserts the new message and then removes it, the same idiom it already uses for the review-withdraw notice.

**Follow-ups / gotchas:**
- `_accept_article(..., cleanup_side_effects=True)` (the default) deletes messages. Tests that assert on acceptance messages must pass `False`.
- In this environment, pytest needs `-p no:freezegun`, because pytest-freezegun imports `distutils`, which Python 3.13 no longer has.
- Pre-existing failures, identical on base code: 6 tests in `test_production.py` (galley generation and publication) and `test_logic.py::test_withdraw_preprint_press_notification[rfp_article-5]`.
- Instances not deployed through `run_customizations` must run `setup_review_settings`. Otherwise `get_setting` raises and the Accept decision rolls back.

**Refs:** wjs/specs#3178; spec `artifacts/specs/2026-10-06-ready-for-typesetter-timeline-design.md`; plan `artifacts/plans/2026-10-06-ready-for-typesetter-timeline.md`.
Eval: 80% — artifacts/evaluations/2026-10-06-ready-for-typesetter-timeline.md
