from typing import Iterable, Optional

from core.models import Account, AccountRole
from django.db.models import Q, QuerySet
from journal.models import Journal


def get_available_users_by_role(
    journal: Journal,
    role: str,
    exclude: Iterable,
    filters: Optional[Q] = None,
) -> QuerySet:
    """Get users by role and journal, excluding a list of users and applying filters."""
    users_ids = AccountRole.objects.filter(
        journal=journal,
        role__slug=role,
    ).values_list("user_id", flat=True)
    qs = Account.objects.filter(pk__in=users_ids).exclude(pk__in=exclude)
    if filters:
        qs = qs.filter(filters)
    return qs
