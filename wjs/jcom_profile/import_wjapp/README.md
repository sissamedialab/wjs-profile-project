# wjapp → WJS article importer

Imports articles (and their full editorial history) from wjapp into WJS/Janeway,
replaying each wjapp action through WJS's own review logic so the resulting
article ends up in a state WJS could legitimately have produced on its own.

## Entry points

- `wjs/jcom_profile/management/commands/import_articles_from_wjapp.py` — the
  main import command: per-article orchestration (archive handling, history
  loading, per-action dispatch, history-correction hooks).
- `wjs/jcom_profile/management/commands/import_editor_keywords_from_wjapp.py` —
  separate command for editors' role/workload/keyword associations, independent
  of the article importer (see `import_editor_keywords.py` below).

## This package (`import_wjapp/`)

- **`import_logic.py`** — one class per wjapp `actionID` (`BaseActionManager`
  subclasses, e.g. `ED_REJ_DOC`, `AU_CONF_VER`, `TYP_TAKES_CHARGE`...), each
  implementing `run()` to replay that action against WJS's real logic classes
  (`wjs_review/logic.py`, `logic__production.py`). The main command dispatches
  via `getattr(import_logic, action["actionID"])(...)`.
- **`import_file_manager.py`** — locates/copies/zips the actual submission
  files from the wjapp archive trees (`current_archive`/`old_archive`) into
  WJS, per article version.
- **`import_specific_corrections.py`** — `repair_history_specific_article()`:
  one `if preprintid == "..." and imported_version_num == N:` block per
  known-bad wjapp article, relabeling/deleting/inserting history entries so
  the replay matches a real, WJS-legal transition sequence. Grows with every
  journal imported; kept separate from the main command to keep that file
  focused on import mechanics.
- **`import_exception_list.py`** — explicit, incrementally-widenable opt-ins
  for *general* fixes too risky to apply corpus-wide in one step. Each fix has
  its own preprintid allowlist, a by-year widening helper, and a full-rollout
  flag; the intended path is: explicit preprintids → widen by year → flip the
  `_ALL` flag once prior years are proven clean.
- **`import_production_hold.py`** — specs#3131: the replayed wjapp acceptance
  holds every imported JCAP paper in `ACCEPTED` until later history shows it
  really left that state; released when e.g. a typesetter later takes charge.
  Deliberately does not import `import_logic` (which patches review logic
  process-wide on import), so it stays independently testable.
- **`import_editor_keywords.py`** — editors' wjapp role/workload/keyword
  import, shared by its own management command; independent of the article
  importer.

## History-correction patterns (in order of first choice)

1. **General correction** (`import_articles_from_wjapp.py`,
   `correction_*`/`remove_*` methods) — a structural pattern matched across
   the whole corpus (e.g. `correction_remove_repeated_actions_same_agent`).
   Prefer this only once a pattern recurs across many articles with identical
   shape; the team has preferred narrow, per-article fixes over widening a
   general correction given the ~21000-paper corpus's blast radius.
2. **Specific correction** (`import_specific_corrections.py`,
   `repair_history_specific_article()`) — the default for anything
   preprintid-specific: relabel an action's `actionID`, delete one or a whole
   version's worth of entries, or `history.insert(...)` a synthetic action
   (minimal dict: `actionID`/`agentCod`/`actionDate`/`actionCod`/`versionCod`/
   `actHistCod`, `actHistCod=0` for anything with no real wjapp counterpart).
   Deleting history entries only suppresses which actions drive WJS's FSM —
   it does not remove the underlying message/correspondence records, so the
   human audit trail survives even when the state-machine replay is trimmed.
3. **Action-class side-effect guard** (`import_logic.py`, a narrow
   `special_cases`/`skip_*_cases` set keyed on
   `(preprintid, imported_version_num, actHistCod)`) — for when the history
   shape itself is fine but one action's handler has a side effect that's
   wrong for this one instance (e.g. `AuthorSubmitRevisionAction` always
   re-imports its own version's files; skip that specifically when the
   action is only being reused to supply a missing FSM transition, not a
   real new file submission).

`repair_history_specific_article()` runs before `repair_history_general()`
for every article/version.

## Debugging a failing import

