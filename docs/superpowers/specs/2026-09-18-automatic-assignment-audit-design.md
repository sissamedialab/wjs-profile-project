# Automatic Editor Assignment — Audit Trail & Historical Replay Design

## Revisions
| Revision | Authors | Date | Changes summary |
| -------- | ------- | ---- | --------------- |
| 1.0 | Iacopo Spalletti (with Claude Sonnet 5) | 2026-09-18 | Initial draft |
| 1.1 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-26 | Replay compares against the first editor ever assigned, not the current/latest one |
| 1.2 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-28 | Capture points follow the rewritten keyword ranking (!1512): no separate fallback branch; matched_by_keyword = winner shares a keyword; new workload_available_30d field |
| 1.3 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-29 | Adds keyword_score (weighted keyword coverage, spec 2026-09-29-weighted-keyword-score-design) to the audit record, audit page and replay CSV |
| 1.4 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-29 | Replay CSV: drop algorithm/skipped_reason, rename workload columns, add matching_keywords; new candidates CSV explaining each matching editor's outcome |
| 1.5 | Iacopo Spalletti (with Claude Sonnet 5.5) | 2026-10-27 | Aligned with the implementation: candidates are also stored per audit (`AutomaticAssignmentCandidate`) and shown on a per-article audit page; replay code lives in the `replay/` subpackage; single migration `0021`; `brake_on` is read-only for directors |

## References
| Document | URL | Notes |
| -------- | --- | ----- |
| Issue #3136 | https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3136 | The ticket this design implements — "Test automatic editor assignment" |
| Editor Selection logic spec | https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic#implementation-details | Related spec (#1245); its "Priority 2 / Report" section describes the full candidate-comparison audit this design explicitly defers |
| MR !1512 | https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1512 | The branch that implemented keyword-based automatic assignment (originally `filter_parameters_by_matching_keywords`/`annotate_automatic_assignment_workload`, since rewritten as `annotate_keyword_ranking`, `annotate_workload_availability`, `rank_editor_parameters`; plus `default_assign_editors_to_articles`) this design instruments |

## Subject

Issue #3136 reports that a real automatic editor assignment on the JCAP test instance (article 5318, assigned to editor "Koch") looked wrong, and asks for a way to test the automatic assignment algorithm against real articles and real editors — not fabricated test cases — with the assignment's reasoning exposed so a supervisor (EO/director) can evaluate the results.

The algorithm does not currently record why it picked a given editor. That reasoning cannot be reconstructed after the fact, because the workload figures the algorithm uses are dynamic (they change as more papers get assigned) — so capture has to happen at decision time, not be recomputed later from historical data.

## Scope of the document

Design for two related deliverables that share one instrumentation mechanism:

1. **A permanent, production-bound audit trail.** Instruments the real automatic-assignment functions so every real assignment, from go-live onward, leaves a durable, reviewable record of why that editor was picked. Non-destructive, always-on, ships to production, stays indefinitely.
2. **A one-off historical replay command.** Run only against an imported copy of the production database on a separate instance (never real production data): resets a batch of real historical articles to a "just submitted" state and replays them, in their original chronological order, through the same real assignment functions — producing a comparison report (original historical editor vs. newly-computed editor) for a supervisor to sanity-check the algorithm against what actually happened.

### Explicit scope boundaries

