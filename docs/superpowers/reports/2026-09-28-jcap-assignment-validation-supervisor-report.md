# JCAP automatic editor assignment: why historical data can't validate it

Sep 26, 2026 · @Iacopo

With JCAP's current data, comparing the new automatic assignment with past editor choices can't tell us whether the algorithm is good. After correcting the keyword data, replays of 40 articles matched the historical editor in 4 cases with the first algorithm, 6 with the second, and **14 with the current one**, which ranks editors by an overall keyword score. The current algorithm lets subject match, rather than workload, decide most assignments. The monthly limits are still placeholders, and past choices remain a weak yardstick, so real validation should happen going forward on the audit page.

## What we tried

We replayed 10 randomly chosen JCAP articles (submitted May 2025 – March 2026) through the new algorithm, in submission order, on a copy of the JCAP database. The algorithm picked the same editor as the first historical choice for only 1 article out of 10.

- In 5 of the 10 articles, no editor shared a single keyword with the article, so the choice fell back to workload alone.
- In 8 of the 10 cases, the chosen editor had a workload limit of 9999, meaning no real limit.

A 1-in-10 agreement rate would normally be a red flag. Here it can't be read either way: the next sections show that the inputs the algorithm relies on are mostly missing or placeholders.

## Keywords: duplicate keyword records, not missing expertise

In the current JCAP data, 55% of articles (1,870 of 3,374) share no keyword with any editor. The cause is a data error, not a gap in the board's expertise: most editors' keywords exist twice in the system.

- Editors are linked to an old copy of each keyword, in lowercase and without a group, for example "dark energy theory".
- Articles use the new, grouped copy of the same term, for example "Dark energy theory".
- The algorithm compares keyword records, not their text, so the two copies never match. This affects 116 of the terms editors hold. Only 12 editor terms have no counterpart among articles' keywords, for example "cosmic rays", "dark matter" and "lensing".

On a copy of the database we moved each editor to the grouped copy of their keywords. After that:

| Measure | Before | After |
| --- | --- | --- |
| JCAP articles with no matching editor | 1,870 (55.4%) | 1 (the only article with no keywords) |
| Articles matching 5 or more editors | 973 | 3,328 |
| Assignable editors with no usable keyword | 23 of 70 | 0 of 70 |

Every keyword group is now covered by editors, except 2 of the 11 Neutrinos keywords. The correction itself will be applied to the live system separately.

Our first replay ran before this correction, so its result (1 match in 10) isn't meaningful for subject matching.

## Second replay: 40 articles on corrected data

On the corrected keywords, with every editor given a test limit of 3–5 papers a month, the algorithm agreed with the historical first editor for 4 of 40 articles. It ran on 40 random JCAP articles submitted between January 2025 and September 2026.

- Every article was matched on subject: none fell back to workload alone. In the first replay, half did.
- The load was spread across 23 different editors, none receiving more than 4 of the 40.
- Most choices rested on little overlap: 18 articles were decided by a single shared keyword and 15 by two.

Why the other 36 articles went to a different editor:

| Reason | Articles |
| --- | --- |
| The historical editor also matched, but ranked lower on keyword weight or count | 31 |
| The historical editor matched equally well, and lost on workload or the tie-break | 4 |
| The historical editor shares no keyword with the article | 1 |

So the difference comes from how candidates are ranked, not from missing editors. The algorithm ranks first by each editor's strongest single keyword weight, and only then by how many keywords they share with the article. Two consequences:

- An editor with one keyword at weight 100 outranks one who shares three keywords at weight 80. Editors' weights range from 40 to 100.
- A few keywords carry a weight of 999, which beats everything else. Two editors won 4 of the 40 articles this way. The value looks like a data-entry artefact rather than a deliberate choice.

## What decided each assignment

The most decisive factor was **workload**: it picked the editor in 22 of the 40 assignments. Keywords decide who is in the running, but many editors usually tie at the top keyword score (a median of 21 matching editors per article), and then the editor with the most free capacity that month wins.

The algorithm applies these factors in order, each one only breaking ties left by the previous one:

1. **Eligibility.** The editor must be a JCAP section editor, enabled, not on leave, not an author of the paper, and have a monthly limit above 0.
2. **Subject match.** Only editors sharing at least one keyword with the article are considered.
3. **Strongest keyword weight.** Each editor sets a weight (40–100) for each of their keywords; the editor whose best matching keyword has the highest weight ranks first.
4. **Number of shared keywords.** Among editors tied on weight, the one sharing more keywords with the article ranks first.
5. **Capacity left this month.** An editor already at their monthly limit is skipped entirely. Among the remaining tied editors, the one with the most free slots wins.
6. **Tie-break.** If everything is still equal, the editor registered earliest wins.

Which factor made the final choice in the 40-article replay:

| Deciding factor | Assignments |
| --- | --- |
| Tied on keywords; the editor with most capacity left won (or the tie-break) | 15 |
| Strongest keyword weight | 10 |
| Number of shared keywords (tie on weight) | 8 |
| A better-matching editor was already at their monthly limit | 7 |

Workload decided 22 assignments in total (15 + 7) and the keyword ranking 18. So how editors set their monthly limits matters as much as their keywords. With test limits of 3–5, capacity shaped the outcome strongly. Real limits could shift this balance.

## Third replay: the same 40 articles on the updated algorithm

