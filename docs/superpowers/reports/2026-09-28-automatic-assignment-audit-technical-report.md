# Automatic editor assignment audit and replay: technical report


Sep 26, 2026 · @Iacopo

Issue wjs/specs#3136 is implemented on `feature/issue-3136-automatic-assignment-audit`: every automatic editor assignment now leaves an audit row, and a replay command re-runs historical articles on a copy database. The full suite passes, and the work is in draft MR !1534. Replays on JCAP showed that duplicate keyword records, since fixed locally, blocked subject matching. On corrected data, workload first settled most assignments (22 of 40). After the switch to a weighted keyword score (!1512), the same 40 articles matched the historical editor in 14 cases instead of 6, with subject match deciding.

## Branches and state

Two branches hold the work, both pushed and each with its own MR. All commits are unsigned (`--no-gpg-sign`), because the agent session had no access to the GPG key.

| Branch | Base / target | MR | Tests |
| --- | --- | --- | --- |
| `feature/issue-3136-automatic-assignment-audit` | `docs/automatic-assignment-audit-design` (stacked on !1513 → !1512) | [!1534](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1534), draft | Full suite: 1171 passed, 63 skipped, 26 xfailed, 10 xpassed |
| `bugfix/jcom-special-issue-managing-editors` | `wjs-develop` | [!1535](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1535), reviewer gamboz | `test_automatic_article_assignment.py`: 76 passed, 1 skipped; the regression test fails without the fix |

- **!1534 is still a draft**, but its base chain has been caught up. `feature/automatic-assignment-keyword-matching` was rewritten (`annotate_keyword_ranking`, `annotate_workload_availability`, `rank_editor_parameters`, `brake_on = -1`), merged into !1513's design branch, and ported into !1534 on 2026-09-29. The audit capture and the replay now run on the new pipeline.
- **Tests always run against one checkout.** Janeway's `plugins/wjs_*` symlinks and the editable `wjs` install select which checkout is tested. Check them before running tests or the replay.
- **Docs:**
  - spec: `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md`, rev 1.1;
  - plan: `docs/superpowers/plans/2026-09-26-automatic-assignment-audit.md`;
  - handover: `docs/superpowers/plans/2026-09-26-automatic-assignment-audit-HANDOVER.md`.

## Audit trail

Every automatic assignment writes one append-only `AutomaticAssignmentAudit` row, in the same transaction as `BaseAssignToEditor.run()`. A row is written only when an editor was actually assigned.

- **Selector return type:** `get_selected_editor_by_workload(parameters, journal)` now returns the winning annotated `StaffWorkloadParameters` row, or `None`, instead of the `Account`. Callers use `parameter.user`. All direct-call tests go through a `_selected_editor` helper.
- **Model** (`wjs_review/models.py`, migration `0018_automaticassignmentaudit`, which depends on `submission.0089`):
  - fields: `article` (CASCADE), `editor` (SET\_NULL), `created_at`, `algorithm` (`default` / `default_special_issue` / `jcom`), `matched_by_keyword`, `matching_keywords_count`, `max_matching_keyword_weight`, `workload_available`, `workload_cap`;
  - deliberately no FK to `WjsEditorAssignment`, which is hard-deleted on reassignment;
  - queryset: `for_journal(journal)`, `latest_for_article(article)`.
- **Capture** (`events/assignment.py`, via `record_automatic_assignment_audit(...)`):
  - keyword path: `algorithm=default`, `matched_by_keyword=True`, and the monthly numbers `automatic_available_workload` / `effective_workload_cap`;
  - fallback and special-issue paths: `default` or `default_special_issue`, keyword fields empty, and the open-articles numbers `available_workload` / `workload`;
  - JCOM: always `jcom`, open-articles numbers.
- **Page:** `AutomaticAssignmentAuditList` at URL name `wjs-automatic-assignment-audit`, for EO and directors only (`has_eo_or_director_role`), scoped to `request.journal`. It is filterable by editor, algorithm, matched-by-keyword and date range, and linked from the EO and director navigation.
- **Monthly window:** `annotate_automatic_assignment_workload` now bounds `assigned__lte=now`. This changes nothing live, and keeps the frozen-time replay honest.

## Historical replay

