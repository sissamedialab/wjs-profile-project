# Drop the duplicate "Issues after acceptance" notification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the acceptance checks hold a paper in Accepted, the EO gets only the check's own notification (for JCAP TA papers: the TA one), not the generic "Issues after acceptance - article N not ready for typesetters." (wjs/specs#3176).

**Architecture:** Delete `VerifyProductionRequirements._log_acceptance_issues` and its call in `run()` (`logic__production.py`); document in `_perform_checks` that a holding check notifies the EO itself. Two regression tests in `test_ready_for_typesetter_timeline.py`.

**Tech Stack:** Django, django-fsm, Janeway settings (`utils.setting_handler`), pytest.

**Spec:** `artifacts/specs/2026-10-13-drop-acceptance-issues-notification-design.md`

## Global Constraints

- Do **not** touch `wjs/plugins/wjs_review/events/checks_after_acceptance.py`, `wjs/defaults/settings.py`, nor create `tests/test_checks_after_acceptance.py` / `tests/test_import_production_readiness_hold.py`: open MR !1556 (#3131) owns them.
- Tests must not hard-code the JCAP check function name: read it from `settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]` (!1556 renames it).
- Tests run from `janeway/src`: `pytest ../../wjs-profile-project/<path>` (add `-p no:freezegun` if the run fails on Python 3.13 with a freezegun error).
- Documentation language: English. No commits without the developer's approval (commit strategy chosen in flow step 4).

## Review Focus

- A held paper still raises no exception and stays in Accepted: the editor's acceptance must keep working (covered by the existing `test_accepted_workflow_issues[always_reject]`).
- A held paper still gets the `ACCESS_MODE_TO_CHECK` AC for the EO (covered by the existing `test_attentionconditions_accepted.py`; run it in Task 1 Step 5).
- A paper that passes the checks still logs exactly one ready-for-typesetter timeline message (covered by the existing tests in `test_ready_for_typesetter_timeline.py`).
- A JCAP paper that is not TA passes straight to ReadyForTypesetter and sends no EO notification: unchanged code path, not re-tested here (owned by !1556's tests).
- `reverse_lazy` removal must not break other names in the module (flake8 in Task 1 Step 6).

---

### Task 1: Drop the generic acceptance-issues notification

**Files:**
- Modify: `wjs/plugins/wjs_review/logic__production.py:44` (import), `:112-123` (`_perform_checks` docstring), `:125-146` (delete `_log_acceptance_issues`), `:184-190` (`run()`)
- Modify: `wjs/plugins/wjs_review/tests/test_workflow.py:121` (drop TODO)
- Test: `wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py`

**Interfaces:**
- Consumes: `VerifyProductionRequirements(articleworkflow).run()`, `_accept_with_checks(...)` and `HOLD_IN_ACCEPTED` already defined in the test module.
- Produces: nothing new; `VerifyProductionRequirements._log_acceptance_issues` no longer exists.

- [ ] **Step 1: Write the failing tests**

Append to `wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py`, and add the imports at the top of the module (keep isort order):

```python
from plugins.wjs_review.logic__production import ConfirmProductionReadiness, VerifyProductionRequirements
from plugins.wjs_submission.models import AccessMode
from plugins.wjs_submission.settings import OA_CODE_TA
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import get_eo_user, render_template
```

(`ConfirmProductionReadiness` and `get_eo_user` are already imported: merge, don't duplicate.)

```python
ISSUES_AFTER_ACCEPTANCE = "Issues after acceptance"


def _article_messages(article: Article):
    return Message.objects.filter(content_type=ContentType.objects.get_for_model(article), object_id=article.pk)


def _set_ta(article: Article) -> None:
    """Give the article the transformative-agreement access mode."""
    access_mode = AccessMode.objects.filter(code=OA_CODE_TA).first() or AccessMode.objects.create(
        name="Transformative agreement", code=OA_CODE_TA
    )
    submission_data = article.submission_data
    submission_data.access_mode = access_mode
    submission_data.save()


@pytest.mark.django_db
def test_held_in_accepted_sends_no_generic_issues_message(assigned_article, fake_request, settings):
    """Checks fail: no generic "Issues after acceptance" message is sent (specs#3176)."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "checks should hold the paper"
    assert not _article_messages(article).filter(
        subject__startswith=ISSUES_AFTER_ACCEPTANCE
    ).exists(), "the generic acceptance-issues message must not be sent"


@pytest.mark.django_db
def test_jcap_ta_paper_held_sends_one_eo_message(assigned_article, fake_request, settings):
    """A JCAP TA paper held in Accepted: the EO gets only the TA message (specs#3176)."""
    # The JCAP check functions as configured, whatever their name. Read before
    # _accept_with_checks, which replaces the whole setting.
    jcap_checks = settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    _set_ta(article)
    _article_messages(article).delete()
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: jcap_checks}
    eo = get_eo_user(article)

    VerifyProductionRequirements(article.articleworkflow).run()

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "TA paper must be held"
    eo_messages = _article_messages(article).filter(recipients=eo)
    assert eo_messages.count() == 1, "EO must receive exactly one message"
    ta_subject = render_template(
        get_setting(
            setting_group_name="wjs_review", setting_name="jcap_ta_pending_subject", journal=article.journal
        ).processed_value,
        {"article": article},
    )
    assert eo_messages.get().subject == ta_subject, "the only EO message must be the TA one"
```

Note: `jcap_checks` must be read **before** `_accept_with_checks`, which overwrites `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` with a single-journal dict (the `JCAP` key would be gone).

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `janeway/src`): `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py -v -k "generic_issues or one_eo_message"`
Expected: both FAIL — first on "the generic acceptance-issues message must not be sent", second on "EO must receive exactly one message" (2 messages).

- [ ] **Step 3: Implement**

In `wjs/plugins/wjs_review/logic__production.py`:

1. Delete the line `from django.urls import reverse_lazy` (its only use is in the method deleted below).
2. Replace the `_perform_checks` docstring and drop the stale `# TODO: how do we report issues?`:

```python
    def _perform_checks(self) -> bool:
        """Apply functions that verify if an accepted article is ready for typs.

        A check function that returns False holds the article in ACCEPTED: it must notify the EO itself
        (e.g. ``jcap_ta_not_yet_confirmed``), since nothing else is sent here (specs#3176). The EO also gets
        the ACCESS_MODE_TO_CHECK attention condition.
        """
        journal = self.articleworkflow.article.journal.code
        checks_functions = settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS.get(
            journal,
            settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS.get(None, []),
        )
        for check_function in checks_functions:
            if not import_string(check_function)(self.articleworkflow.article):
                return False
        return True
```

3. Delete the whole `_log_acceptance_issues` method.
4. In `run()`, replace

```python
            if not self._check_conditions():
                # Here we do not raise an exception, because doing so would prevent an editor from accepting an
                # article. Instead we send a message to EO.
                self._log_acceptance_issues()

                # -- Materialized AC updates --
```

with

```python
            if not self._check_conditions():
                # Here we do not raise an exception, because doing so would prevent an editor from accepting an
                # article. The check function that held the article has already notified the EO.

                # -- Materialized AC updates --
```

In `wjs/plugins/wjs_review/tests/test_workflow.py`, in `test_accepted_workflow_issues`, delete the line
`    # TODO: check for a message to the EO after approach is validated`.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the neighbouring suites**

Run: `pytest -n7 ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_workflow.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_production.py`
Expected: no new failures. Seven tests in `test_production.py` were known to fail on a clean `wjs-develop` (activity log 2026-09-29): compare any failure against `git stash` + rerun on `wjs-develop` before attributing it to this change.

- [ ] **Step 6: Lint and check for leftovers**

Run (from `wjs-profile-project`): `grep -rn "_log_acceptance_issues\|Issues after acceptance" wjs/ --include=*.py` — expected: only the new test constant.
Run: `pre-commit run --files wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py wjs/plugins/wjs_review/tests/test_workflow.py`
Expected: all hooks pass.

- [ ] **Step 7: Commit**

Per the commit strategy chosen in flow step 4 (default: leave the changes in the working tree; commits are proposed for approval in flow step 10). Proposed message:

```
fix(wjs_review): drop the duplicate "Issues after acceptance" EO notification (#3176)
```