- **Automatic assignment only.** Manual "Select new editor" (`Account.objects.get_editors_with_keywords`, `SupervisorAssignEditor`) and EO assignment (`get_select_eo_by_workload`, `dispatch_eo_assignment`) are not audited by this design.
- **Phase 1 only.** Per the ticket's own phasing ("the list of information about editor — current workload, keyword matching — can be done in the first batch [...] report of auditing of the selection priority depends on the complexity, might be moved to Priority 2"), this design started by capturing only the *chosen* editor's own numbers at decision time. A candidate-by-candidate comparison (every editor sharing a keyword with the article, and why each one won or lost — the "Priority 2 / Report" section of the Editor Selection logic spec) was added later (rev 1.4 for the replay's candidates CSV, rev 1.5 for the stored snapshot): on the default, non-special-issue path the audit also stores one `AutomaticAssignmentCandidate` row per editor sharing a keyword (see *Candidates snapshot*), so the audit page can explain the pick with the values the algorithm actually saw. Special-issue and JCOM assignments, and audits written before the snapshot existed, have no candidates. A full comparison of *every* editor, including those sharing no keyword, remains out of scope.
- **Typesetting-state cleanup for the replay command's reset step is a follow-up.** Nothing in `wjs_review` currently resets typesetting state (that lives in Janeway's separate typesetting plugin); this design covers resetting editor-assignment and review state, and flags typesetting cleanup as needing its own investigation before the replay command is complete for articles that reached that stage.

## Content

### Background: current automatic assignment flow

`wjs/plugins/wjs_review/events/assignment.py` (as of MR !1512) has two production dispatch functions, chosen per-journal by the `WJS_ARTICLE_ASSIGNMENT_FUNCTIONS` setting via `dispatch_assignment`:

- **`default_assign_editors_to_articles`**: for the general (non-special-issue) case, chains `annotate_keyword_ranking` (annotates each `StaffWorkloadParameters` with its keyword overlap with the article) → `annotate_workload_availability` (journal- and month-scoped availability, filtered on `available_workload_month > 0`, which drops editors at or over their cap or disabled) → `rank_editor_parameters` (orders by keyword weight, keyword count, trailing-30-day availability, then id) and takes the first row. Keyword matching ranks but never excludes, so there is no separate fallback branch. The special-issue branch bypasses this pipeline and always uses `get_selected_editor_by_workload` (a different, non-monthly, open-articles-based metric).
- **`jcom_assign_editors_to_articles`**: JCOM/JCOMAL director assignment. Always uses `get_selected_editor_by_workload` against `DIRECTOR_MAIN_ROLE` candidates; never does keyword matching, by design.

Both dispatch functions eventually call `BaseAssignToEditor(editor=user, article=article, request=request, first_assignment=True).run()` to actually create the `WjsEditorAssignment`.

None of this records *why* a given editor was picked — the winning `StaffWorkloadParameters` row's annotations (`matching_keywords_count`, `max_matching_keyword_weight`, `available_workload_month`, `available_workload_30d`, `effective_workload_cap`) are computed, read once via `.first()`, and discarded once the function returns.

### Deliverable 1: Permanent audit trail

#### Data model

A new model, `AutomaticAssignmentAudit` (in `wjs_review/models.py`), one row per automatic-assignment *event*:

| Field | Type | Notes |
| --- | --- | --- |
| `article` | FK → `submission.Article`, `on_delete=CASCADE` | |
| `editor` | FK → `Account`, `on_delete=SET_NULL`, nullable | the chosen editor |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `algorithm` | `CharField` with `TextChoices` | `DEFAULT`, `DEFAULT_SPECIAL_ISSUE`, `JCOM` |
| `matched_by_keyword` | `BooleanField` | `True` when the winner shares at least one keyword with the article; `False` when it shares none, and always on the special-issue/JCOM paths, which never do keyword matching |
| `matching_keywords_count` | nullable `PositiveIntegerField` | set only when `matched_by_keyword=True` |
| `max_matching_keyword_weight` | nullable `PositiveIntegerField` | set only when `matched_by_keyword=True` |
| `workload_available` | nullable `IntegerField` | the winning editor's remaining capacity at decision time — whichever metric actually applied |
| `workload_cap` | nullable `IntegerField` | the cap that was checked against — whichever metric actually applied |
| `workload_available_30d` | nullable `IntegerField` | trailing-30-day availability of the chosen editor (default path only; the ranking tie-break) |
| `keyword_score` | nullable `FloatField` | chosen editor's weighted keyword score (0-1), default path only |

