# Automatic editor assignment audit & replay — session handover (updated 2026-09-29)

Read this first to pick up the work. It summarises what was decided, built and found in the sessions of 2026-09-26 and
2026-09-28, and what is still open. Code facts are in the repo; this file carries the context the repo does not.

## Documents

- Spec (binding, rev 1.1): `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md`
- Plan (7 tasks, all executed): `docs/superpowers/plans/2026-09-26-automatic-assignment-audit.md`
- Technical report for developers: Claude Doc https://claude.ai/code/artifact/deda4611-297c-4d83-b673-a4a9ef3fa83a;
  markdown snapshot `docs/superpowers/reports/2026-09-28-automatic-assignment-audit-technical-report.md`
- Supervisor report, "why historical data can't validate the algorithm": Claude Doc
  https://claude.ai/code/artifact/d6f21a39-fa0b-4f73-8a46-445fb0682a5b; markdown snapshot
  `docs/superpowers/reports/2026-09-28-jcap-assignment-validation-supervisor-report.md`
- The markdown files are **exports** of the Claude Docs. The Docs are the living versions; re-export them after editing
  (the user asked for the reports to be kept as markdown too).
- Weighted keyword score: spec `docs/superpowers/specs/2026-09-29-weighted-keyword-score-design.md` (rev 1.1), plan
  `docs/superpowers/plans/2026-09-29-weighted-keyword-score.md` (7 tasks, all executed), wiki replacement text
  `docs/superpowers/specs/2026-09-29-weighted-keyword-score-wiki-text.md` (the owner pastes it into the wiki)
- Feature docs for developers: the "Automatic assignment audit & replay" section of `wjs/plugins/wjs_review/CLAUDE.md`

## Branches and MRs (both pushed)

| Branch | Target | MR | State |
| --- | --- | --- | --- |
| `feature/issue-3136-automatic-assignment-audit` (main checkout) | `docs/automatic-assignment-audit-design` (!1513 → !1512 → `wjs-develop`) | [!1534](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1534), **draft**, assigned to the user, no reviewer | Implementation done and reviewed. Port done: merged the rewritten keyword branch (via the design branch), capture moved onto the new pipeline, 30-day field added (migration 0019). Full suite after the port: 1179 passed, 63 skipped, 26 xfailed, 10 xpassed (10 failures from a stale reused test DB, all pass on a fresh DB) |
| `bugfix/jcom-special-issue-managing-editors` (worktree `.worktrees/jcom-special-issue-check`) | `wjs-develop` | [!1535](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1535), assigned to the user, reviewer gamboz | `.managing_editors.exists()` fix + regression test. 76 passed, 1 skipped; the test fails without the fix. No linked issue (owner's decision) |

- The owner chose a separate MR stacked on !1513 (which stays design-only), rather than pushing to !1513.

## Owner decisions (don't re-litigate)

- The replay's "original editor" is the **first** editor ever assigned (the earliest across `PastEditorAssignment.date_assigned`
  and `WjsEditorAssignment.assigned`). This is spec rev 1.1.
- The replay reset puts the workflow back to **`SUBMITTED`**. The user briefly asked for `EDITOR_TO_BE_SELECTED`, then reverted to `SUBMITTED`.
- The zero-keyword fix stays in the feature branch.
- **Ranking (2026-09-29):** editors are ranked by the weighted keyword score (normalised coverage, editor weights capped at 100,
  NULL article weight = 100), then shared-keyword count, then 30-day availability, then id. It is implemented on !1512.
  The count tie-break was added at the owner's request, for journals whose editor weights are all 0 (JQuant today).
- The JCOM `managing_editors` truthiness is a **bug**, confirmed by the owner after first calling it a feature. It is fixed on its own branch from `wjs-develop`.
- **All requests are about JCAP until further notice.** Earlier actions that touched other journals are accepted as they are.
- The duplicate-keyword fix will be applied **externally**, not in this repo. The local remap only served the analysis.

## Working conventions the user set

- **Commits:** unsigned (`git commit --no-gpg-sign`), because the GPG key isn't available to the agent. Trailers:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu`.
- **MRs:** assign to the user. **Always ask for the reviewer and the target branch**, and show the description (repo
  template `.gitlab/merge_request_templates/base.md`) before opening. Issue refs go in `wjs-profile-project#<n>` form.
- **Worktrees:** no worktree for the audit feature (it is worked in place). The user explicitly asked for one for the JCOM bugfix.
- **Pushing:** only when the user asks. SSH goes through gpg-agent. On 2026-09-26 the agent's cache was raised to 24h
  (`max-cache-ttl-ssh 86400`, backup `~/.gnupg/gpg-agent.conf.bak-2026-09-26`). When the key is locked (`agent refused
  operation`), the user must unlock it from their terminal (`! ssh -T git@gitlab.sissamedialab.it`); pinentry is curses-only.

