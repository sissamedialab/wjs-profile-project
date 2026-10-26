# Typesetter-late AC threshold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the `typesetter_late` attention condition only from *last action + 4 days* (i.e. when the typesetter is at least 1 day late), with correct "day"/"days" wording.

**Architecture:** A single change in `conditions.is_typesetter_late()`: strict `today > due` instead of `today >= due`, "today" taken as `timezone.localdate()`, and a singular/plural message. The AC evaluator (`ac_service._evaluate_typesetter_late`) is unchanged: it already upserts/resolves the AC from that function's return value.

**Tech Stack:** Django, pytest + pytest-django, freezegun.

**Spec:** `artifacts/specs/2026-10-15-typesetter-late-ac-threshold-design.md`

## Global Constraints

- `TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS` (3, `wjs/defaults/settings.py`) and the way `due` is computed in `logic__production.py` stay unchanged.
- `conditions.is_author_proofing_late()` follows the same rule (added in Task 2 after the stakeholder's confirmation).
- Messages: "Typesetter is N days late" / "You are N days late", with "1 day late" for N == 1. Not translated (plain f-strings, like the rest of `conditions.py`).
- No data migration.
- Tests run from `janeway/src` (see `.claude/rules/tests.md`); never run the whole suite unless needed.
- Documentation language: English.

## Review Focus

- **Local midnight vs UTC:** just after local midnight of `due + 1` (Europe/Rome) the UTC date is still `due`; a reasonable user expects the AC that morning → pinned by `test_is_typesetter_late_uses_local_date`.
- **Assignment without `due` / no assignment:** must return `""`, never raise → pinned by `test_is_typesetter_late_without_due`.
- **Stale "0 days late" rows already in the DB:** must disappear on re-evaluation → pinned by `test_evaluator_resolves_typesetter_late_when_no_longer_late`.
- **Typesetter who is also EO:** the personal wording must win on the shared row → existing write ordering in `_evaluate_typesetter_late` is untouched; no new test (behaviour not changed by this plan).
- **Several typesetting rounds on one article:** the evaluator loops over all assignments; behaviour per assignment is unchanged apart from the threshold; no new test.

---

### Task 1: Strict threshold, local date and pluralization in `is_typesetter_late`

**Files:**
- Create: `wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py`
- Modify: `wjs/plugins/wjs_review/conditions.py:569-583` (`is_typesetter_late`)

**Interfaces:**
- Consumes: `conditions.is_typesetter_late(assignment: TypesettingAssignment, for_typesetter: bool = False) -> str` (existing signature, unchanged); `ac_service.TYPESETTER_LATE`; fixtures `assigned_to_typesetter_article`, `typesetter` (`tests/conftest.py`); helper `attention_conditions_rebuild` (`tests/test_helpers.py`).
- Produces: same signature; return value `""` or the late message.

- [ ] **Step 1: Write the failing tests**

Create `wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py`:

```python
"""Typesetter-late attention condition (specs#3264).

The AC must start on *last action + 4 days*: the assignment's ``due`` is
*last action + TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS* (3), so the typesetter
is late only from the day after ``due``.
"""

import datetime
from datetime import timedelta

import freezegun
import pytest
from django.utils import timezone
from plugins.wjs_review import ac_service, conditions
from plugins.wjs_review.models import AttentionCondition
from submission.models import Article
from typesetting.models import TypesettingAssignment

from wjs.jcom_profile.utils import get_eo_user

from .test_helpers import attention_conditions_rebuild

DUE = datetime.date(2026, 10, 13)
"""Due date of an assignment made on 10.10: the AC must start on 14.10."""


def _local(day: datetime.date, hour: int = 12, minute: int = 0) -> datetime.datetime:
    """Return an aware datetime at the given local time of ``day``."""
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


@pytest.mark.parametrize(
    "today,expected_eo,expected_typesetter",
    (
        (DUE - timedelta(days=1), "", ""),
        (DUE, "", ""),
        (DUE + timedelta(days=1), "Typesetter is 1 day late", "You are 1 day late"),
        (DUE + timedelta(days=3), "Typesetter is 3 days late", "You are 3 days late"),
    ),
)
def test_is_typesetter_late(today, expected_eo, expected_typesetter):
    """The typesetter is late only from the day after the due date."""
    assignment = TypesettingAssignment(due=DUE)
    with freezegun.freeze_time(_local(today)):
        assert conditions.is_typesetter_late(assignment) == expected_eo
        assert conditions.is_typesetter_late(assignment, for_typesetter=True) == expected_typesetter


def test_is_typesetter_late_without_due():
    """No assignment or no due date: never late."""
    assert conditions.is_typesetter_late(None) == ""
    assert conditions.is_typesetter_late(TypesettingAssignment(due=None)) == ""


def test_is_typesetter_late_uses_local_date(settings):
    """Just after local midnight of due + 1 the typesetter is late, even if the UTC date is still the due date."""
    settings.TIME_ZONE = "Europe/Rome"
    assignment = TypesettingAssignment(due=DUE)
    with freezegun.freeze_time(_local(DUE + timedelta(days=1), hour=0, minute=30)):
        assert timezone.now().date() == DUE  # precondition: UTC is still on the due date
        assert conditions.is_typesetter_late(assignment) == "Typesetter is 1 day late"


def _typesetter_late_acs(article: Article):
    return AttentionCondition.objects.active().filter(article=article, code=ac_service.TYPESETTER_LATE)


def _set_due(article: Article, due: datetime.date) -> None:
    assignment = TypesettingAssignment.objects.get(round__article=article)
    assignment.due = due
    assignment.save()


@pytest.mark.django_db
def test_evaluator_typesetter_late_starts_day_after_due(assigned_to_typesetter_article, typesetter):
    """The evaluator creates no AC on the due date, and the AC for typesetter and EO the day after."""
    article = assigned_to_typesetter_article
    _set_due(article, DUE)

    with freezegun.freeze_time(_local(DUE)):
        attention_conditions_rebuild(article)
    assert not _typesetter_late_acs(article).exists()

    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert _typesetter_late_acs(article).get(user=typesetter).message == "You are 1 day late"
    assert _typesetter_late_acs(article).get(user=get_eo_user(article)).message == "Typesetter is 1 day late"


@pytest.mark.django_db
def test_evaluator_resolves_typesetter_late_when_no_longer_late(assigned_to_typesetter_article):
    """An existing AC (e.g. a "0 days late" row from the old rule) is resolved when re-evaluated on the due date."""
    article = assigned_to_typesetter_article
    _set_due(article, DUE)
    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert _typesetter_late_acs(article).exists()

    _set_due(article, DUE + timedelta(days=1))
    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert not _typesetter_late_acs(article).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `janeway/src`):
`pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py -v`

Expected: FAIL — the `DUE` case returns "Typesetter is 0 days late", the 1-day cases return "… 1 days late", `test_is_typesetter_late_uses_local_date` returns `""`, the evaluator tests find an AC on the due date / a "1 days late" message. `test_is_typesetter_late_without_due` and the `DUE - 1` / `DUE + 3` cases already pass (regression guards).

- [ ] **Step 3: Implement**

In `wjs/plugins/wjs_review/conditions.py` replace `is_typesetter_late` with:

```python
def is_typesetter_late(assignment: TypesettingAssignment, for_typesetter: bool = False) -> str:
    """Tell if the typesetter is late with the assignment.

    The typesetter is late from the day after the due date (specs#3264): since ``due`` is set to the assignment
    date + ``TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS``, the AC starts on the last action + 4 days.

    With for_typesetter=True the message addresses the typesetter directly
    (the AC is shown to the typesetter themselves).
    """
    if not assignment or not assignment.due:
        return ""
    today = timezone.localdate()
    if today > assignment.due:
        days = (today - assignment.due).days
        days_late = f"{days} day{'' if days == 1 else 's'} late"
        if for_typesetter:
            return f"You are {days_late}"
        return f"Typesetter is {days_late}"
    else:
        return ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `janeway/src`):
`pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the neighbouring AC and production tests for regressions**

Run (from `janeway/src`):
`pytest -n7 ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_rfp.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_production.py`
Expected: no new failures (compare any failure against `wjs-develop` before attributing it to this change).

- [ ] **Step 6: Lint**

Run (from the repo root): `pre-commit run --files wjs/plugins/wjs_review/conditions.py wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py`
Expected: clean (re-run if hooks auto-fix formatting).

- [ ] **Step 7: Commit (only if per-change commits were chosen in flow step 4)**

```bash
git add wjs/plugins/wjs_review/conditions.py wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py
git commit -m "fix: show typesetter-late AC only from the day after the due date"
```
Otherwise leave the changes in the working tree.

---

> **Amendment (2026-10-15):** the stakeholder confirmed that the rule also applies to the author-proofing AC. Task 2 below was added after Task 1 was complete and its final review was clean.

### Task 2: Same threshold for `is_author_proofing_late`

**Files:**
- Create: `wjs/plugins/wjs_review/tests/test_attentionconditions_proofing.py`
- Modify: `wjs/plugins/wjs_review/conditions.py` (`is_typesetter_late`, `is_author_proofing_late`, new helper `_days`)
- Modify: `wjs/plugins/wjs_review/tests/test_attentionconditions.py` (`test_author_proofing_is_late`: expected date in local time)

**Interfaces:**
- Consumes: `conditions.is_author_proofing_late(assignment: GalleyProofing) -> str` (signature unchanged); `ac_service.AUTHOR_PROOFING_LATE`; fixture `stage_proofing_article`; helper `attention_conditions_rebuild`.
- Produces: private `conditions._days(days: int) -> str` returning "1 day" / "N days", used by both late conditions.

- [ ] **Step 1: Verify the bug is real (RED)** — write `test_attentionconditions_proofing.py`:

```python
"""Author-proofing-late attention condition (specs#3264).

As for the typesetter: the author is late only from the day after the due
date. ``GalleyProofing.due`` is a datetime: production stores the local
midnight of the due day (a date is assigned), read back from the DB in UTC.
"""