All the fields above and the candidates table below ship in a single migration, `wjs_review.0021_automaticassignmentaudit` (depends on `submission.0089` and `wjs_review.0020_appeal_granted_system_actor`).

#### Candidates snapshot

A second model, `AutomaticAssignmentCandidate`, hangs off the audit (`audit.candidates`, `on_delete=CASCADE`). It is written on the default (non-special-issue) path only, right before the assignment changes any workload, from the same explanation the replay's candidates CSV uses (`events/assignment.explain_default_assignment_candidates`). One row per section editor sharing at least one keyword with the article, the selected editor included:

| Field | Type | Notes |
| --- | --- | --- |
| `audit` | FK → `AutomaticAssignmentAudit`, `on_delete=CASCADE` | |
| `editor` | FK → `Account`, `on_delete=SET_NULL`, nullable | |
| `position` | `PositiveIntegerField` | explanation order: the selected editor first, then the ranked editors, then the excluded ones |
| `rank` | nullable `PositiveIntegerField` | 1-based position in the algorithm's ranking; empty for excluded editors |
| `selected` | `BooleanField` | |
| `reason` | `CharField(128)` | same values as the replay candidates CSV (see *Candidates report*) |
| `keyword_score` | nullable `FloatField` | |
| `matching_keywords_count` | `PositiveIntegerField` | |
| `shared_keywords` | `JSONField` | list of `{"word", "article_weight", "editor_weight"}` |
| `monthly_workload`, `workload_30d`, `workload_cap` | nullable `IntegerField` | values the algorithm saw |

**Design decision — not FK'd to `WjsEditorAssignment`.** `BaseDeassignEditor._delete_assignment` hard-deletes the `WjsEditorAssignment` row on deassignment/reassignment (archiving it to `PastEditorAssignment`). FK'ing the audit record to it (even via `CASCADE`) would destroy the audit history at exactly the moment it becomes most interesting — auditing why a reassignment happened. `AutomaticAssignmentAudit` is therefore an independent, append-only log: multiple rows can exist for the same article over time (a genuine reassignment, or a replay re-run in deliverable 2 both just add another row), and none of them are affected by later workflow changes.

**`workload_available`/`workload_cap` deliberately hold whichever metric actually ran**, rather than being forced onto one unified number. The default path (keyword match or not) uses the monthly, `brake_on`-aware cap (`annotate_workload_availability`: `available_workload_month`, `effective_workload_cap`); the special-issue and JCOM paths use a different, non-monthly, open-articles-based metric (`get_selected_editor_by_workload`). This is a known, already-documented inconsistency between the paths (see the docstring note added on `default_assign_editors_to_articles` in MR !1512) — the audit record just reports the truth about which metric applied, rather than papering over the difference.

#### Capture points

In `default_assign_editors_to_articles`:

- **Default path** (`ranked_parameters.first()` succeeds after `annotate_keyword_ranking` → `annotate_workload_availability` → `available_workload_month > 0` → `rank_editor_parameters`): write an audit row with `algorithm=DEFAULT`, all read off the winning row's existing annotations (zero extra queries). `matched_by_keyword` is `matching_keywords_count > 0`; `matching_keywords_count` and `max_matching_keyword_weight` are set only when matched; `workload_available` = `available_workload_month`, `workload_cap` = `effective_workload_cap`, `workload_available_30d` = `available_workload_30d`. There is no separate fallback branch: an article without keywords is still assigned by workload, with `matched_by_keyword=False`.
- **Special-issue branch** (always uses `get_selected_editor_by_workload`): write an audit row with `algorithm=DEFAULT_SPECIAL_ISSUE`, `matched_by_keyword=False`, `workload_available`/`workload_cap` read off the same returned row (see next section) and `workload_available_30d=None`.

In `jcom_assign_editors_to_articles`: write an audit row with `algorithm=JCOM`, `matched_by_keyword=False`, `workload_available`/`workload_cap` read off the returned row.

