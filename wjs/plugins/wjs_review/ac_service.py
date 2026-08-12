"""
Materialized Attention Conditions service API.

This module implements the materialized AC architecture described in:
  - 260211-SISSA-Analyse-a-different-approach-to-attention-conditions.md
  - 260318-SISSA-Specifications-for-attention-conditions.md
  - 260401-SISSA-Optimize-attention-conditions-DRAFT.md

ACs are pre-computed and stored in the AttentionCondition DB table rather than
computed on the fly. Computation is triggered by:
  - User actions (event-driven): explicit calls from logic classes
  - Daily scheduled job (time-based): rebuild_attention_conditions management command

Design decisions:
  - Single AC displayed per article (highest priority), but model supports multiple.
    See New Issue 1 in 260318-SISSA-Specifications-for-attention-conditions.md.
  - Time-based ACs are decoupled from the reminder system; they use elapsed time
    directly. See New Issue 10 in 260318-SISSA-Specifications-for-attention-conditions.md.
  - Role resolution in the write path uses the Redis cache via
    get_or_refresh_role_cache_entry, which falls back to compute_role_for_article
    on cache miss. Redis is a hard dependency (sessions, etc.), so a restart
    would be catastrophic regardless.
  - The old on-the-fly dispatcher (BaseState.article_requires_attention) has been
    removed; the materialized table is the single source of truth.
  - Invariant: business logic never CREATES time-based ACs (TIME_BASED_AC_CODES)
    directly — the evaluator (nightly rebuild, or an explicit ACStateEvaluator
    call) is their only writer. Business logic resolves them eagerly when an
    event invalidates them, and may re-evaluate via the evaluator.

Future extensions anticipated by this design:
  - New Issue 2: EO-configurable AC parameters (thresholds, messages)
  - New Issue 3: Blocking AC for EO OA settings confirmation (IoP)
  - New Issue 4: AC for author's special copyright/OA request
  - New Issue 5: AC for submission matching EO-defined rules
  - New Issue 7: Manual per-paper custom alerts (source=MANUAL)
  - New Issue 8: Blocking AC for EO manuscript hold before typesetting
  - New Issue 9: AC for arXiv metadata mismatch
  - New Issue 11: AC for probable duplicate submission
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from typesetting.models import GalleyProofing, TypesettingAssignment

from wjs.jcom_profile import constants
from wjs.jcom_profile.utils import get_eo_user

from . import conditions
from .models import AttentionCondition, PastEditorAssignment, WjsEditorAssignment
from .role_cache import get_or_refresh_role_cache_entry

if TYPE_CHECKING:
    from submission.models import Article

Account = get_user_model()

# ---------------------------------------------------------------------------
# AC code constants -- stable functional identifiers.
#
# These are the deduplication keys stored in AttentionCondition.code.
# They must remain stable across releases; changing a code would orphan
# existing rows in the table.
# ---------------------------------------------------------------------------

# -- Event-based (fire immediately when condition is met) --

EDITOR_NOT_SELECTED = "editor_not_selected"
"""EO/director: no editor has been chosen for the article."""

NEEDS_ASSIGNMENT = "needs_assignment"
"""Editor: no valid review assignments exist; review should start/restart."""

REVIEWS_COMPLETED = "reviews_completed_decision_needed"
"""Editor: all active reviews are done; a decision should be made."""

EDITOR_REVIEW_OVERDUE = "editor_review_overdue"
"""Editor: the editor assigned themselves as reviewer and is late."""

INCOMPLETE_SUBMISSION = "incomplete_submission"
"""Author: the submission was left unfinished."""

SUBMISSION_TO_CHECK = "submission_to_check"
"""EO: the submission needs manual checking (PaperMightHaveIssues)."""

MISSING_SOCIAL_MEDIA = "missing_social_media"
"""EO: social-media image or short description is missing."""

MISSING_ENGLISH_CONTENT = "missing_english_content"
"""EO: English title or abstract is missing where required."""

APPEAL_TO_SUBMIT = "appeal_to_submit"
"""Author: an appeal needs to be submitted."""

# -- Time-based (fire after elapsed time) --

EDITOR_IS_LATE = "editor_is_late"
"""EO/director: the editor has taken too long to make a decision."""

NEEDS_ASSIGNMENT_ESCALATED = "needs_assignment_escalated"
"""EO/director: review process not started after extended waiting period."""

REVIEWER_LATE = "reviewer_late"
"""Editor: a reviewer is not answering or is overdue."""

REVIEWER_LATE_ESCALATED = "reviewer_late_escalated"
"""EO/director: a reviewer is not answering or is overdue (escalated)."""

REVIEWER_INVITATION_PENDING = "reviewer_invitation_pending"
"""Reviewer: the invitation still needs to be accepted or declined."""

REVIEWER_REPORT_OVERDUE = "reviewer_report_overdue"
"""Reviewer: the review itself is overdue."""

REVIEWER_INACTIVE = "reviewer_inactive_after_reminders"
"""Editor: reviewers stayed inactive even after extended waiting period."""

AUTHOR_REVISION_LATE = "author_revision_late"
"""Author: a requested revision is overdue."""

AUTHOR_REVISION_LATE_ESCALATED = "author_revision_late_escalated"
"""Editor/EO: the author is late with a revision (escalated view)."""

AUTHOR_METADATA_LATE = "author_metadata_late"
"""Author: a metadata update / technical revision is overdue."""

AUTHOR_METADATA_LATE_ESCALATED = "author_metadata_late_escalated"
"""Editor/EO: the author is late updating metadata (escalated view)."""

APPEAL_LATE = "appeal_late"
"""EO: the author is late submitting the appeal."""

TYPESETTER_LATE = "typesetter_late"
"""Typesetter/EO: the typesetting assignment is overdue."""

AUTHOR_PROOFING_LATE = "author_proofing_late"
"""EO: the author is late proofreading."""

OLD_UNREAD_MESSAGES = "old_unread_messages"
"""Non-EO roles: there are messages left unread for a long time."""

OLD_UNREAD_MESSAGES_FOR_EO = "old_unread_messages_for_eo"
"""EO: there are messages left unread for a long time."""

# -- Fallback --

HAS_UNREAD_MESSAGE = "has_unread_message"
"""Any role: the user has unread messages on the article."""

# -- Classification: which ACs are time-based vs event-based --

TIME_BASED_AC_CODES: set[str] = {
    EDITOR_IS_LATE,
    NEEDS_ASSIGNMENT_ESCALATED,
    REVIEWER_LATE,
    REVIEWER_LATE_ESCALATED,
    REVIEWER_INVITATION_PENDING,
    REVIEWER_REPORT_OVERDUE,
    REVIEWER_INACTIVE,
    AUTHOR_REVISION_LATE,
    AUTHOR_REVISION_LATE_ESCALATED,
    AUTHOR_METADATA_LATE,
    AUTHOR_METADATA_LATE_ESCALATED,
    APPEAL_LATE,
    TYPESETTER_LATE,
    AUTHOR_PROOFING_LATE,
    OLD_UNREAD_MESSAGES,
    OLD_UNREAD_MESSAGES_FOR_EO,
}
"""AC codes that depend on elapsed time and should be re-evaluated daily.