`manage.py replay_automatic_assignment` resets historical articles and re-runs the real `dispatch_assignment` on them, in submission order, writing a CSV of original versus new editor. It is destructive and meant only for a copy database. Logic lives in `wjs_review/logic__replay.py`.

```
python manage.py replay_automatic_assignment --journal JCAP \
  (--article-ids 4833 4999 … | --date-from 2025-01-01 --date-to 2025-12-31) \
  [--output-file report.csv] --confirm-copy-instance
```

- **Two phases:** `ReplayAutomaticAssignment.run()` first snapshots and resets the whole batch, then replays each article under `freezegun.freeze_time(article.date_submitted)`. Resetting everything first stops later articles' historical assignments from inflating workload. The whole run is one transaction.
- **Original editor** = the first editor ever assigned (the earliest of `PastEditorAssignment.date_assigned` and `WjsEditorAssignment.assigned`). This is an owner decision, recorded in spec rev 1.1.
- **Reset** (`ResetArticleForReplay`) deletes, in PROTECT-safe order: `EditorRevisionRequest`, then `EditorDecision`, `ReviewAssignment`, `ReviewRound`, `EditorAssignment`. It then sets `Article.stage` to `STAGE_UNASSIGNED` and the workflow state to `SUBMITTED`. After the replay the state becomes `EDITOR_SELECTED`, or `EDITOR_TO_BE_SELECTED` if nobody was found.
- **Skipped, left untouched and reported with a reason:**
  - articles that reached typesetting (they have a `TypesettingAssignment`);
  - articles that never had an editor;
  - articles already replayed on this instance: an assignment dated exactly at `date_submitted` is the replay's fingerprint, so re-import the database to replay them again.
- **Safety:**
  - `--confirm-copy-instance` is required;
  - the email backend is forced to locmem during the run;
  - `--article-ids` with an empty list selects nothing.