An audit row is written only when an editor was actually assigned (i.e., after `BaseAssignToEditor(...).run()` succeeds). "No editor could be found at all" is an operational-alert concern, not an assignment-reasoning concern, and is out of scope here.

#### Required change: `get_selected_editor_by_workload`'s return type

Today, `get_selected_editor_by_workload(users_parameters, journal) -> Account | None` returns only the winning `Account`, discarding the annotated `StaffWorkloadParameters` row (and its `available_workload`) it computed internally. To capture workload data for the special-issue branch and JCOM without duplicating that query, this function changes to **return the winning annotated `StaffWorkloadParameters` row itself** (`StaffWorkloadParameters | None`) — the row already carries `.user`, `.workload`, and the computed `.available_workload`.

This is a real signature change. Call sites and tests that need updating:
- Production: `default_assign_editors_to_articles`'s special-issue branch, `jcom_assign_editors_to_articles` — both currently do `user = get_selected_editor_by_workload(...)`; both become `parameter = get_selected_editor_by_workload(...)` then `user = parameter.user if parameter else None`, plus reading `.available_workload`/`.workload` for the audit row.
- Tests: the existing suite in `wjs_review/tests/test_automatic_article_assignment.py` that calls this function directly and compares the result to an `Account` (`test_get_selected_editor_by_workload`, `test_get_selected_editor_by_workload_include_by_state`, `test_workload_decrease_editor`, and others) need `.user` added to their assertions, with a `None`-check added for the "no editor found" case (since `None.user` would raise).

This is the broadest-blast-radius single change in deliverable 1 and should be its own implementation-plan task with its own dedicated test-update pass.

#### Audit page

Two views in `wjs_review`, both gated to EO/director roles (`base_permissions.has_eo_or_director_role`) and scoped to the current journal:

- `AutomaticAssignmentAuditList` (`wjs-automatic-assignment-audit`, path `automatic-assignment-audit/`, linked from the EO/director menu): `AutomaticAssignmentAudit` rows newest-first, 50 per page. Shows the article (linked to the article audit page), editor, algorithm, a "matched by keyword?" indicator, matching keywords count and max weight, the "Keyword score" column (a percentage, or "—" when there is no score), the workload figures and the timestamp. Filterable by editor, algorithm, matched-by-keyword and a from/to date (`AutomaticAssignmentAuditFilter`); the journal is implicit.
- `AutomaticAssignmentAuditArticle` (`wjs-automatic-assignment-audit-article`, path `automatic-assignment-audit/article/<article_pk>/`): every audit of one article, with the stored candidates of each (rank, reason, score, shared keywords, workloads). 404 when the article has no audit in the journal.

The article's "details" page shows a link to the article audit next to the current editor, only when the user is EO/director and an automatic assignment picked that very editor (`user_can_view_assignment_audit` template tag).

No charts or aggregate statistics in this first cut — a reviewable list is the whole requirement for Phase 1.

### Deliverable 2: Historical replay command

A management command, `replay_automatic_assignment`, intended to run **only** against an imported copy of the production database on a separate instance — never against real production data. The code lives in the throw-away `wjs_review/replay/` subpackage (`command.py`, `logic.py`, tests), with a one-line shim in `management/commands/` so that Django finds the command; deleting the package and the shim removes the tooling. It refuses to run without `--confirm-copy-instance`. CLI arguments: `--journal`, `--date-from`/`--date-to` (or an explicit `--article-ids` list for ad-hoc runs), `--output-file` (see report format below).

For each matching `Article`, processed in **`Article.date_submitted` ascending order** (chronological — not `WjsEditorAssignment.assigned`, which reflects when the *original* assignment happened, not when the article was submitted):

