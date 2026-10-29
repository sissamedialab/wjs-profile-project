# Weighted Keyword Score for Editor Ranking — Design

## Revisions
| Revision | Authors | Date | Changes summary |
| -------- | ------- | ---- | --------------- |
| 1.0 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-29 | Initial draft |
| 1.1 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-29 | Shared-keyword count breaks exact score ties (owner decision), e.g. when all editor weights are 0 |

## References
| Document | URL / path | Notes |
| -------- | ---------- | ----- |
| Editor Selection logic spec | https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic#implementation-details | Wiki spec for editor selection. Its keyword-ranking rule ("highest keyword weight first") is superseded by this design; see "Wiki update" below |
| Editor-assignment review findings design | `docs/superpowers/specs/2026-09-22-editor-assignment-review-findings-design.md` | Introduced the current pipeline (`annotate_keyword_ranking`, `annotate_workload_availability`, `rank_editor_parameters`) that this design changes |
| Automatic-assignment audit design | `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md` (MR !1513) | The audit trail and replay that must report the new score (rev 1.3) |
| MR !1512 | https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1512 | Branch `feature/automatic-assignment-keyword-matching`: the scoring change lands here |
| MR !1534 | https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1534 | Branch `feature/issue-3136-automatic-assignment-audit`: the audit/report changes land here |
| JCAP replay reports | `docs/superpowers/reports/` on !1534 | Evidence that motivated this change |

## Subject

Editors are currently ranked on keywords by two criteria:
1. the **highest editor weight** among the keywords they share with the article;
2. then the **number** of shared keywords.

This ranking has two blind spots:
- The **article's** keyword weights are ignored. Authors rank their keywords, stored as `KeywordArticle.weight`: 100 / 75 / 50 / 25 by position.
- One strong keyword beats broad coverage. An editor sharing one keyword at weight 100 outranks one sharing three keywords at weight 80.

In the JCAP replay (40 real articles), the historical editor usually was a candidate but ranked lower on keywords.

An offline comparison on the same 40 articles scored every eligible editor with the formula below. Ranks count keyword ranking only; monthly limits are not applied.

| Historical editor's rank | Current ranking | Weighted score |
| --- | --- | --- |
| Rank 1 | 7 / 40 | 13 / 40 |
| Top 3 | 21 / 40 | 29 / 40 |
| Median rank | 3 | 2 |

Under the weighted score the historical editor moved up in 21 articles, down in 5, and kept its rank in 12.

## Scope

In scope:
- One weighted keyword score, used by both automatic assignment and the manual "Select new editor" page. It replaces weight-then-count as the keyword criterion.
- Showing the score on the "Select new editor" page.
- Recording and reporting the score in the automatic-assignment audit trail: the audit record, the audit page, and the replay CSV.

Out of scope:
- Workload rules: the monthly exclusion on `available_workload_month > 0`, the `brake_on = -1` sentinel and the 30-day availability are unchanged.
- Special-issue and JCOM assignment. They don't use keywords.
- Matching on keyword groups.
- Changing how editor or article weights are entered.
- Merging the duplicate `Keyword` records. That is applied externally.

## Content

### The score

For an article `A` and an editor `E`, where the editor keywords come from `E`'s `StaffWorkloadParameters` for `A`'s journal:

```
keyword_score(E, A) = Σ_{k ∈ K(A) ∩ K(E)} w_A(k) · min(w_E(k), 100)
                      ─────────────────────────────────────────────
                              Σ_{k ∈ K(A)} w_A(k) · 100
```

- `w_A(k)` is `KeywordArticle.weight` for keyword `k` on article `A`. An empty (NULL) weight counts as **100**.
- `w_E(k)` is `StaffKeyword.weight`. Values above 100 (e.g. 999) count as **100**.
- The range is 0–1: 1 means the editor covers every article keyword at full weight.
- The score is **`None`** when the editor shares no keyword with the article, or when the article has no keywords.

Worked example. The article has four keywords weighted 100, 75, 50 and 25. The editor shares the first (editor weight 80) and the third (editor weight 100):

```
(100·80 + 50·100) / ((100+75+50+25) · 100) = 13000 / 25000 = 0.52
```

### Where it's computed

- `wjs/plugins/wjs_review/users.py::get_editors_with_keywords` gets a new annotation, `keyword_score` (float or `None`).
    - It's computed in a **correlated subquery over `StaffKeyword`**, filtered to the editor's parameters for the article's journal and to the article's keyword ids.
    - The per-keyword article weight comes from a `Case`/`When` built from the article's `KeywordArticle` rows. There are at most a handful per article.
    - The denominator is a per-article constant, computed in Python.
    - It must **not** be a `Sum` in the main query. That query already joins past editor assignments (`article_editor`), which would multiply the sum.
