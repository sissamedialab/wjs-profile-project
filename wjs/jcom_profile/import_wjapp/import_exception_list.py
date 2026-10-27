"""Explicit, incrementally-widenable opt-ins for import fixes that can't be safety-checked in full.

Each fix below has its own explicit preprintid set, plus a helper function that also supports
widening by wjapp year (trailing 2 digits of the preprintid - the same convention used by
wjs-utils-project/import/analyze_missing_pdfs.py's _expected_year_glob) and a full-rollout flag.
The intended rollout path for a fix like this is: (1) list the currently-failing preprintids
known to need it, to test broadly without touching anything already succeeding; (2) once that
looks clean, widen by year, one year at a time, watching each year's own re-import for new
regressions; (3) once confident, flip the ALL flag for the remaining batch. Every step is a
plain data edit, and narrowing back down (remove a year, flip ALL back to False) is just as easy
as widening - there's no one-way door here.

This is a deliberate, temporary workaround, not the correct general fix - once a fix's safety
across the full historical batch is confirmed (or that batch is re-imported from scratch), the
gating for it should be removed and the fix applied unconditionally at its call site.
"""


def _preprintid_year_matches(preprintid: str, years: set[int]) -> bool:
    """True if `preprintid`'s trailing 2 digits (the wjapp year convention) are in `years`.

    `years` holds full 4-digit years (e.g. {2025}); this compares against their last 2 digits,
    matching how the preprintid itself only ever encodes the year that way.
    """
    trailing = preprintid[-2:]
    if len(trailing) != 2 or not trailing.isdigit():
        return False
    return any(trailing == f"{year % 100:02d}" for year in years)


# Requestproofs.run() (import_logic.py) unconditionally creates a new GalleyProofing on the
# article's latest TypesettingRound. PUM_SENDS_TO_EO resets state without creating a new round,
# so if wjapp's real history sends the article through PM_SENDS_FOR_PROOF_R a second time
# against that same round - with no new typesetter upload in between (e.g. after PUM_SENDS_TO_EO
# instead of a normal AU_SENDS_CORRECT/new-round cycle - see JCAP_035P_0210/4) -
# RequestProofs._create_proofing_assignment() creates a second GalleyProofing for one round,
# violating the "at most 1 GalleyProofing per round" invariant (import_logic.py:4449) and
# causing a later .get() to raise MultipleObjectsReturned. Creating a fresh TypesettingRound
# here instead would be fabricated data (no real new typesetting work happened), so the fix
# skips the full RequestProofs() call and just advances the FSM state directly, reusing the
# existing round/GalleyProofing. Scoped the same incremental way as the fix above - see that
# docstring for the rollout path.
REQUEST_PROOFS_DEDUP_GALLEY_PROOFING_PREPRINTIDS: set[str] = {
    "JCAP_035P_0210",
    "JCAP_077P_0812",
    "JCAP_054P_1013",
    "JCAP_044P_0815",
    "JCAP_012P_0316",
    "JCAP_033P_0417",
    "JCAP_046P_0917",
    "JCAP_067P_0917",
    "JCAP_009P_0918",
    "JCAP_002P_0420",
    "JCAP_041P_1120",
    "JCAP_039P_0422",
}

# Kept preprintid-only deliberately, unlike a year-widened fix: this pattern
# (PUM_SENDS_TO_EO resetting state without a new round, then a real second PM_SENDS_FOR_PROOF_R
# against that same round) is genuinely rare - only these 12 preprintids have ever hit it, across
# the whole ~21000-paper corpus and all years re-imported so far (2024-2026 included). Widening by
# year would exercise this special-case branch on thousands of papers that have never needed it,
# for no evidenced benefit - only widen this one if a *new*, previously-unseen preprintid is found
# to need it during a full-year run, the same way the 12 already here were each found individually.
REQUEST_PROOFS_DEDUP_GALLEY_PROOFING_YEARS: set[int] = set()

REQUEST_PROOFS_DEDUP_GALLEY_PROOFING_ALL: bool = False


def request_proofs_dedup_galley_proofing_applies(preprintid: str) -> bool:
    """Whether Requestproofs' skip-duplicate-GalleyProofing fix should apply to this preprintid."""
    if REQUEST_PROOFS_DEDUP_GALLEY_PROOFING_ALL:
        return True
    if preprintid in REQUEST_PROOFS_DEDUP_GALLEY_PROOFING_PREPRINTIDS:
        return True
    return _preprintid_year_matches(preprintid, REQUEST_PROOFS_DEDUP_GALLEY_PROOFING_YEARS)