1. **Snapshot the original assignment first**, before any mutation: record the **first editor ever assigned** to the article (the earliest across `PastEditorAssignment.date_assigned` and `WjsEditorAssignment.assigned`). That is the editor the original assignment picked; later reassignments are not what the algorithm should be judged against. This must happen before step 2, since step 2 destroys that information.
2. **Reset the article** to a pristine "just submitted, never processed" state:
   - Delete `WjsEditorAssignment` for the article (no need to archive to `PastEditorAssignment` here, since the original was already snapshotted in step 1 — this reset is destructive by design, on a copy instance only).
   - Delete `WorkflowReviewAssignment` / review rounds (`review.ReviewRound`) associated with the article.
   - Delete `EditorDecision` rows for the article.
   - Reset typesetting state — **flagged as a follow-up investigation**, not designed here (see Scope boundaries).
   - Reset `ArticleWorkflow.state` back to `SUBMITTED`.
   - (Investigation note from brainstorming: no existing command does this. The closest prior art is `rollback_acceptance.py`, which only rolls ACCEPTED back to EDITOR_SELECTED — too narrow — and `WithdrawPreprint`, which transitions to a terminal WITHDRAWN state without deleting assignments or review rounds. This reset command has to be built from scratch, informed by `BaseDeassignEditor._delete_assignment`'s delete/archive pattern used in reverse.)
3. **Simulate "now" as `article.date_submitted`** for the duration of the assignment call (e.g. via `freezegun.freeze_time(article.date_submitted)`), so `annotate_workload_availability`'s "current calendar month" window lines up with the article's real submission month rather than today's date.
4. **Call the real `dispatch_assignment(article)`** — the actual production entry point, unmodified. This creates a real new `WjsEditorAssignment` in the copy instance and, thanks to deliverable 1's instrumentation, a real new `AutomaticAssignmentAudit` row, with no special-cased "test mode" logic anywhere.
5. **Record a report row**: article, original editor (from step 1), newly-computed editor (from step 4), whether they match, plus the `AutomaticAssignmentAudit` fields for that decision.

Because articles are processed in submission order and each one's real `WjsEditorAssignment` is created (step 4) before the next article is evaluated, workload accumulates through the batch exactly as it would have historically — this is what makes the replay's dynamics comparable to what actually happened, rather than testing each article against a frozen, out-of-context snapshot.

#### Report format

The command writes a **CSV file** (`--output-file`, defaulting to a timestamped filename if not given) with one row per replayed article: `article_id`, `title`, `journal`, `date_submitted`, `original_editor`, `new_editor`, `match` (boolean), `matched_by_keyword`, `matching_keywords_count`, `matching_keywords`, `max_matching_keyword_weight`, `keyword_score`, `monthly_workload`, `workload_30d`, `workload_cap`. `monthly_workload` and `workload_30d` are the audit's `workload_available` and `workload_available_30d` (renamed in the CSV only; model fields and audit page are unchanged). `matching_keywords` lists the winner's shared keywords, `; `-separated, each as `<word> (article <w_A>, editor <w_E>)`, ordered by article weight descending (a NULL article weight counts as 100, like in the keyword score) then word; a NULL article weight prints as `—`. It is empty when there is no audit, off the default keyword path (`algorithm` other than `DEFAULT`), or when the winner shares no keyword. Skipped articles are not written to either CSV: after the summary the command prints one `Skipped <article_id>: <reason>` line per skipped article. A short summary (total processed, match/mismatch counts) is printed to stdout. This is deliberately a separate artifact from the deliverable-1 audit page — a permanent, ongoing production audit page has no use for an "original editor" column, since that concept doesn't exist for a genuinely new submission.

#### Candidates report

A second CSV (`--candidates-file`, defaulting to the main path with `.csv` replaced by `_candidates.csv`, or `_candidates.csv` appended when the main path has no `.csv` suffix) explains the default algorithm's choice: one row per (article, editor) for every section editor of the journal sharing at least one keyword with the article, the selected editor included. Only replayed articles on the default, non-special-issue path get rows: special-issue articles and journals whose assignment function is not `default_assign_editors_to_articles` (e.g. JCOM) get none. Columns: `article_id`, `editor` (`Full Name <email>`), `rank` (1-based position in the algorithm's ranking, empty for excluded editors), `selected` (`True`/`False`), `reason`, `keyword_score` (three decimals), `matching_keywords_count`, `matching_keywords` (same format as above), `monthly_workload`, `workload_30d`, `workload_cap`. Rows follow article submission order, then selected first, then rank, then excluded editors by keyword score descending.

