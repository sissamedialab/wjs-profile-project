# Editor Selection logic — replacement text for the keyword-ranking rule

Paste this into the "Keywords matching" part of https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic, replacing the "highest keyword weight, then number of matching keywords" rule.

## Keyword score

Each candidate editor gets a keyword score between 0 and 1:

    score = Σ over keywords shared with the article of (article weight × editor weight)
            ──────────────────────────────────────────────────────────────────────────
            Σ over all article keywords of (article weight × 100)

- The article weight is the author's keyword weight (100 / 75 / 50 / 25 by position). A missing weight counts as 100.
- The editor weight is the editor's configured keyword weight. Values above 100 count as 100.
- An editor sharing no keyword with the article has no score and is ranked after every editor with a score.

## Ranking

Candidates are ordered by:
1. keyword score, highest first;
2. number of shared keywords, highest first (this separates editors whose scores are exactly equal, for instance when all their keyword weights are 0);
3. availability over the last 30 days, highest first;
4. internal id.

Editors at their monthly limit are still excluded from automatic assignment. The same order is used on the "Select new editor" page, which also shows each editor's score.
