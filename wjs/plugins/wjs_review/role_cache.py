"""Utilities to compute and refresh article role cache entries."""

from __future__ import annotations

from typing import Iterable

from core.models import AccountRole
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache as django_cache
from django.db.models import Q
from plugins.typesetting.models import TypesettingAssignment
from review import models as review_models
from submission.models import Article

from wjs.jcom_profile import constants

from .models import PastEditorAssignment, WjsEditorAssignment

Account = get_user_model()
ROLE_CACHE_TTL = getattr(settings, "ROLE_FOR_ARTICLE_CACHE_TTL", 1_296_000)
ROLE_CACHE_VER = getattr(settings, "ROLE_FOR_ARTICLE_CACHE_VER", 1)

# Special case to be used only inside role_for_article
CURRENT_REVIEWER_ROLE = f"current-{constants.REVIEWER_ROLE}"


def _role_cache_key(user_id: int, article_id: int | str) -> str:
    return f"wjs_review:role_for_article:v{ROLE_CACHE_VER}:{user_id}:{article_id}"


def compute_role_for_article(article: Article, user: Account) -> str:
    """Compute role_for_article for the given article and user."""

    if WjsEditorAssignment.objects.filter(editor=user, article=article).exists():
        return constants.EDITOR_ROLE

    if PastEditorAssignment.objects.filter(editor=user, article=article).exists():
        return f"past {constants.EDITOR_ROLE}"

    if review_models.ReviewAssignment.objects.filter(reviewer=user, article=article).exists():
        current_round = article.current_review_round_object()
        pending_reviews = Q(
            reviewer=user,
            article=article,
            review_round=current_round,
            is_complete=False,
            date_declined__isnull=True,
        )
        delivered_reviews = Q(
            reviewer=user,
            article=article,
            review_round=current_round,
            date_complete__isnull=False,
            date_accepted__isnull=False,
            is_complete=True,
        ) & ~Q(
            decision="withdrawn",
        )
        if review_models.ReviewAssignment.objects.filter(pending_reviews | delivered_reviews).exists():
            return CURRENT_REVIEWER_ROLE
        return constants.REVIEWER_ROLE

    if user == article.correspondence_author:
        return constants.AUTHOR_ROLE

    if article.authors.filter(pk=user.pk).exists():
        return constants.COAUTHOR_ROLE

    if TypesettingAssignment.objects.filter(round__article=article, typesetter=user).exists():
        return constants.TYPESETTER_ROLE

    if user.check_role(article.journal, role=constants.DIRECTOR_ROLE, staff_override=False):
        return constants.DIRECTOR_ROLE

    return ""


def invalidate_role_cache_for_user(*, user_id: int) -> None:
    """Delete all cached role_for_article rows for one user."""
    if not user_id:
        return
    django_cache.delete_pattern(_role_cache_key(user_id, "*"), itersize=65_536)


def refresh_role_cache_for_article_user(*, article: Article, user: Account) -> str:
    """Refresh (or create) the cache entry for one article-user pair."""
    role = compute_role_for_article(article=article, user=user)
    django_cache.set(_role_cache_key(user.pk, article.pk), role, ROLE_CACHE_TTL)
    return role


def refresh_role_cache_for_article_users(*, article: Article, users: Iterable[Account]) -> None:
    """Refresh cache rows for one article and a set of user instances."""
    for user in users:
        if getattr(user, "pk", None):
            refresh_role_cache_for_article_user(article=article, user=user)


def refresh_role_cache_for_article_user_ids(*, article_id: int, user_ids: Iterable[int]) -> None:
    """Refresh cache rows for one article and a set of users."""
    user_id_set = {user_id for user_id in user_ids if user_id}
    if not user_id_set:
        return
    article = Article.objects.get(pk=article_id)
    users = Account.objects.filter(pk__in=user_id_set)
    for user in users:
        refresh_role_cache_for_article_user(article=article, user=user)


def get_or_refresh_role_cache_entry(*, article: Article, user: Account) -> str:
    """Get cache entry for (article, user), refreshing it lazily on first miss."""
    key = _role_cache_key(user.pk, article.pk)
    role = django_cache.get(key)
    if role is not None:
        return role
    return refresh_role_cache_for_article_user(article=article, user=user)


def get_director_user_ids_for_journal(*, journal_id: int) -> set[int]:
    """Get user ids that currently have director role for the given journal."""
    return set(
        AccountRole.objects.filter(
            journal_id=journal_id,
            role__slug=constants.DIRECTOR_ROLE,
        ).values_list("user_id", flat=True)
    )