`reason` is one of:
- `selected` — rank 1;
- excluded editors, checked in this order: `author of the article`; `not available (disabled, on leave or no workload)` (not returned by `get_available_editor_parameters`); `monthly limit reached` (available but out of the ranking, i.e. `available_workload_month <= 0`);
- ranked losers, compared with rank 1 along `rank_editor_parameters`' ordering: `lower keyword score`; `same score, fewer shared keywords`; `same score and shared keywords, less 30-day availability`; `full tie, lost on id`.

The explanation (`events/assignment.explain_default_assignment_candidates`) is computed in `ReplayAutomaticAssignment._replay` (`replay/logic.py`) right before `dispatch_assignment`, inside the same `freezegun` block, so it sees exactly the state the algorithm sees. The live assignment calls the same function to store the candidates snapshot (see *Candidates snapshot*). It does not restate the selection rules: it reuses the helpers `default_assign_editors_to_articles` itself is built from (`is_special_issue`, `get_default_section_editors`, `get_available_editor_parameters`, `exclude_article_authors`, `annotate_default_candidates`, `rank_default_candidates`); the rank is the position in `rank_default_candidates`' output, and tie reasons compare the annotated values with rank 1. `selected` is filled after dispatch (candidate editor == new editor); rank 1 and the actual pick must agree, which the tests assert.

### Related changes shipped in the same MR

Not part of the audit itself, but made while validating the assignment against production-like data and merged together with it:

- **`brake_on` is read-only for directors.** `brake_on` (`-1` switches an editor off from automatic assignment) is now editable only by the EO and staff. In the director's "update editor parameters" form (`jcom_profile/forms.py`, `jcom_profile/views.py`) the field is `disabled` unless the view passes `can_edit_brake_on=True`; a disabled field ignores submitted data, so a crafted request cannot change it. Test: `test_brake_on_is_editable_only_by_eo`.
- **Editor keywords are listed highest weight first.** `get_editor_keywords` (journal editors page) orders by `-weight` then keyword word, so the page shows the same priority the ranking uses. Test: `test_get_editor_keywords_ordered_by_weight_then_word`.

### Testing strategy

- Audit-record creation for all four cases: default keyword-matched, default without keyword match, special-issue, JCOM; the candidates snapshot on the default path (none on special-issue/JCOM) and its display on the article audit page.
- The `get_selected_editor_by_workload` signature change: update the ~9 existing direct-call tests to unwrap `.user`, add a `None`-handling case.
- The reset step: given an article with existing `WjsEditorAssignment`, review rounds, and `EditorDecision` rows, confirm all are cleared and `ArticleWorkflow.state` returns to `SUBMITTED`.
- Chronological replay + time simulation: a small batch of test articles processed in order, confirming workload accumulates across the batch the same way sequential real-time assignment would.
- CSV report output shape (both CSVs), skipped articles on stdout, one test per candidates `reason`, rank 1 agreeing with the actual pick, no candidates on the special-issue/JCOM paths.

### Explicit follow-ups (not designed here)

- **Phase 2**: comparison of *every* editor, including those sharing no keyword (the Editor Selection logic spec's "Priority 2 / Report" section) — deferred per the ticket's own phasing. The candidates CSV (replays) and the stored candidates snapshot (live assignments, default path) cover the editors sharing at least one keyword.
- **Typesetting-state cleanup** for the reset command, for articles that reached the typesetting plugin.
- EO-assignment auditing and manual-selection auditing remain out of scope entirely (not just deferred).