## Environment gotchas

- Tests run from `janeway/src`: `pytest ../../wjs-profile-project/<path> -q -n4`. The full suite takes about 22 minutes with `-n7`.
- Janeway's `plugins/wjs_*` are **symlinks**, and `wjs` is an **editable install**
  (finder: `~/.pyenv/versions/janeway/lib/python3.13/site-packages/__editable___wjs_jcom_profile_2_1_3_dev1_finder.py`).
  They decide which checkout pytest and `manage.py` run. **Check `readlink janeway/src/plugins/wjs_review` before running
  anything**: other work repoints them. On 2026-09-28 they pointed at `.worktrees/issue-2903-appeal-editor-permissions`; they
  were relinked to the main checkout at the user's request, and `restore-issue-2903-links.sh` in the session scratchpad puts
  them back. The finder was pointing at the main checkout throughout.
- `makemigrations` can generate a spurious `submission/…` migration inside the Janeway checkout (translation fields).
  Delete it and never commit it.
- `manage.py` settings: `janeway/src/core/settings/__init__.py`, which the user edits. The DB is local PostgreSQL **`janeway`**, a JCAP
  production snapshot the user can restore from a dump, so destructive operations are OK there. Email: console backend. Q-cluster: sync, not running.
- **The snapshot can be swapped under you.** Between 2026-09-26 and 2026-09-28 the user loaded a newer dump (3,374 submitted JCAP
  articles, 2025-01-02 → 2026-09-25), which undid migration 0018, the editor limits and the first replay. Re-check before relying on
  earlier state.
- In a Django shell, `qs.distinct().values_list(...)` gives *distinct values*, so counts come out wrong. Count with `pk__in=` instead.
- During the first session a subagent ran `pip install setuptools` in the Janeway venv, so that `pytest-freezegun` works on Python 3.13.

## State of the local `janeway` DB (as of 2026-09-29, fresh import of the same snapshot)

- **2026-09-29 state:** migrations 0018–0020 applied, remap re-applied (889 moved, 5 merged), seeded limits restored from
  `jcap-limits-3-5-seed3136.csv`, and the same 40 articles replayed on the weighted keyword score. The older notes below describe
  the 2026-09-28 state, which the re-import reset and this run recreated.

- Migration `wjs_review.0018_automaticassignmentaudit` applied.
- **Keyword remap (local only):** every JCAP `StaffKeyword` on an ungrouped lowercase `Keyword` copy was repointed to its grouped twin
  (889 moved, 5 merged keeping the higher weight). Log: `jcap-staffkeyword-case-remap.csv` in the session scratchpad.
- **Editor limits:** all 70 JCAP section editors with a workload above 0 were set to a random 3–5 (24 at 3, 22 at 4, 24 at 5).
  This overwrote the snapshot's values (33 at 1–7, 2 at 999, 36 at 9999); 1 editor at 0 was left untouched.
- **40 articles replayed** (and so fingerprinted as already replayed): 4391 4542 4563 4810 4815 4997 5325 5337 5470 5645 5697 5711
  5753 5823 5976 5991 6008 6026 6050 6164 6450 6502 6545 6581 6678 6743 6792 6827 6856 6894 6944 6963 7019 7086 7265 7267 7315 7359
  7389 7392. Output: `replay-jcap-40.csv` and `replay-run-40.log` in the session scratchpad (it may be gone).
- The earlier snapshot's first replay (10 articles) and its 3–5 limits for JCOM and JCOMAL are gone with that snapshot.

## Findings (JCAP)

- **The 40-article analysis below ran on the old ranking** (monthly tie-break, keyword-excluding), so a rerun is needed on the new one (see Next steps).
- **First replay (2026-09-26, older snapshot, 9999 limits, before the keyword fix):** 1 of 10 matched the first historical
  editor, and 5 of 10 had no keyword match. Not meaningful for subject matching.
- **The keyword gap is duplicate `Keyword` records, not an obsolete vocabulary.** (An earlier report said the vocabulary was
  obsolete; that was wrong and has been corrected everywhere.)
    - Editors' `StaffKeyword` rows point at old, ungrouped lowercase copies (e.g. `dark energy theory`, id 364), while articles
      use the grouped twins (`Dark energy theory`, id 514). Matching compares ids, so they never meet.
    - 113 pairs differ only in case and 2 in a double space; 116 normalised words are duplicated across the whole `Keyword` table.
    - Only 12 editor terms have no counterpart.
    - Before the remap, 1,870 of 3,374 (55.4%) had no matching editor. After it, only 1 does (the only article with no keywords).
    - Verified two ways (set arithmetic, and `filter_parameters_by_matching_keywords` on a 200-article sample): 0 disagreements.
