## 2026-11-06 — Generic "busy" reason in the Editor's decline modal
**What:** The Editor decline reason `JCOM_BUSY` ("Already too busy with JCOM editorial work") became `BUSY_EDITORIAL` ("Too busy with other editorial tasks") for all journals. Migration `0024` moves stored rows (reversible). The en/es/pt catalogs are updated.
**Why:** JCAP went live, and the JCOM wording was wrong on every journal other than JCOM. The team chose one generic reason over per-journal labels.
**Decisions:**
- `0024` is hand-written with only the `decline_reason` AlterField plus a RunPython step. `makemigrations` also wants 4 proxy-model CreateModel operations, but those were already pending on `wjs-develop` and are unrelated.
- The `.po` catalogs were edited in place rather than regenerated with `makemessages`, because their source references are stale and a full run would churn them.
- Recompiling the es/pt `.mo` also picks up pre-existing `.po` drift. Deploy runs `compilemessages`, so production already gets the same result.
- A form posting the stale `JCOM_BUSY` value (a modal open across the deploy) is rejected as an invalid choice rather than stored.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Review | superpowers:requesting-code-review (opus, final whole-branch) | ≈70k | ≈1m |

**Considered & dropped:**
- Per-journal labels (the original request): rejected in favour of one generic reason, per the issue.
- Accepting `makemigrations` output as-is: it would have bundled the unrelated proxy models.
**Follow-ups:**
- Verify on the test environment that no `JCOM_BUSY` rows remain, run the issue's functional steps on JCAP and JCOM, and comment the results on wjs/specs#3306.
- Gotcha: the local `janeway` venv had `wjs-profile-project` installed non-editable (a 28 Oct snapshot), so `wjs.*` and `wjs/locale` were stale in tests. It is now `pip install --no-deps -e .`; the full `-e .[test]` failed because `wjs-themes>=2.1.10` could not be resolved.
- Gotcha: `pytest_freezegun` in the venv imports `distutils`, which Python 3.13 removed, so tests need `-p no:freezegun`.
- The 4 pending proxy-model migrations deserve their own issue.
**Eval:** 86% — artifacts/evaluations/2026-11-06-editor-decline-reason-generic-busy.md
**Refs:** wjs/specs#3306; spec `artifacts/specs/2026-11-06-editor-decline-reason-generic-busy-design.md`; plan `artifacts/plans/2026-11-06-editor-decline-reason-generic-busy.md`.