We reran the same 40 articles after the algorithm was updated. The main change: when editors tie on keywords, the tie is now broken by how many papers each received in the **last 30 days**, instead of the current calendar month. The result: **6 of 40** matched the historical editor, up from 4. The data was a fresh copy of the same database, with the same keyword correction and new test limits of 3–5 papers a month.

|  | Second replay (previous algorithm) | Third replay (updated algorithm) |
| --- | --- | --- |
| Matches with the historical editor | 4 of 40 | 6 of 40 |
| Same editor chosen in both runs | — | 27 of 40 |
| Editors used / most papers for one editor | 23 / 4 | 21 / 5 |
| Decided by workload / by keywords | 22 / 18 | 22 / 18 |
| Won through a weight-999 keyword | 4 | 5 |

- **Workload is still the most decisive factor:** 22 of 40 assignments again. There were 15 keyword ties settled by capacity, and 7 cases where a better-matching editor was already at the monthly limit.
- **Why the other 34 went elsewhere:** in 29 cases the historical editor matched but ranked lower on keywords. In 3 they tied and lost on workload. In 2 they shared no keyword with the article.
- **Monthly limits can be exceeded across a month boundary.** The limit only counts the current calendar month, so an editor can receive more than their limit in any 30-day stretch that spans two months. One editor was chosen while already 6 papers over her limit for the last 30 days, because her weight-999 keyword outranks every other consideration. The 30-day figure only breaks ties; it never excludes anyone.
- **The comparison with the second run isn't exact:** the test limits were drawn at random again, so part of the 13 changed choices comes from different limits rather than from the new tie-break. The limits used this time are saved, so future runs can be compared exactly.

## Fourth replay: ranking by overall keyword score

Following the ranking question raised above, the algorithm now gives each editor an **overall keyword score**. It counts every keyword the editor shares with the article, weighted both by how important the keyword is to the article (the author's order) and by the editor's own weight for it. Weights above 100, such as 999, count as 100. Editors with the same score are separated by the number of shared keywords, then by workload over the last 30 days. The score is shown on the "Select new editor" page and on the audit page.

We reran the same 40 articles on a fresh copy of the database, with the same keyword correction and **exactly the same** test limits as the third replay, so the difference comes from the ranking alone.

|  | Third replay | Fourth replay (keyword score) |
| --- | --- | --- |
| Matches with the historical editor | 6 of 40 | **14 of 40** |
| Historical editor ranked 1st on keywords | — | 13 of 40 |
| Historical editor in the top 3 on keywords | — | 29 of 40 |
| Editors used / most papers for one editor | 21 / 5 | 28 / 4 |
| Won through a weight-999 keyword | 5 | 1 |
| Winner shared 2 or more keywords | 21 of 40 | 33 of 40 |

- **Subject match now decides.** Winners typically cover about two thirds of the article's weighted keywords (median score 65%). Broad coverage beats a single strong keyword.
- **Why the other 26 went elsewhere:** the replay now records, at the moment of each assignment, why every editor with a matching keyword was not chosen. For the historical editor: in 19 cases the chosen editor had a higher keyword score; in 4 the historical editor had already reached their monthly limit; in 1 they had the same score and shared keywords but less availability over the last 30 days; in 2 they shared no keyword with the article or are no longer an editor.
- **The load is spread more widely:** 28 editors used, none getting more than 4 papers.
- **Monthly limits can still be exceeded over 30 days:** 3 winners were over their limit for the last 30 days. The 30-day figure only breaks ties; whether it should also exclude editors is an open policy question.

## Workload: limits were placeholders

Most JCAP editors had a workload limit of 9999, so the workload rules never came into play. The algorithm is designed to spread papers by each editor's monthly capacity. With effectively unlimited capacity, ties between equally matching editors were broken by who had the fewest open papers, and then by an internal id, which is close to arbitrary.

- On the copy database we have since set every assignable JCAP editor to a test limit of 3–5 papers a month, chosen at random. That makes the workload side testable, but these are invented numbers, not the board's real availability.
- The replay also uses today's settings for every past article: today's limits, today's editor list and today's keywords. Editors who joined, left or changed their availability since then are treated as if they had always been in their current state.
- Papers replayed earlier in a run stay counted as open for the rest of the run, whereas in reality many were decided and freed the editor. Over a period of several months this makes editors look busier than they were.

## History: past choices aren't a yardstick

Even with clean data, "the algorithm picked the same editor as happened in the past" is a weak test, because past choices weren't made by these rules.

- Past first assignments were largely made or overridden by hand: by the old automatic rules, by directors, or after an editor declined.
- A different choice isn't necessarily a worse one. Several editors can be equally suitable for a paper, and the algorithm deliberately spreads the load.
- Where the algorithm had no keyword information (half the articles), a mismatch says nothing about subject fit.

## What we need before a meaningful validation

The blocker is the editorial board data, not the software. Once the steps below are done, the same replay tool can be rerun and its results will mean something.

1. **Merge the duplicate keyword records** so editors and articles share the same keywords. This is being applied to the live system separately. The 12 editor terms with no current counterpart need a person to map them.
2. **Confirm the new ranking.** Broad weighted overlap now comes before the single strongest keyword (see the fourth replay), and weights above 100 are capped at 100. Remaining question: should values like 999 be allowed at entry at all?
3. **Set real workload limits** for every JCAP editor, meaning the number of new papers each can take per month. In the current data, 38 of 71 editors are at 999 or 9999.
4. **Then validate going forward rather than backward.** Every automatic assignment now records why that editor was chosen, and the editorial office can review this on the new audit page. A few weeks of real assignments reviewed there will tell us more than any replay of old papers.