- The existing annotations `num_shared_kwds` and `max_matching_keyword_weight` stay for display and for the audit. `num_shared_kwds` now breaks exact `keyword_score` ties (see "Ranking"); `max_matching_keyword_weight` no longer orders anything.
- `events/assignment.py::annotate_keyword_ranking` copies `keyword_score` onto the `StaffWorkloadParameters` rows through a correlated subquery, as it already does for the other two annotations.

### Ranking

`rank_editor_parameters`, used by automatic assignment, orders by:
1. `F("keyword_score").desc(nulls_last=True)`: editors with no shared keyword come last, never first;
2. shared-keyword count (`matching_keywords_count`) desc: breaks exact score ties, e.g. when every editor keyword weight is 0 and all matchers score 0.0 (owner decision, rev 1.1);
3. `F("available_workload_30d").desc(nulls_last=True)`;
4. `id`.

`get_editors_with_keywords`' own default ordering (by `num_shared_kwds`) is unchanged for other callers.

The manual "Select new editor" queryset (`views.py`, the section-editor listing that today orders by `max_matching_keyword_weight`) orders the same way: `keyword_score` desc nulls last, then `num_shared_kwds` desc, then `available_workload_30d` desc nulls last, then `id`. Its non-excluding behaviour is unchanged: everyone stays visible, availability is shown by badges.

### "Select new editor" page

`assign_editor/elements/editor_table.html` and `editor_list.html` get a new "Keyword score" column. It sits next to the matching keywords, shows the score as a whole percentage (e.g. `52%`), and shows "—" when the score is `None`.

### Audit trail and reports (on !1534, after !1512 is merged into it)

- `AutomaticAssignmentAudit` gets a new field, `keyword_score` (nullable `FloatField`, `verbose_name` "Keyword score"), in migration `wjs_review.0020`.
    - The default path stores the chosen editor's `keyword_score`.
    - Special-issue and JCOM rows leave it empty.
    - Existing rows stay empty.
- The audit page shows a "Keyword score" column as a percentage, with "—" when empty.
- The replay CSV has a new `keyword_score` column right after `max_matching_keyword_weight`: the score to 3 decimals, empty when there is none.
- The audit design spec is bumped to rev 1.3 to add the field and the column.

### Flow of changes between branches

1. The score, the ranking, the manual page and their tests land on `feature/automatic-assignment-keyword-matching` (!1512).
2. !1512 is merged into `docs/automatic-assignment-audit-design` (!1513), then into `feature/issue-3136-automatic-assignment-audit` (!1534). This is the same route as the previous port.
3. On !1534: the audit field, the page column, the CSV column, and migration 0020.

### Testing strategy

All tests use the real database; nothing is mocked.

**On !1512, the score** (unit tests on `get_editors_with_keywords` / `annotate_keyword_ranking`):
- It matches the worked example exactly (0.52).
- Editor weight 999 counts as 100.
- A NULL article weight counts as 100.
- No shared keyword gives `None`, and an article with no keywords gives `None`.
- Keywords the editor holds only in another journal don't count.
- Past assignments of the editor on the article don't change the score (join-multiplication guard).

**On !1512, the ranking:**
- Broad weighted coverage beats a single strong keyword (the behaviour change).
- An exact score tie goes to the editor sharing more keywords, then to the one with more `available_workload_30d`, then the lower id.
- No-match editors rank last.

**On !1512, end to end:**
- Through the HTTP submission pipeline, the higher-scoring editor is assigned.
- Manual page: the order follows the score, and the score column renders, including "—".
- Existing !1512 tests that encode "weight first" are updated to the new rule, each with a short note on why its expectation changed.

**On !1534:**
- The audit row stores the winner's score on the default path, and `None` on special-issue and JCOM.
- The audit page renders the column, including "—" for legacy rows.
- The replay CSV contains `keyword_score`.

**Acceptance check:** rerun the replay on the same 40 JCAP articles, with the same seeded limits (`docs/superpowers/reports/2026-09-29-jcap-limits-3-5-seed3136.csv`), and compare with the third replay (6/40 matches). Report the match count and the historical editor's rank.

### Wiki update

The Editor Selection logic wiki page is out of reach of the repo. The implementation hands the owner a replacement text for its keyword-ranking rule: the formula above, the 999 cap and NULL-as-100 rules, and the new ranking order. The owner pastes it into the wiki.

### Explicit follow-ups (not designed here)

- Whether the 30-day availability should also **exclude** editors, not only break ties. The replay showed winners over their limit across a month boundary.
- Validating or capping editor weights at entry time, so values like 999 aren't stored.
- Keyword-group matching.
