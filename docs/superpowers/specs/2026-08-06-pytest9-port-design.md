# Port test suite to pytest 9 + latest pytest-django

## Context

`setup.cfg`'s `test` extra currently pins `pytest == 8.3.3` with the comment
`# temporary workaround for wjs-profile-project#204`. Issue #204
("As developer I want to investigate why pytest 8.4 breaks our tests setup")
has been open since 2025-06-03 and was never root-caused — the pin was applied
as a stopgap and the investigation never happened.

On 2026-08-06, the sibling repo `wjs-submission-project` hit an analogous
problem one layer up the stack (MR !136, "chore: pin pytest / pytest-django"):
`pytest-django` 4.13 broke things, and was pinned to `< 4.13` without
root-causing it either. Checking pytest-django's changelog confirms this is
not a bug but a hard version-compatibility fact: **pytest-django 4.13.0
(released 2026-08-06) dropped support for Django 4.2 and 5.1.** This project
depends on `Django ~= 4.2` (`setup.cfg`), so `pytest-django` is durably capped
below 4.13 until Django itself is upgraded — no bisection needed for that half
of the problem.

Additionally:

- `pytest-freezegun` (providing the `freezer` fixture used across the test
  suite for `freeze_time`-style tests) is unmaintained since ~2020. Upstream's
  own recommended migration is `pytest-freezer`, a drop-in replacement that
  keeps the same `freezer` fixture name — expected to require no test-code
  changes.
- `pytest-mock`, `pytest-xdist`, `pytest-reverse`, `pytest-factoryboy` are all
  actively maintained; bumping them to latest is expected to be uneventful.
- pytest 9 requires Python >= 3.10. `setup.cfg` currently declares
  `python_requires = >=3.9`, which is already inaccurate — CI (`python:3.11`
  image) and the local dev venv both run Python 3.11. Bumping the floor to
  `>=3.10` corrects the metadata and satisfies pytest 9 at the same time.

## Scope

`wjs-profile-project` only. Sibling repos (`wjs-submission-project`,
`wjs-themes`, etc.) are out of scope for this change — they pin their own test
extras independently and can be ported separately once this repo's approach is
proven.

## Dependency targets

In `setup.cfg`:

```
[options]
python_requires = >=3.10          # was >=3.9

[options.extras_require]
test =
    pytest                         # latest 9.x, unpinned (was == 8.3.3, temporary workaround for #204)
    pytest-django ~= 4.12.0        # requires Django>=5.2, we're on Django~=4.2
    pytest-factoryboy               # bumped to latest
    pytest-mock                     # bumped to latest
    pytest-freezer                  # replaces pytest-freezegun (unmaintained); same `freezer` fixture
    pytest-xdist                    # bumped to latest
    pytest-reverse                  # bumped to latest
    wjs_mgmt_cmds ~= 0.5.0
```

Two pins end up with fundamentally different characters, and both must be
documented as such:

- `pytest` — no version pin once #204 is root-caused and fixed. If a fix
  turns out to be impossible for the latest release, pin only as far back as
  needed and document why in the comment and in #204, rather than freezing at
  a fixed version out of caution.
- `pytest-django ~= 4.12.0` — a durable constraint tied to this project's
  Django version, not a workaround for a bug. It stays until Django is
  upgraded past 5.1. Documented inline only (no separate tracking issue) per
  explicit decision — it's expected to surface naturally whenever a Django
  upgrade is eventually planned.

## Implementation phases

### Phase 0 — Diagnostic spike (pytest only, not committed)

`pytest-django`'s incompatibility is already fully explained (see Context) —
no bisection needed there. Only pytest itself needs investigating, to finally
answer #204 instead of re-pinning past it again.

From `janeway/src`, with this repo installed (`pip install -e .[test]`):

1. Step through pytest 8.3.3 → 8.4.0 → 9.0.0 → 9.1.1 (or narrower, as needed
   once the first failing minor version is found), running:
   ```bash
   pytest --create-db -n7 ../../wjs-profile-project
   ```
   at each version, to find the first version that breaks and the first
   failing test/error.
2. Cross-reference the failure against that version's pytest changelog.
   Candidates already considered and ruled out by a repo-wide grep (no
   top-level `async def test_*`, no top-level `test_*` function returning a
   non-`None` value, no bare `yield` inside a top-level test function) —
   confirm against the actual failure rather than assuming one of these is it,
   since Janeway's own test modules run in the same session and weren't
   grepped.
3. Record the root cause (version + mechanism + fix) — this becomes the
   answer to #204 and the explanation in the eventual MR description.

This phase produces knowledge, not commits — no throwaway version-bisection
commits should land in the branch history.

### Phase 1 — Dependency bump

Apply the `setup.cfg` changes above in one commit.

### Phase 2 — Fix forward with the root cause already known

Apply the specific fix Phase 0 identified (fixture/marker/test-pattern change,
`pytest.ini` `filterwarnings`/`markers` updates if needed). Then run the full
suite matching CI's actual invocation:

```bash
pytest -c ${CI_PROJECT_DIR}/pytest.ini ${CI_PROJECT_DIR}/ -v -n 6 --reverse -m "not fix_labels" --create-db
```

Any unrelated failures surfaced along the way (e.g. order-dependent tests
unmasked by a newer `pytest-reverse`) get triaged case by case, but are
expected to be rare since the two known incompatibilities are already
resolved going in — this phase should not turn into open-ended trial and
error.

## Validation

- Full local run from `janeway/src`, per `.claude/rules/tests.md` (parallel +
  reversed, matching CI's `-m "not fix_labels"` invocation).
- `pre-commit run --all-files` (the change touches `setup.cfg`, which is
  linted too).
- Fall back to the `docker-compose-test-*.yml` CI-replication path only if
  local and CI results disagree.

## Rollback / risk

Single feature branch, single MR targeting `wjs-develop`. If Phase 0 turns up
a fix that's invasive — a real behavior change needed in shared fixtures, not
just a version bump — split that into its own preparatory MR rather than
block the whole port on it. Default expectation is one MR closes #204.

## Definition of done

- `pytest` at latest 9.x, unpinned (or pinned no further back than strictly
  necessary, with the reason documented).
- `pytest-django ~= 4.12.0`, documented as a Django-version constraint, not a
  workaround.
- `pytest-freezer` replacing `pytest-freezegun`; `pytest-mock`,
  `pytest-xdist`, `pytest-reverse`, `pytest-factoryboy` at latest.
- `python_requires >= 3.10`.
- Issue #204 closed with the real pytest root cause on record.
- Full suite green locally and in CI.