- The log's own "Looking at action" trace stops at the first error — for the
  real, full history (including actions never reached), query wjapp's own
  DB directly:
  ```sql
  SELECT v.versionNumber, a.actionID, ah.actionDate, ah.actHistCod
  FROM Document d
  JOIN Version v ON v.documentCod = d.documentCod
  JOIN Action_History ah ON ah.versionCod = v.versionCod
  JOIN Action a ON a.actionCod = ah.actionCod
  WHERE d.prePrintID = '<preprintid>'
  ORDER BY v.versionNumber, ah.actionDate;
  ```
- `repair_history_specific_article()` also logs the pre-repair history for
  every item as `original history specific article correction ...` WARNING
  lines — grep those for ground truth when the SQL route isn't available.
- The authoritative "what's still failing" source is the external utils DB's
  `journal_preprintid_import_status` table (`import_status='ERROR'`), not the
  log-derived `errors_details*.csv` — the CSV has been seen to silently drop
  some exception types and to mis-attribute a traceback to the wrong
  preprintid when a crash happens before that article's own log line prints.

## Companion scripts (`wjs-utils-project/import/`)

Standalone scripts (plain `psycopg`/`mariadb` connections, no Django setup),
used alongside the importer for consistency-checking and log analysis after a
batch, each with its own config-file template in the same directory:

- `generate_import_status_table.py` — builds/maintains the external utils DB's
  `journal_preprintid_import_status` table (see Debugging above).
- `check_dates_consistency.py` / `check_titles_consistency.py` /
  `check_users_consistency.py` — compare wjapp vs. wjs after import: key
  dates (submission/acceptance/publication), article titles, and that every
  wjapp user is known to wjs for the journal, respectively.
- `find_changed_preprintids.py` — finds preprintids changed in wjapp after a
  cutoff date, to scope a targeted reimport instead of a full re-run.
- `force_final_state_for_import_errors.py` — forces the visible workflow
  state of import-failed articles to match their known wjapp final state,
  without a real re-import (for articles not worth fully repairing yet).
- `sum_article_files_size.py` — sums the on-disk size of a journal's article
  files directories (optionally scoped to a submission year), querying wjs
  directly for the article ids.
- `log_analysis/group_import_errors.py` — groups a raw import log's errors by
  signature into the `errors_details*.csv` used to drive fix triage (see
  Debugging above for its known gaps vs. the status table).
- `log_analysis/analyze_missing_pdfs.py` — checks "File pdf not found"
  failures against the raw, pre-`dedup_sanitize.py` archive.
- `log_analysis/check_pum_sends_to_eo_fix_impact.py` — checks a fix's blast
  radius against the corpus without re-importing.
- `log_analysis/find_open_withdraw_appeal_cases.py` — finds every
  preprintid/version whose history opens an appeal and then withdraws it.

## Known gotchas

- **Open importer limitation, not a solved case**: *any* action whose handler
  calls `import_version_files()` for version `N` rebuilds that version's
  `build_archive_dir`/zip unconditionally (`mkdir(..., exist_ok=False)`), with
  no check for whether it was already built earlier in the same run. If two
  actions end up targeting the same version in the same run — whether that's
  two genuine wjapp actions, or a specific correction that relabels/inserts
  an action reusing a file-importing handler for its FSM side effect only —
  the second one crashes with `FileExistsError`. There is no general fix for
  this; each occurrence so far has been resolved with a narrow, one-off
  action-class side-effect guard (pattern 3 above), not a systemic one. Watch
  for it whenever a specific correction reuses a handler like
  `AuthorSubmitRevisionAction` purely to supply a transition.
- `AttentionCondition` rows are keyed to `Article` (not `ArticleWorkflow`), so
  they used to survive `reset_article_data()`'s reimport reset untouched —
  **fixed**: `reset_article_data()` now also clears
  `AttentionCondition.objects.filter(article__id=article.id)`. Still run
  `populate_attention_conditions` (wjs_review) after any batch, since it's
  the only thing that resolves *time*-based ACs stale from a previous state;
  event-based ones are only ever resolved by the explicit business-logic
  call at the point of a real transition. On an environment predating this
  fix, wipe and rebuild (`AttentionCondition.objects.all().delete()`, then
  `populate_attention_conditions`) once, for a guaranteed-clean slate.
