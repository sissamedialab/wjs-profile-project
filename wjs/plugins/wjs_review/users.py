from typing import Iterable, Optional

from core.models import AccountRole
from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet
from django.http import QueryDict
from journal.models import Journal

from .models import ArticleWorkflow

Account = get_user_model()


def get_available_users_by_role(
    journal: Journal,
    role: str,
    exclude: Optional[Iterable] = None,
    filters: Optional[Q] = None,
) -> QuerySet:
    """Get users by role and journal, excluding a list of users and applying filters."""
    users_ids = AccountRole.objects.filter(
        journal=journal,
        role__slug=role,
    ).values_list("user_id", flat=True)
    qs = Account.objects.filter(pk__in=users_ids)
    if exclude:
        qs = qs.exclude(pk__in=exclude)
    if filters:
        qs = qs.filter(filters)
    return qs


def get_reviewers_choices(self, workflow: ArticleWorkflow) -> QuerySet[Account]:
    """Get valid reviewers choices."""
    return self.filter(is_active=True).exclude_authors(workflow)


def exclude_authors(self, workflow: ArticleWorkflow) -> QuerySet[Account]:
    """Exclude articles authors from queryset."""
    return self.exclude(
        pk__in=workflow.article_authors.values_list("pk", flat=True),
    )


def filter_reviewers(self, workflow: ArticleWorkflow, search_data: QueryDict) -> QuerySet[Account]:
    """
    Filter reviewers by input data.

    Text filter currently searches in first name, last name, email and keywords of the articles the user has reviewed.
    """
    q_filters = None
    if search_data.get("search"):
        search_text = search_data.get("search").lower()
        q_filters = Q(
            Q(first_name__icontains=search_text)
            | Q(last_name__icontains=search_text)
            | Q(email__icontains=search_text)
            | Q(reviewer__article__keywords__word__icontains=search_text),
        )
    qs = self.exclude_authors(workflow)
    if q_filters:
        qs = qs.filter(q_filters)
    # FIXME: Order must be updated once we have full annotation for user types
    return qs.order_by("-is_active")