- **Second replay (2026-09-28): 40 random articles submitted in 2025–2026**, on corrected keywords and 3–5 limits.
    - 4 of 40 matched the first historical editor. All 40 took the keyword path; none fell back.
    - The load was spread over 23 editors, none getting more than 4.
    - Winners mostly shared 1–2 keywords with the article (18 had 1, 15 had 2).
    - Why the 36 others differed: 31 had the historical editor matching but ranked lower on (weight, count); 4 matched equally
      well and lost on workload or tie-break; 1 had no shared keyword.
    - **Deciding factor.** This was found by rebuilding every candidate's static keyword ranking; there were 4–40 candidates per
      article, median 21:

      | Deciding factor | Assignments |
      | --- | --- |
      | Workload or tie-break among keyword-tied candidates | 15 |
      | Unique top keyword weight | 10 |
      | Keyword count (tie on weight) | 8 |
      | A better-ranked candidate was at its monthly cap | 7 |

      **Workload decided 22 of 40** and keywords 18. The 15 ties can't be split into workload vs. tie-break without Phase-2
      candidate recording.
    - **Ranking-policy questions for the supervisor:**
        - The current order is `-max_matching_keyword_weight, -matching_keywords_count, -automatic_available_workload, id`, so
          one weight-100 keyword beats three shared keywords at weight 80. Editor weights range 40–100.
        - Weight **999** (Martin S. Sloth, Licia Verde) wins almost everything it touches: 4 of the 40 articles. It is probably a
          data-entry value.
        - Any policy change belongs in the rewritten ranking on !1512.
- **Supervisor conclusion (in both reports):** matching past choices isn't a valid test. With corrected keywords, workload and
  the ranking order drive the outcome. Decide the ranking policy, set real limits, and validate going forward using the audit page.

- **Fourth replay (2026-09-29): same 40 articles, weighted keyword score.** Fresh import; same remap (889/5); migrations
  0018–0020; limits restored exactly from `docs/superpowers/reports/2026-09-29-jcap-limits-3-5-seed3136.csv` (70 applied, 0 mismatches).
    - **14 of 40** matched (third replay: 6). Kept 5, gained 9, lost 1 (4810). 28 editors used, max 4 each. One 999 win (was 5).
    - The historical editor's static rank on the score: 1st in 13, top 3 in 29, median 2. This matches the offline estimate.
    - Mismatches, from the candidates CSV (recorded at assignment time): 19 lower score, 4 at the monthly limit, 1 same score but
      less 30-day availability, 2 no shared keyword or no longer an editor. 3 winners are over their 30-day limit.
    - 0 duplicate `StaffKeyword` pairs in JCAP. CSV: `docs/superpowers/reports/2026-09-29-replay-jcap-40-weighted-score.csv`.
    - **Rerun in the new report format** (`99913611`, another fresh import): the same 40 winners. Main CSV
      `2026-09-29-replay-jcap-40-weighted-score-v2.csv` (no `algorithm`/`skipped_reason`; `monthly_workload`, `workload_30d`,
      `matching_keywords`) and `..._v2_candidates.csv` (841 rows: every section editor sharing a keyword, with rank and the
      reason they weren't picked). Skipped articles go to stdout as `Skipped <id>: <reason>`. `brake_on = -1` editors show as
      `monthly limit reached`.

## Next steps

1. Done: the weighted keyword ranking is on !1512 and reported on !1534. Still for the supervisor: whether 999-style weights should be
   allowed at entry, real workload limits, and whether the 30-day availability should also exclude editors.
2. After the external keyword merge and real limits: rerun the replay on fresh articles. Use `get_replayable_articles(JCAP, …)`
   with a historical editor, no `TypesettingAssignment` and no replay fingerprint.
3. !1535 awaits gamboz's review.
4. Possible follow-ups:
    - matching on keyword groups (a new feature);
    - a manual mapping for the 12 unmatched editor terms;
    - the deferred minor findings listed at the end of the technical report;
    - spec Phase 2 (recording every candidate, which would make the deciding-factor analysis exact);
    - a `UniqueConstraint(parameters, keyword)` on `StaffKeyword`: duplicates would double-count in the score;
    - the "Select new editor" queryset re-filtering bug (already on `wjs-develop`).