- **CSV columns** (current format, since `99913611`): `article_id`, `title`, `journal`, `date_submitted`, `original_editor`, `new_editor`, `match`, `matched_by_keyword`, `matching_keywords_count`, `matching_keywords`, `max_matching_keyword_weight`, `keyword_score`, `monthly_workload`, `workload_30d`, `workload_cap`. Skipped articles aren't in the CSV; they are printed as `Skipped <id>: <reason>`. A second file, `<output>_candidates.csv` (or `--candidates-file`), lists every section editor sharing a keyword with the article: `article_id`, `editor`, `rank`, `selected`, `reason`, `keyword_score`, `matching_keywords_count`, `matching_keywords`, `monthly_workload`, `workload_30d`, `workload_cap`. The reasons are `selected`, `author of the article`, `not available (disabled, on leave or no workload)`, `monthly limit reached`, `lower keyword score`, `same score, fewer shared keywords`, `same score and shared keywords, less 30-day availability` and `full tie, lost on id`. They are computed inside the frozen-time block right before the assignment, with the same pipeline (`rank_default_candidates`). The report row links the audit row created by that replay (`pk` greater than the pre-replay maximum), not a later live one.
- **Known limits** (also in `wjs_review/CLAUDE.md` and the command's `help`):
  - replayed articles stay open for the rest of the batch, so the open-articles metric only grows;
  - today's workload settings, roles and editor keywords are used for past articles;
  - articles outside the batch are seen in today's state;
  - the copy must have outgoing email and the Q-cluster/cron disabled, because the replay creates reminders dated in the past.

## Bugs found along the way

**Zero-keyword articles took the keyword path.** This is fixed on the feature branch (`filter_parameters_by_matching_keywords`).

- **Cause:** with an article that has no keywords, `get_editors_with_keywords` filters `staffkeyword__keyword__id__in=[]`. Django raises `EmptyResultSet` while compiling the filtered `Count` and folds it to the constant `0`, so the HAVING clause `NOT (num_shared_kwds = 0)` collapses to "match everything".
- **Effect:** every candidate survived with a count of 0 and passed the `isnull=False` check. The article was then ranked on the monthly workload metric instead of taking the open-articles fallback.
- **Fix:** short-circuit to `.none()` on the already annotated queryset when the article has no keywords, so the fallback runs. It is pinned by `test_filter_parameters_by_matching_keywords_empty_when_article_has_no_keywords`.
- The manual "Select new editor" path shows `num_shared_kwds`, where a constant 0 is correct. Manual selection is out of scope and was not changed.

**The JCOM special-issue check was always true.** This is fixed on the bugfix branch, commit `b820f11f` (MR !1535), and its tests pass.

- **Cause:** `jcom_assign_editors_to_articles` checked `article.primary_issue.managing_editors`, a related manager, which is always truthy.
- **Effect:** every JCOM or JCOMAL article with a `primary_issue` used the special-issue pool. When that issue had no managing editors, the pool was empty, no director was assigned, and the article waited in `EditorToBeSelected`.
- **Fix:** `.managing_editors.exists()`, the same check the default algorithm already uses. The regression test is `test_jcom_assignment_primary_issue_without_managing_editors_uses_directors`.

## Replay run and JCAP data analysis

The keyword gap comes from duplicate `Keyword` records, not an obsolete vocabulary. A first report said otherwise; this section is the corrected analysis.

**First replay (2026-09-26).** The replay ran on the local `janeway` database after migration 0018. That database has since been replaced by a newer JCAP snapshot, covering 3,374 submitted articles from 2025-01-02 to 2026-09-25. The 10 articles were drawn at random from 981 eligible ones: 4833, 4999, 5017, 5718, 5951, 5956, 6117, 6325, 6412 and 6522.

- 1 of 10 agreed with the first historical editor.
- 5 of 10 had no keyword match.
- 8 of 10 chosen editors had a workload cap of 9999.
- It ran before the keyword correction below, so its subject-matching result is not meaningful.

**Keyword overlap (2026-09-28, current snapshot).** Checked two ways: set arithmetic over `StaffKeyword`/`KeywordArticle`, and `filter_parameters_by_matching_keywords` on a 200-article random sample, with 0 disagreements between them.

| Measure | Before remap | After remap |
| --- | --- | --- |
| JCAP articles with no matching assignable editor | 1,870 / 3,374 (55.4%) | 1 (the only article with no keywords) |
| Articles matching 5+ assignable editors | 973 | 3,328 |
| Distinct editor keywords used by any JCAP article | 26 of 153 | 141 of 153 |
| Assignable editors (70) with no keyword from the article vocabulary | 23 | 0 |

- **Cause:**
  - Editors' `StaffKeyword` rows point at an old, ungrouped copy of each keyword, for example `dark energy theory` (id 364, `group=None`).
  - Articles use the grouped copy of the same term, for example `Dark energy theory` (id 514, group 35).
  - Of the 127 editor keywords that no article used, 115 match an article keyword once text is normalised: 113 differ only in case, and 2 have a double space (`redshift  surveys`, `primordial gravitational waves  (theory)`).
  - Across the whole `Keyword` table, 116 normalised words have more than one row.
  - Matching compares keyword ids, so the two copies never meet.
- **Local correction only** (the real fix will be applied externally):
  - on `janeway`, every JCAP `StaffKeyword` pointing at an ungrouped copy was repointed to the grouped twin with the same normalised word;
  - 889 rows were moved, and 5 were merged, keeping the higher weight, where the editor already had the twin;
  - 116 distinct old keywords were affected, and no target was ambiguous;
  - the audit CSV is `jcap-staffkeyword-case-remap.csv` in the session scratchpad.
- **Still unmatched:** 12 editor terms have no counterpart among article keywords. They need a manual mapping: black holes, classical tests of cosmology, cosmic rays, cosmological constant experiments, dark matter, gravity waves / theory, lensing, neutrino and gamma astronomy, structure of galaxies, superclusters and voids, supernova type Ia, surveys galaxies.
- **Group coverage after the remap:** every group with JCAP keywords is fully covered by editors, except Neutrinos (9 of 11). Group names such as "Gravity" and "Stars" also exist as plain editor keywords, but matching ignores groups.
- **Other local database changes:**
  - migration 0018 applied;
  - the 10 replayed articles reset, as on the earlier snapshot (the newer snapshot may already have replaced them);
  - section editors with a workload above 0 set to a random limit of 3–5: 70 JCAP, 3 JCOM, 2 JCOMAL, on the earlier snapshot.

## Second replay: 40 articles, corrected data (2026-09-28)

4 of 40 matched the first historical editor. Every article took the keyword path, and 35 of the 36 mismatches are explained by the ranking order, not by missing candidates.

**Setup:**

- The run used the current snapshot, after the `StaffKeyword` remap and migration 0018.
- All 70 JCAP section editors with a workload above 0 were reset to a random 3–5 (24 at 3, 22 at 4, 24 at 5). This overwrote the snapshot's values: 33 editors at 1–7, 2 at 999 and 36 at 9999.
- The 40 articles were drawn at random from 1,888 eligible ones, meaning submitted between 2025-01-01 and 2026-12-31, historically assigned, never typeset and not yet replayed.

```
replay_automatic_assignment --journal JCAP --article-ids 4391 4542 4563 4810 4815 4997 5325 5337 5470 5645 5697 5711 5753 5823 5976 5991 6008 6026 6050 6164 6450 6502 6545 6581 6678 6743 6792 6827 6856 6894 6944 6963 7019 7086 7265 7267 7315 7359 7389 7392 --confirm-copy-instance
```

| Measure | Value |
| --- | --- |
| Processed / skipped / no editor found | 40 / 0 / 0 |
| Match with the first historical editor | 4 |
| Keyword path / fallback | 40 / 0 |
| Articles by shared keywords (1 / 2 / 3 / 4) | 18 / 15 / 6 / 1 |
| Winning max keyword weight (100 / 999 / 90) | 34 / 4 / 2 |
| Distinct new editors / max papers per editor | 23 / 4 |
| Chosen editor's slots left at decision time (5 / 4 / 3 / 2 / 1) | 14 / 9 / 6 / 5 / 6 |

**Mismatches (36)**, re-checking the historical editor's own `StaffKeyword` overlap with each article:

- 31: the historical editor matched, but ranked lower on `(max_matching_keyword_weight, matching_keywords_count)`;
- 4: matched equally well, and lost on `automatic_available_workload` or the id tie-break;
- 1: shares no keyword with the article.

**Deciding factor per assignment.** For each article, the keyword ranking `(max_matching_keyword_weight, matching_keywords_count)` of every eligible candidate was rebuilt exactly, since keywords don't change during a replay, and compared with the winner's. Candidates were JCAP section editors that are enabled, have a workload above 0 and aren't authors: between 4 and 40 per article, median 21.

| Deciding factor | Assignments |
| --- | --- |
| Tie on weight and count; decided by `automatic_available_workload` or the id tie-break | 15 |
| Unique top `max_matching_keyword_weight` | 10 |
| Tie on weight; decided by `matching_keywords_count` | 8 |
| A higher-ranked candidate was at its monthly cap (excluded by `automatic_available_workload > 0`) | 7 |

Workload decided 22 of the 40 assignments, the keyword ranking 18. The 15 ties can't be split between workload and the id tie-break, because the other candidates' remaining slots weren't recorded at decision time. Phase 2 of the spec (recording every candidate) would make this exact. *(Since `99913611` the replay writes a candidates CSV that records this for replays; see the fourth replay.)*

**Findings for the ranking code:**

- **Weight comes before breadth.** The order is `-max_matching_keyword_weight, -matching_keywords_count, -automatic_available_workload, id`. Editors' weights spread from 40 to 100 (weight 100: 173 links, 90: 82, 80: 116, 70: 135, 50: 117, 40: 97), so a single weight-100 keyword beats three shared keywords at weight 80.
- **Weight 999 dominates.** Martin S. Sloth and Licia Verde won 4 articles through keywords weighted 999, one of them over a 4-keyword match. It is probably a data-entry value; consider capping or validating weights.
- **This is being rewritten anyway.** Ranking is being reworked on the keyword-matching branch (`annotate_keyword_ranking` / `rank_editor_parameters`, !1512), so any policy change belongs there.

The per-article CSV is `replay-jcap-40.csv` in the session scratchpad.

## Third replay: same 40 articles, ported ranking (2026-09-29)

6 of 40 matched the first historical editor, up from 4. Workload still decided 22 of 40. The run used the branch at `1dd5e6b7`, where ranking is `F("max_matching_keyword_weight").desc(nulls_last=True), -matching_keywords_count, -available_workload_30d, id` and editors are excluded on `available_workload_month > 0`.

**Setup:**

- Fresh import of the same snapshot. The remap counts were identical (889 moved, 5 merged).
- Migrations 0018 and 0019 applied.
- JCAP section editors with a workload above 0 set to random 3–5 **with `random.seed(3136)`**: 28 at 3, 21 at 4, 21 at 5. The per-editor values are saved in `jcap-limits-3-5-seed3136.csv`.
- Same 40 article ids as the second replay.

| Measure | Second replay (old ranking) | Third replay (ported ranking) |
| --- | --- | --- |
| Match with the first historical editor | 4 | 6 |
| Same new editor as the second replay | — | 27 / 40 |
| Keyword path / no-match | 40 / 0 | 40 / 0 |
| Distinct new editors / max per editor | 23 / 4 | 21 / 5 |
| Winning max weight (100 / 999 / 90) | 34 / 4 / 2 | 32 / 5 / 3 |
| Deciding factor: workload tie-break / cap exclusion / weight / count | 15 / 7 / 10 / 8 | 15 / 7 / 11 / 7 |
| Mismatch reason: ranked lower / tied / no shared keyword | 31 / 4 / 1 | 29 / 3 / 2 |

**Findings:**

- **Two new matches** (4810, 5470), and **13 winners changed** from the second replay. The causes are mixed: the 30-day tie-break, and limits that were drawn at random again. The second replay's per-editor limits weren't saved, so the two effects can't be separated; this run's limits can be reproduced.
- **Over-cap editors still win.** The exclusion only checks the calendar month, so `available_workload_30d` goes negative across a month boundary. Two winners had 30-day availability of −2 and −6, both Licia Verde through a weight-999 keyword. That's because `available_workload_30d` only breaks ties. If a trailing-30-day cap is wanted, it needs an exclusion filter, which is a policy question for !1512.
- **Weight 999 dominates** in 5 of 40 (Martin S. Sloth 3, Licia Verde 2).

The CSV is `docs/superpowers/reports/2026-09-29-replay-jcap-40-new-ranking.csv` (it has the `workload_available_30d` column).

## Fourth replay: same 40 articles, weighted keyword score (2026-09-29)

14 of 40 matched the first historical editor, up from 6. The run used `feature/issue-3136-automatic-assignment-audit` at `3f192b74`, which includes !1512 at `a5942a48`. Ranking is now `F("keyword_score").desc(nulls_last=True), F("matching_keywords_count").desc(), F("available_workload_30d").desc(nulls_last=True), id`, still after the `available_workload_month > 0` exclusion.

`keyword_score = Σ_shared w_A·min(w_E,100) / (Σ_all w_A·100)`: `w_A` is `KeywordArticle.weight` (NULL = 100) and `w_E` is `StaffKeyword.weight`. It is a correlated subquery over `StaffKeyword` in `users.py::_keyword_score_expression`; spec `docs/superpowers/specs/2026-09-29-weighted-keyword-score-design.md` rev 1.1. The audit row stores it (migration 0020), and the audit page and the replay CSV show it.

**Setup:**

- Fresh import of the same snapshot. The remap counts were identical (889 moved, 5 merged).
- Migrations 0018–0020 applied.
- Limits restored per `parameters_id` from `jcap-limits-3-5-seed3136.csv`: 70 applied, 0 mismatches with the snapshot values. The limits are identical to the third replay.
- Duplicate `(parameters, keyword)` `StaffKeyword` pairs in JCAP: 0.
- Same 40 article ids.

| Measure | Third replay (weight, count) | Fourth replay (keyword score) |
| --- | --- | --- |
| Match with the first historical editor | 6 | 14 |
| Matches kept / gained / lost | — | 5 / 9 / 1 (4810) |
| Same new editor as the third replay | — | 16 / 40 |
| Distinct new editors / max per editor | 21 / 5 | 28 / 4 |
| Winning max weight 999 | 5 | 1 |
| Winner's shared keywords (1 / 2 / 3 / 4) | 19 / 16 / 4 / 1 | 7 / 18 / 14 / 1 |
| Winner's keyword score (min / median / max) | — | 0.275 / 0.647 / 1.000 |
| Winners with negative `available_workload_30d` | 2 | 3 |
| Historical editor's outcome (26 mismatches): lower score / monthly limit reached / less 30-day availability / not a matching candidate | — | 19 / 4 / 1 / 2 |

**Findings:**

- **Static rank of the historical editor on `keyword_score`** (38 articles where they have a score): 1st in 13, top 3 in 29, median 2. This matches the offline estimate made before the change, so the in-DB expression agrees with the prototype.
- **Only exact ties reach workload now.** With limits identical, every change against the third replay comes from the ranking.
- **Over-cap winners remain possible** (3), for the same reason as before: the 30-day figure only breaks ties.

The CSV is `docs/superpowers/reports/2026-09-29-replay-jcap-40-weighted-score.csv`, with the `keyword_score` column.

**Rerun in the new report format** (`99913611`, fresh import, same limits): the same 40 winners, so the refactor behind the candidates report changed no assignment. Files: `2026-09-29-replay-jcap-40-weighted-score-v2.csv` and `2026-09-29-replay-jcap-40-weighted-score-v2_candidates.csv`.

- **Candidates CSV:** 841 rows, one per section editor sharing at least one keyword with the article (median 20 per article, min 4, max 40). The selected editor is always rank 1.
- **Reasons across all rows:** `selected` 40, `lower keyword score` 690, `monthly limit reached` 100, `same score and shared keywords, less 30-day availability` 8, `full tie, lost on id` 2, `same score, fewer shared keywords` 1.
- **The historical editor's outcome** in the 26 mismatches is in the table above. The earlier offline breakdown (22 lower score, 2 tied) came from static ranks, which couldn't see that 4 of those editors were already at their monthly limit when the assignment ran.
- **Caveat:** editors switched off with `brake_on = -1` are reported as `monthly limit reached`, not as `not available`. None appear in this run.

## Open items and follow-ups

- [ ] Done 2026-09-29: the port onto the rewritten keyword ranking (merge `c2949a22`, the `workload_available_30d` field in migration 0019, the time bound in both windows, spec rev 1.2). Remaining: take !1534 out of draft and pick a reviewer, and settle the `wjs_review` 0018 migration-number clash with the issue-2903 branch.
- [ ] JCOM bugfix !1535: tests pass. It is waiting for review by gamboz and has no linked issue, by the owner's decision.
- [x] Done 2026-09-29: ranking by weighted keyword score on !1512 (`3b86043e`..`a5942a48`), audit field, page and CSV on !1534 (migration 0020). Replay 14 / 40.
- [ ] Whether weights such as 999 should be allowed at entry (they're capped at 100 in the score).
- [ ] `StaffKeyword` has no unique constraint on `(parameters, keyword)`. A duplicate would be counted twice in `keyword_score`, pushing it above 1. There are none in JCAP today; the importers and admin can create them. Follow-up: a `UniqueConstraint` plus a data migration keeping the max weight, and a duplicate check on production before merge.
- [ ] The "Select new editor" page (`SupervisorAssignEditor._editors_with_keywords`) chains filters onto an already-evaluated queryset, which probably drops `matching_keywords` and repeats its queries. This is already on `wjs-develop`; open its own issue.
- [ ] Data work, applied externally: merge the duplicate Keyword records (116 case or space variants; a local remap proved it), map the 12 editor terms with no counterpart by hand, and set real workload limits. Then rerun the replay on the corrected data.
- [ ] Decide whether a match on a keyword group should count. That would be a new feature in `filter_parameters_by_matching_keywords` / `get_editors_with_keywords`.
- [ ] Deferred minor findings from review, none blocking:
  - orphaned `Reminder` rows after a reset;
  - `logic__replay` filters through `.objects` instead of queryset methods (the plan specified this);
  - untested default `--output-file` path, and only the "Processed" summary line is asserted;
  - `test_audit_page_filters_by_editor` doesn't assert the status code;
  - the fake request stays registered in `GlobalRequestMiddleware` after a run.
- [ ] Phase 2 of the spec, live assignments only: a candidate-by-candidate comparison stored with each audit row. Replays already get it through the candidates CSV (`99913611`).