import datetime
from datetime import timedelta

import freezegun
import pytest
from django.utils import timezone
from plugins.wjs_review import ac_service, conditions
from plugins.wjs_review.models import AttentionCondition
from submission.models import Article
from typesetting.models import GalleyProofing

from wjs.jcom_profile.utils import get_eo_user

from .test_helpers import attention_conditions_rebuild

DUE = datetime.date(2026, 10, 13)


def _local(day: datetime.date, hour: int = 12, minute: int = 0) -> datetime.datetime:
    """Return an aware datetime at the given local time of ``day``."""
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


def _stored_due(day: datetime.date) -> datetime.datetime:
    """Return ``due`` as production stores it and reads it back: local midnight, in UTC."""
    return _local(day, hour=0).astimezone(datetime.timezone.utc)


def _late_message(days: str) -> str:
    return f"Proofing is late by {days}. Was expected by 2026-10-13. Please contact the author."


@pytest.mark.parametrize(
    "now,expected",
    (
        (_local(DUE), ""),
        (_local(DUE, hour=23, minute=59), ""),
        (_local(DUE + timedelta(days=1), hour=0, minute=30), _late_message("1 day")),
        (_local(DUE + timedelta(days=3)), _late_message("3 days")),
    ),
)
def test_is_author_proofing_late(now, expected):
    """The author is late only from the day after the due date; the expected date is the local one."""
    assignment = GalleyProofing(due=_stored_due(DUE))
    with freezegun.freeze_time(now):
        assert conditions.is_author_proofing_late(assignment) == expected


def test_is_author_proofing_late_without_due():
    """No assignment or no due date: never late."""
    assert conditions.is_author_proofing_late(None) == ""
    assert conditions.is_author_proofing_late(GalleyProofing(due=None)) == ""


def _author_proofing_late_acs(article: Article):
    return AttentionCondition.objects.active().filter(article=article, code=ac_service.AUTHOR_PROOFING_LATE)


@pytest.mark.django_db
def test_evaluator_author_proofing_late_starts_day_after_due(stage_proofing_article):
    """The evaluator creates no AC on the due date, and the EO's AC the day after."""
    article = stage_proofing_article
    assignment = GalleyProofing.objects.get(round__article=article, proofreader=article.correspondence_author)
    assignment.due = DUE  # a date, as RequestProofs assigns it
    assignment.save()

    with freezegun.freeze_time(_local(DUE)):
        attention_conditions_rebuild(article)
    assert not _author_proofing_late_acs(article).exists()

    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert _author_proofing_late_acs(article).get(user=get_eo_user(article)).message == _late_message("1 day")