Event-based codes (e.g., EDITOR_NOT_SELECTED, REVIEWS_COMPLETED) fire
immediately on user actions and are NOT in this set. They are only
evaluated by the event-driven leg (explicit calls from logic classes).
"""


# ============================================================================
# Core API
# ============================================================================


def upsert_ac(
    article: Article,
    user: Account,
    code: str,
    message: str,
    *,
    priority: int = 99,
    source: str = "automatic",
) -> AttentionCondition:
    """Create or update an attention condition for a single (article, user, code) tuple.

    If a row with the same (article, user, code) already exists, its message,
    priority, and status are updated. Otherwise a new row is created.

    This is the primary write primitive. All other write functions delegate to this.

    Priority is the display order: lower number = higher priority (checked first
    in the original flow). It must be set explicitly by each call site to match
    the position in the original article_requires_{role}_attention check sequence.

    Args:
        article: The article the AC refers to.
        user: The user who should see the AC.
        code: Stable functional code (one of the module-level constants).
        message: Human-readable description of the condition.
        priority: Display priority (lower = higher priority). Defaults to 99 (lowest).
        source: Source type ("automatic" or "manual").

    Returns:
        The created or updated AttentionCondition instance.
    """
    ac, _created = AttentionCondition.objects.update_or_create(
        article=article,
        user=user,
        code=code,
        defaults={
            "message": message,
            "priority": priority,
            "source": source,
            "status": AttentionCondition.Status.ACTIVE,
        },
    )
    return ac


def resolve_ac(article: Article, user: Account, code: str) -> None:
    """Mark a specific attention condition as resolved.

    The row is not deleted; its status is set to RESOLVED so that history
    is preserved for auditing and reporting.

    Args:
        article: The article the AC refers to.
        user: The user the AC was shown to.
        code: The AC code to resolve.
    """
    AttentionCondition.objects.filter(
        article=article,
        user=user,
        code=code,
    ).update(status=AttentionCondition.Status.RESOLVED)


def resolve_all_for_article(article: Article, codes: Iterable[str] | None = None) -> None:
    """Resolve all ACs for all users on an article.

    If *codes* is provided, only those codes are resolved. If ``None`` (the
    default), all active ACs for the article are resolved regardless of code.

    Used when a state transition invalidates a whole class of conditions
    (e.g., when an editor is assigned, all EDITOR_NOT_SELECTED ACs are resolved)
    or when an article leaves the workflow entirely (e.g., withdrawal).

    Args:
        article: The article whose ACs should be resolved.
        codes: Optional iterable of AC code strings to resolve. If ``None``,
               all active ACs for the article are resolved.
    """
    qs = AttentionCondition.objects.filter(article=article)
    if codes is not None:
        code_list = list(codes)
        if not code_list:
            return
        qs = qs.filter(code__in=code_list)
    qs.update(status=AttentionCondition.Status.RESOLVED)


def resolve_all_for_user_on_article(article: Article, user: Account) -> None:
    """Resolve all active ACs for a specific user on an article.

    Used when a user action clears all their pending conditions
    (e.g., author submits revision, clearing all their late ACs).

    Args:
        article: The article whose ACs should be resolved.
        user: The user whose ACs should be resolved.
    """
    AttentionCondition.objects.filter(
        article=article,
        user=user,
        status=AttentionCondition.Status.ACTIVE,
    ).update(status=AttentionCondition.Status.RESOLVED)


# ============================================================================
# Role-based bulk operations
# ============================================================================

# ---------------------------------------------------------------------------
# Role resolution for the write path.
#
# Uses the Redis cache via get_or_refresh_role_cache_entry (see role_cache.py),
# which falls back to compute_role_for_article on cache miss. The stale-data-
# on-restart scenario described in 260401-SISSA-Optimize-attention-conditions-
# DRAFT.md was a false alarm: Redis is a hard dependency (sessions, etc.), so
# a restart would be catastrophic regardless.
#
# The Redis cache is also used for the real-time display path via
# role_for_article() in communication_utils.py.
# ---------------------------------------------------------------------------


def _get_users_for_role(article: Article, role: str) -> list[Account]:
    """Resolve the set of users who currently hold a given role on an article.

    Uses the Redis cache via get_or_refresh_role_cache_entry, which falls back
    to compute_role_for_article on cache miss.

    Args:
        article: The article to check.
        role: Role slug (e.g., "eo", "director", "editor", "reviewer", "author",
              "typesetter").

    Returns:
        List of Account instances that hold the given role on the article.
    """
    candidates: list[Account] = []

    if role.upper() == constants.EO_GROUP:
        # EO is a journal-level role (group membership), not article-specific.
        # We need to return:
        # 1. The EO system user for this journal (created via get_eo_user, has
        #    no AccountRole, only the EO group)
        # 2. Human EO users who have an AccountRole for this journal's
        #    (any role) AND are in the EO group.
        eo_system_user = get_eo_user(article)
        eo_humans = Account.objects.filter(
            groups__name=constants.EO_GROUP,
            accountrole__journal=article.journal,
        ).distinct()
        return list(set([eo_system_user] + list(eo_humans)))

    elif role == constants.DIRECTOR_ROLE:
        candidates = list(
            Account.objects.filter(
                accountrole__journal=article.journal,
                accountrole__role__slug=constants.DIRECTOR_ROLE,
            ).distinct()
        )

    elif role == constants.EDITOR_ROLE:
        try:
            editor_assignment = WjsEditorAssignment.objects.get_current(article)
        except WjsEditorAssignment.DoesNotExist:
            editor_assignment = None
        if editor_assignment:
            candidates = [editor_assignment.editor]

    elif role == constants.REVIEWER_ROLE:
        review_round = article.current_review_round_object()
        candidates = list(
            Account.objects.filter(
                reviewer__article=article,
                reviewer__review_round=review_round,
            ).distinct()
        )

    elif role == constants.AUTHOR_ROLE:
        candidates = [article.correspondence_author] if article.correspondence_author else []
        coauthors = list(article.author_accounts.exclude(pk=article.correspondence_author_id))
        candidates.extend(coauthors)

    elif role == constants.TYPESETTER_ROLE:
        candidates = list(
            Account.objects.filter(
                typesettingassignment__round__article=article,
            ).distinct()
        )

    else:
        return []

    # Filter: only keep users who actually hold the role according to
    # the Redis cache (falls back to compute_role_for_article on miss).
    result = []
    for user in candidates:
        computed = get_or_refresh_role_cache_entry(article=article, user=user)
        # Normalize: compute_role_for_article may return "current-reviewer"
        # which maps to REVIEWER_ROLE in role_for_article.
        if role == constants.REVIEWER_ROLE and computed in {
            constants.REVIEWER_ROLE,
            "current-reviewer",
        }:
            result.append(user)
        elif computed == role:
            result.append(user)

    return result


def upsert_for_role(
    article: Article,
    role: str,
    code: str,
    message: str,
    *,
    priority: int = 99,
    source: str = "automatic",
) -> list[AttentionCondition]:
    """Create or update an AC for all users holding a given role on an article.

    Resolves the set of users for the role via _get_users_for_role, then
    calls upsert_ac for each.

    Args:
        article: The article the AC refers to.
        role: Role slug (e.g., "eo", "director", "editor").
        code: Stable functional code.
        message: Human-readable description.
        priority: Display priority.
        source: Source type.

    Returns:
        List of created/updated AttentionCondition instances.
    """
    users = _get_users_for_role(article, role)
    results = []
    for user in users:
        ac = upsert_ac(
            article=article,
            user=user,
            code=code,
            message=message,
            priority=priority,
            source=source,
        )
        results.append(ac)
    return results


def resolve_for_role(article: Article, role: str, code: str) -> None:
    """Resolve an AC for all users holding a given role on an article.

    Args:
        article: The article the AC refers to.
        role: Role slug.
        code: The AC code to resolve.
    """
    users = _get_users_for_role(article, role)
    for user in users:
        resolve_ac(article=article, user=user, code=code)


def resolve_for_role_batch(article: Article, role: str, codes: list[str]) -> None:
    """Resolve multiple AC codes for all users of a role in a single pass.

    Resolves the user set once, then issues a single bulk UPDATE for all
    codes. This avoids redundant ``_get_users_for_role`` calls when a logic
    class needs to clear several ACs for the same role.

    Args:
        article: The article the ACs refer to.
        role: Role slug (e.g., ``"editor"``, ``"eo"``).
        codes: List of AC code constants to resolve.
    """
    users = _get_users_for_role(article, role)
    if not users:
        return
    AttentionCondition.objects.filter(
        article=article,
        user__in=users,
        code__in=codes,
        status=AttentionCondition.Status.ACTIVE,
    ).update(status=AttentionCondition.Status.RESOLVED)


def resolve_ac_batch(article: Article, user: Account, codes: list[str]) -> None:
    """Resolve multiple AC codes for a single user in a single query.

    Args:
        article: The article the ACs refer to.
        user: The user whose ACs should be resolved.
        codes: List of AC code constants to resolve.
    """
    AttentionCondition.objects.filter(
        article=article,
        user=user,
        code__in=codes,
        status=AttentionCondition.Status.ACTIVE,
    ).update(status=AttentionCondition.Status.RESOLVED)


# ============================================================================
# ACStateEvaluator -- maps (state, role) to ordered AC code lists
# ============================================================================

# ---------------------------------------------------------------------------
# STATE_ROLE_AC_MAP: maps each (state, role) pair to an ordered list of AC
# codes. The list order defines the display priority: index 0 = priority 1
# (highest), index 1 = priority 2, etc.
#
# This is the single source of truth for both:
#   - Scope: which ACs are relevant for a given (state, role)
#   - Priority: the display order among those ACs
#
# The order replicates the original check sequence in the now-removed
# article_requires_{role}_attention methods on each state class.
#
# _STATE_AC_MAP (set of codes per state, for the daily task) is derived
# automatically from STATE_ROLE_AC_MAP at module load time and exposed
# as ACStateEvaluator.STATE_AC_MAP.
#
# Derived from the AC catalog in
# 260318-SISSA-Specifications-for-attention-conditions.md, section
# "AC catalog grouped by state".
# ---------------------------------------------------------------------------

STATE_ROLE_AC_MAP: dict[tuple[str, str], list[str]] = {
    # -- EditorToBeSelected --
    ("EditorToBeSelected", "eo"): [EDITOR_NOT_SELECTED],
    ("EditorToBeSelected", "director"): [EDITOR_NOT_SELECTED],
    # -- EditorSelected --
    ("EditorSelected", "editor"): [
        REVIEWER_LATE,
        NEEDS_ASSIGNMENT,
        REVIEWS_COMPLETED,
        EDITOR_REVIEW_OVERDUE,
        REVIEWER_INACTIVE,
    ],
    ("EditorSelected", "eo"): [
        NEEDS_ASSIGNMENT_ESCALATED,
        REVIEWER_LATE_ESCALATED,
        EDITOR_IS_LATE,
        OLD_UNREAD_MESSAGES_FOR_EO,
    ],
    ("EditorSelected", "director"): [NEEDS_ASSIGNMENT_ESCALATED, REVIEWER_LATE_ESCALATED, EDITOR_IS_LATE],
    ("EditorSelected", "reviewer"): [REVIEWER_INVITATION_PENDING, REVIEWER_REPORT_OVERDUE],
    # -- IncompleteSubmission --
    ("IncompleteSubmission", "author"): [INCOMPLETE_SUBMISSION],
    # -- ToBeRevised --
    ("ToBeRevised", "author"): [AUTHOR_REVISION_LATE, AUTHOR_METADATA_LATE],
    ("ToBeRevised", "editor"): [AUTHOR_REVISION_LATE_ESCALATED, AUTHOR_METADATA_LATE_ESCALATED],
    ("ToBeRevised", "eo"): [AUTHOR_REVISION_LATE_ESCALATED, AUTHOR_METADATA_LATE_ESCALATED],
    ("ToBeRevised", "reviewer"): [REVIEWER_REPORT_OVERDUE],
    # -- UnderAppeal --
    ("UnderAppeal", "author"): [APPEAL_TO_SUBMIT],
    ("UnderAppeal", "eo"): [APPEAL_LATE],
    # -- PaperMightHaveIssues --
    ("PaperMightHaveIssues", "eo"): [SUBMISSION_TO_CHECK],
    # -- TypesetterSelected --
    ("TypesetterSelected", "typesetter"): [TYPESETTER_LATE],
    ("TypesetterSelected", "eo"): [TYPESETTER_LATE, OLD_UNREAD_MESSAGES_FOR_EO],
    # -- Proofreading --
    ("Proofreading", "eo"): [AUTHOR_PROOFING_LATE, OLD_UNREAD_MESSAGES_FOR_EO],
    # -- ReadyForTypesetter --
    ("ReadyForTypesetter", "eo"): [OLD_UNREAD_MESSAGES_FOR_EO],
    # -- ReadyForPublication --
    ("ReadyForPublication", "eo"): [MISSING_SOCIAL_MEDIA, MISSING_ENGLISH_CONTENT],
}

# Derived: set of all AC codes for each state (used by the daily task to
# iterate codes without regard to role).
_STATE_AC_MAP: dict[str, set[str]] = {}
for (_state, _role), _codes in STATE_ROLE_AC_MAP.items():
    _STATE_AC_MAP.setdefault(_state, set()).update(_codes)

# Derived: maps (state, code) → list of roles that should see this AC.
# Built by inverting STATE_ROLE_AC_MAP at module load time.
# Used by _evaluate_code to pass the correct role list to each evaluator.
STATE_AC_ROLES_MAP: dict[tuple[str, str], list[str]] = defaultdict(list)
for (_state, _role), _codes in STATE_ROLE_AC_MAP.items():
    for _code in _codes:
        STATE_AC_ROLES_MAP[(_state, _code)].append(_role)


def get_ac_priority(state: str, role: str, code: str) -> int:
    """Return the display priority for an AC code in a given (state, role).

    Priority is 1-based: index 0 in the list = priority 1 (highest).
    Returns 99 (lowest) if the (state, role, code) combination is not found.

    Args:
        state: ArticleWorkflow.ReviewStates value.
        role: Role slug (e.g., "eo", "editor").
        code: AC code constant.

    Returns:
        Priority integer (lower = higher priority).
    """
    codes = STATE_ROLE_AC_MAP.get((state, role))
    if codes is None:
        return 99
    try:
        return (codes.index(code) + 1) * 10
    except ValueError:
        return 99


class ACStateEvaluator:
    """Evaluates all relevant ACs for an article in a given state.

    Used by the daily recomputation task and the one-shot population command.
    """

    STATE_AC_MAP = _STATE_AC_MAP

    def __init__(self, state: str, article: Article) -> None:
        """Initialize the evaluator for a specific state and article.

        Args:
            state: The ArticleWorkflow.ReviewStates value.
            article: The article to evaluate ACs for.
        """
        self.state = state
        self.article = article
        self.codes = self.STATE_AC_MAP.get(state, set())

    def evaluate_all(self) -> None:
        """Evaluate all ACs relevant to the current state.

        For each AC code in the state's set, runs the corresponding condition
        function and calls upsert_ac or resolve_ac accordingly.

        Time-based ACs are always re-evaluated (they may have become true/false
        with the passage of time). Event-based ACs are also re-evaluated to
        ensure consistency.

        The fallback HAS_UNREAD_MESSAGE is always evaluated regardless of
        whether the state has mapped AC codes, so that unread-message ACs
        are resolved even in states with no other ACs.
        """
        if self.codes:
            for code in self.codes:
                self._evaluate_code(code)

        # Evaluate the fallback HAS_UNREAD_MESSAGE for all roles in all states.
        # This fixes New Issue 6 (unread messages not triggering ACs in
        # post-review states). See:
        # 260318-SISSA-Specifications-for-attention-conditions.md, New Issue 6.
        self._evaluate_unread_messages()

        # Resolve stale time-based ACs that belong to a previous state.
        self._resolve_stale_time_based_acs()

    def evaluate_time_based(self) -> None:
        """Evaluate only time-based ACs for the daily rebuild job.

        Unlike :meth:`evaluate_all`, this skips event-based ACs (e.g.,
        EDITOR_NOT_SELECTED, REVIEWS_COMPLETED) that fire immediately on
        user actions and don't need periodic re-evaluation.

        The fallback HAS_UNREAD_MESSAGE is always evaluated regardless,
        since it is time-sensitive (messages age).
        """
        if self.codes:
            for code in self.codes:
                if code in TIME_BASED_AC_CODES:
                    self._evaluate_code(code)

        # HAS_UNREAD_MESSAGE is always time-sensitive.
        self._evaluate_unread_messages()

        # Resolve stale time-based ACs that belong to a previous state.
        self._resolve_stale_time_based_acs()

    def _resolve_stale_time_based_acs(self) -> None:
        """Resolve time-based ACs that are no longer relevant to the current state.

        When an article transitions to a new state, time-based ACs created in
        the previous state are not in ``self.codes`` and therefore never
        re-evaluated or resolved.  They persist as "stale" rows with outdated
        messages (e.g., "Proofing is late by 6 days" that never updates).

        This method computes the set of time-based codes that are **not** in
        the current state's code set and resolves them in a single bulk update.
        """
        stale_codes = TIME_BASED_AC_CODES - self.codes
        if stale_codes:
            resolve_all_for_article(self.article, codes=stale_codes)

    def _evaluate_code(self, code: str) -> None:
        """Evaluate a single AC code and update the materialized table.

        Uses dynamic dispatch: looks up ``_evaluate_{code}`` on self.
        If no matching method exists, the code is silently skipped.

        The roles that should see this AC are derived from
        STATE_AC_ROLES_MAP and passed to the evaluator method.
        """
        if _evaluate := getattr(self, f"_evaluate_{code}", None):
            _evaluate(STATE_AC_ROLES_MAP[(self.state, code)])

    # -- Helpers that auto-derive priority from STATE_ROLE_AC_MAP --

    def _upsert_for_role(
        self, role: str, code: str, message: str, *, source: str = "automatic"
    ) -> list[AttentionCondition]:
        """upsert_for_role with priority derived from STATE_ROLE_AC_MAP."""
        priority = get_ac_priority(self.state, role, code)
        return upsert_for_role(self.article, role, code, message, priority=priority, source=source)

    def _resolve_for_role(self, role: str, code: str) -> None:
        """resolve_for_role wrapper (no priority needed for resolution)."""
        resolve_for_role(self.article, role, code)

    def _upsert_ac(
        self, user: Account, code: str, message: str, *, role: str, source: str = "automatic"
    ) -> AttentionCondition:
        """upsert_ac with priority derived from STATE_ROLE_AC_MAP."""
        priority = get_ac_priority(self.state, role, code)
        return upsert_ac(self.article, user, code, message, priority=priority, source=source)

    # -- Individual AC evaluators --

    def _evaluate_editor_not_selected(self, roles: list[str]) -> None:
        """EO/director: no editor chosen yet.

        Note: uses different messages for eo vs director, so cannot
        use a simple loop over roles.
        """
        article = self.article
        if latest := PastEditorAssignment.objects.filter(article=article).order_by("date_unassigned").last():
            waiting_days = (timezone.now() - latest.date_unassigned).days
        elif article.date_submitted:
            waiting_days = (timezone.now() - article.date_submitted).days
        else:
            # Article was never submitted (e.g., incomplete submission left in
            # EditorToBeSelected state). Use date_started as fallback.
            waiting_days = (timezone.now() - article.date_started).days

        message = f"Editor has not been selected for {waiting_days} days"
        self._upsert_for_role("eo", EDITOR_NOT_SELECTED, message)
        self._upsert_for_role("director", EDITOR_NOT_SELECTED, "Editor should be selected")

    def _evaluate_needs_assignment(self, roles: list[str]) -> None:
        """Editor: review process should start/restart."""
        article = self.article
        msg = conditions.needs_assignment(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, NEEDS_ASSIGNMENT, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, NEEDS_ASSIGNMENT)

    def _evaluate_reviews_completed_decision_needed(self, roles: list[str]) -> None:
        """Editor: all reviews done, decision needed."""
        article = self.article
        msg = conditions.all_assignments_completed(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, REVIEWS_COMPLETED, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, REVIEWS_COMPLETED)

    def _evaluate_editor_review_overdue(self, roles: list[str]) -> None:
        """Editor: editor-as-reviewer is late."""
        article = self.article
        msg = conditions.editor_as_reviewer_is_late(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, EDITOR_REVIEW_OVERDUE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, EDITOR_REVIEW_OVERDUE)

    def _evaluate_editor_is_late(self, roles: list[str]) -> None:
        """EO/director: editor late with decision."""
        article = self.article
        msg = conditions.editor_is_late(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, EDITOR_IS_LATE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, EDITOR_IS_LATE)

    def _evaluate_needs_assignment_escalated(self, roles: list[str]) -> None:
        """EO/director: review not started after extended period."""
        article = self.article
        msg = conditions.needs_assignment_all_editorreminders_sent(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, NEEDS_ASSIGNMENT_ESCALATED, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, NEEDS_ASSIGNMENT_ESCALATED)

    def _evaluate_reviewer_late(self, roles: list[str]) -> None:
        """Editor: reviewer late (accept/decline or write review)."""
        article = self.article
        msg = conditions.reviewer_is_late(article, for_editor=True)
        if msg:
            for role in roles:
                self._upsert_for_role(role, REVIEWER_LATE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, REVIEWER_LATE)

    def _evaluate_reviewer_late_escalated(self, roles: list[str]) -> None:
        """EO/director: reviewer late (escalated)."""
        article = self.article
        msg = conditions.reviewer_is_late(article, for_editor=False)
        if msg:
            for role in roles:
                self._upsert_for_role(role, REVIEWER_LATE_ESCALATED, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, REVIEWER_LATE_ESCALATED)

    def _evaluate_reviewer_invitation_pending(self, roles: list[str]) -> None:
        """Reviewer: the invitation still needs to be accepted or declined."""
        article = self.article
        msg = conditions.reviewer_acceptdecline_is_late(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, REVIEWER_INVITATION_PENDING, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, REVIEWER_INVITATION_PENDING)

    def _evaluate_reviewer_report_overdue(self, roles: list[str]) -> None:
        """Reviewer: the review itself is overdue."""
        article = self.article
        msg = conditions.reviewer_report_is_late(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, REVIEWER_REPORT_OVERDUE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, REVIEWER_REPORT_OVERDUE)

    def _evaluate_reviewer_inactive_after_reminders(self, roles: list[str]) -> None:
        """Editor: reviewers inactive after extended period."""
        article = self.article
        msg = conditions.any_reviewer_is_late_after_reminder(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, REVIEWER_INACTIVE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, REVIEWER_INACTIVE)

    def _evaluate_author_revision_late(self, roles: list[str]) -> None:
        """Author: revision overdue."""
        article = self.article
        msg = conditions.author_revision_is_late(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, AUTHOR_REVISION_LATE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, AUTHOR_REVISION_LATE)

    def _evaluate_author_revision_late_escalated(self, roles: list[str]) -> None:
        """Editor/EO: author revision late (escalated).

        Note: uses different late_after_days thresholds per role, so
        cannot use a simple loop over roles.
        """
        article = self.article
        msg_editor = conditions.author_revision_is_late_all_reminders_sent(article, late_after_days=1)
        if msg_editor:
            self._upsert_for_role("editor", AUTHOR_REVISION_LATE_ESCALATED, msg_editor)
        else:
            self._resolve_for_role("editor", AUTHOR_REVISION_LATE_ESCALATED)

        msg_eo = conditions.author_revision_is_late_all_reminders_sent(article, late_after_days=2)
        if msg_eo:
            self._upsert_for_role("eo", AUTHOR_REVISION_LATE_ESCALATED, msg_eo)
        else:
            self._resolve_for_role("eo", AUTHOR_REVISION_LATE_ESCALATED)

    def _evaluate_author_metadata_late(self, roles: list[str]) -> None:
        """Author: metadata update overdue."""
        article = self.article
        msg = conditions.author_technicalrevision_is_late(article)
        if msg:
            for role in roles:
                self._upsert_for_role(role, AUTHOR_METADATA_LATE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, AUTHOR_METADATA_LATE)

    def _evaluate_author_metadata_late_escalated(self, roles: list[str]) -> None:
        """Editor/EO: author metadata late (escalated).

        Note: uses different late_after_days thresholds per role, so
        cannot use a simple loop over roles.
        """
        article = self.article
        msg_editor = conditions.author_technicalrevision_is_late_all_reminders_sent(article, late_after_days=1)
        if msg_editor:
            self._upsert_for_role("editor", AUTHOR_METADATA_LATE_ESCALATED, msg_editor)
        else:
            self._resolve_for_role("editor", AUTHOR_METADATA_LATE_ESCALATED)

        msg_eo = conditions.author_technicalrevision_is_late_all_reminders_sent(article, late_after_days=2)
        if msg_eo:
            self._upsert_for_role("eo", AUTHOR_METADATA_LATE_ESCALATED, msg_eo)
        else:
            self._resolve_for_role("eo", AUTHOR_METADATA_LATE_ESCALATED)

    def _evaluate_appeal_to_submit(self, roles: list[str]) -> None:
        """Author: appeal to submit."""
        for role in roles:
            self._upsert_for_role(role, APPEAL_TO_SUBMIT, "Appeal to submit")

    def _evaluate_appeal_late(self, roles: list[str]) -> None:
        """EO: appeal submission late."""
        article = self.article
        msg = conditions.author_appealsubmission_is_late(article)
        if msg:
            msg = msg + ". Withdraw?"
            for role in roles:
                self._upsert_for_role(role, APPEAL_LATE, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, APPEAL_LATE)

    def _evaluate_typesetter_late(self, roles: list[str]) -> None:
        """Typesetter/EO: typesetting overdue.

        Note: uses per-user _upsert_ac for typesetter vs per-role
        _upsert_for_role for eo, so cannot use a simple loop over roles.
        """
        article = self.article
        for assignment in TypesettingAssignment.objects.filter(round__article=article):
            msg = conditions.is_typesetter_late(assignment)
            if msg:
                # The typesetter's personal row is written LAST: if the
                # typesetter is also in the EO group, both writes hit the same
                # (article, user, code) row, and the personal wording must win.
                msg_typesetter = conditions.is_typesetter_late(assignment, for_typesetter=True)
                self._upsert_for_role("eo", TYPESETTER_LATE, msg)
                self._upsert_ac(assignment.typesetter, TYPESETTER_LATE, msg_typesetter, role="typesetter")
            else:
                resolve_ac(article, assignment.typesetter, TYPESETTER_LATE)
                self._resolve_for_role("eo", TYPESETTER_LATE)

    def _evaluate_author_proofing_late(self, roles: list[str]) -> None:
        """EO: author proofing late."""
        article = self.article
        for proofing in GalleyProofing.objects.filter(round__article=article):
            msg = conditions.is_author_proofing_late(proofing)
            if msg:
                for role in roles:
                    self._upsert_for_role(role, AUTHOR_PROOFING_LATE, msg)
            else:
                for role in roles:
                    self._resolve_for_role(role, AUTHOR_PROOFING_LATE)

    def _evaluate_old_unread_messages(self, roles: list[str]) -> None:
        """Non-EO roles: old unread messages (short threshold).

        Uses ``WJS_UNREAD_MESSAGES_LATE_AFTER`` days as the threshold.
        """
        article = self.article
        msg = conditions.article_has_old_unread_message(
            article, late_after_days=settings.WJS_UNREAD_MESSAGES_LATE_AFTER
        )
        if msg:
            for role in roles:
                self._upsert_for_role(role, OLD_UNREAD_MESSAGES, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, OLD_UNREAD_MESSAGES)

    def _evaluate_old_unread_messages_for_eo(self, roles: list[str]) -> None:
        """EO: old unread messages (EO threshold, effectively never).

        Uses ``WJS_UNREAD_MESSAGES_LATE_AFTER_FOR_EO`` days as the threshold.
        """
        article = self.article
        msg = conditions.article_has_old_unread_message(
            article, late_after_days=settings.WJS_UNREAD_MESSAGES_LATE_AFTER_FOR_EO
        )
        if msg:
            for role in roles:
                self._upsert_for_role(role, OLD_UNREAD_MESSAGES_FOR_EO, msg)
        else:
            for role in roles:
                self._resolve_for_role(role, OLD_UNREAD_MESSAGES_FOR_EO)

    def _evaluate_incomplete_submission(self, roles: list[str]) -> None:
        """Author: incomplete submission."""
        for role in roles:
            self._upsert_for_role(role, INCOMPLETE_SUBMISSION, "Partial submission to be completed or withdrawn")

    def _evaluate_submission_to_check(self, roles: list[str]) -> None:
        """EO: submission needs manual checking."""
        for role in roles:
            self._upsert_for_role(role, SUBMISSION_TO_CHECK, "Submission to be checked")

    def _evaluate_missing_social_media(self, roles: list[str]) -> None:
        """EO: social-media material missing."""
        article = self.article
        workflow = article.articleworkflow
        if (
            conditions.journal_requires_social_media_files(article.journal)
            and conditions.article_is_published_piecemeal(workflow)
            and (not article.meta_image or not workflow.social_media_short_description)
        ):
            for role in roles:
                self._upsert_for_role(
                    role, MISSING_SOCIAL_MEDIA, "Missing image and/or short description for social media"
                )
            return
        for role in roles:
            self._resolve_for_role(role, MISSING_SOCIAL_MEDIA)

    def _evaluate_missing_english_content(self, roles: list[str]) -> None:
        """EO: English title/abstract missing."""
        article = self.article
        workflow = article.articleworkflow
        if conditions.needs_article_data_for_social_media_and_translations(workflow, None) and (
            not article.title_en or not article.abstract_en
        ):
            for role in roles:
                self._upsert_for_role(
                    role, MISSING_ENGLISH_CONTENT, "Missing English translation of title or abstract"
                )
            return
        for role in roles:
            self._resolve_for_role(role, MISSING_ENGLISH_CONTENT)

    def _evaluate_unread_messages(self) -> None:
        """Evaluate HAS_UNREAD_MESSAGE fallback for all roles in all states.

        This fixes New Issue 6: previously, unread messages would not create
        ACs for roles that had no explicit attention function in the current
        state. Now we evaluate this for all users who have any role on the
        article, regardless of state.

        Priority 99: this is the fallback AC, always lowest priority so that
        any specific AC takes precedence in the display.

        See: 260318-SISSA-Specifications-for-attention-conditions.md, New Issue 6.
        """
        article = self.article
        all_users: set[Account] = set()
        for role in ("eo", "director", "editor", "reviewer", "author", "typesetter"):
            try:
                users = _get_users_for_role(article, role)
                all_users.update(users)
            except Exception:
                pass

        for user in all_users:
            msg = conditions.has_unread_message(article, recipient=user)
            if msg:
                upsert_ac(article, user, HAS_UNREAD_MESSAGE, msg, priority=99)
            else:
                resolve_ac(article, user, HAS_UNREAD_MESSAGE)