```

- [ ] **Step 2: Run it** (from `janeway/src`): `pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_proofing.py -v`
Expected: FAIL — on the due date "Proofing is late by 0 days. Was expected by 2026-10-12 …" (the bug, plus the off-by-one expected date); "1 days"/"3 days … 2026-10-12" mismatches. The "without due" test passes (guard).

- [ ] **Step 3: Implement** in `wjs/plugins/wjs_review/conditions.py`: add the helper above `is_typesetter_late`, use it there, and rewrite `is_author_proofing_late`:

```python
def _days(days: int) -> str:
    """Return "1 day" / "N days"."""
    return f"{days} day{'' if days == 1 else 's'}"
```

In `is_typesetter_late`: `days_late = f"{_days(days)} late"`.

```python
def is_author_proofing_late(assignment: GalleyProofing) -> str:
    """Tell if the author is late with the proofing assignment.

    As for :py:func:`is_typesetter_late`, the author is late from the day after the due date (specs#3264).
    ``due`` is a datetime (production stores the local midnight of the due day): compare local dates.
    """
    if not assignment or not assignment.due:
        return ""
    due = timezone.localtime(assignment.due).date()
    today = timezone.localdate()
    if today > due:
        return (
            f"Proofing is late by {_days((today - due).days)}."
            f" Was expected by {due.strftime('%F')}."
            " Please contact the author."
        )
    else:
        return ""
```

- [ ] **Step 4: Align `test_author_proofing_is_late`** (`tests/test_attentionconditions.py`): it builds the expected message from the UTC `due`; compute it from local dates so it matches at any hour:

```python
    due = timezone.localtime(assignment.due).date()
    assert (
        state_cls.article_requires_attention(article=article, user=eo)
        == f"Proofing is late by {(timezone.localdate() - due).days} days."
        f" Was expected by {due.strftime('%F')}."
        " Please contact the author."
    )
```

- [ ] **Step 5: Run** (from `janeway/src`): `pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_proofing.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint**: `pre-commit run --files` on the changed files. Expected: clean.

- [ ] **Step 7:** leave changes in the working tree (flow step 4 choice).

---

> **Amendment 2 (2026-10-15):** after the final review, the user asked to fix the proofing due-date computation too (small change). Task 3 below.

### Task 3: `RequestProofs` computes the proofing `due` from the local date

**Files:**
- Modify: `wjs/plugins/wjs_review/logic__production.py:557-566` (`RequestProofs._create_proofing_assignment`)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions_proofing.py`

**Interfaces:**
- Consumes: `_stage_proofing_article(article, typesetter, fake_request)` (`tests/conftest.py`), fixture `assigned_to_typesetter_article_with_files_to_typeset`; setting `PROOFING_ASSIGNMENT_MAX_DUE_DAYS`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test** (append to `test_attentionconditions_proofing.py`, import `_stage_proofing_article` from `.conftest`):

```python
@pytest.mark.django_db
def test_request_proofs_due_date_counts_from_the_local_date(
    assigned_to_typesetter_article_with_files_to_typeset, typesetter, fake_request, settings
):
    """Just after local midnight (UTC still on the previous day) the due date counts from the local today."""
    local_today = datetime.date(2026, 10, 14)
    with freezegun.freeze_time(_local(local_today, hour=0, minute=30)):
        assert timezone.now().date() == local_today - timedelta(days=1)  # precondition: UTC is still on the 13th
        article = _stage_proofing_article(assigned_to_typesetter_article_with_files_to_typeset, typesetter, fake_request)
    assignment = GalleyProofing.objects.get(round__article=article, proofreader=article.correspondence_author)
    expected = local_today + timedelta(days=settings.PROOFING_ASSIGNMENT_MAX_DUE_DAYS)
    assert timezone.localtime(assignment.due).date() == expected
```

- [ ] **Step 2: Run it** — Expected: FAIL, due is one day earlier (2026-10-20 instead of 2026-10-21).
- [ ] **Step 3: Implement** — in `_create_proofing_assignment` replace both `timezone.now().date()` with `timezone.localdate()`.
- [ ] **Step 4: Run** the proofing, typesetter and `test_attentionconditions.py` modules plus `test_production.py`. Expected: new test PASS; no new failures (the 6 pre-existing `test_production.py` failures stay identical).
- [ ] **Step 5: Lint** the changed files. Expected: clean.
