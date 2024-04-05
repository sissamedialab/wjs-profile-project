from typing import Union

import django_filters
from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _
from journal.models import Issue

from wjs.jcom_profile.settings_helpers import get_journal_language_choices

from .communication_utils import get_eo_user
from .managers import ArticleWorkflowQuerySet
from .models import ArticleWorkflow


def status_choices() -> list[tuple[str, str]]:
    """
    Return the list of status choices for the ArticleWorkflowFilter.

    It includes the symbolic status cases, ArticleWorkflow.ReviewStates.choices must be loaded during filterset
    initialization.
    """
    return [
        ("with_unread_messages", _("With any unread messages")),
        ("my_unread_messages", _("With personal unread messages")),
        ("eo_unread_messages", _("With any unread messages by EO")),
        ("with_reviews", _("With assigned reviews for current review round")),
        ("with_pending_reviews", _("With pending reviews for current review round")),
        ("with_all_completed_reviews", _("With all reviews completed for current review round")),
    ]


class BaseArticleWorkflowFilter(django_filters.FilterSet):
    article = django_filters.CharFilter(field_name="article", method="filter_article")

    class Meta:
        model = ArticleWorkflow
        fields = ["article__language", "article__keywords", "article__section"]

    def __init__(self, *args, **kwargs):
        self._journal = kwargs.pop("journal", None)
        super().__init__(*args, **kwargs)

    def filter_article(self, queryset: QuerySet, name: str, value: Union[str, int]) -> QuerySet:
        """
        Filter by article's title, identifier by substring, and id by exact match.

        :param queryset: the queryset to filter
        :type queryset: QuerySet
        :param name: target article foreign key field name
        :type name: str
        :param value: the value to filter
        :type value: Union[str, int]

        :return: the filtered queryset
        :rtype: QuerySet
        """
        if value:
            filters = Q(**{f"{name}__title__icontains": value})
            filters |= Q(**{f"{name}__identifier__identifier__icontains": value})
            try:
                filters |= Q(**{f"{name}__id": int(value)})
            except ValueError:
                pass
            return queryset.filter(filters)
        return queryset


class AuthorArticleWorkflowFilter(BaseArticleWorkflowFilter):
    # Empty to ease further customization
    pass


class ReviewerArticleWorkflowFilter(BaseArticleWorkflowFilter):
    # Empty to ease further customization
    pass


class StaffArticleWorkflowFilter(BaseArticleWorkflowFilter):
    author = django_filters.CharFilter(field_name="article__authors", method="filter_user")
    editor = django_filters.CharFilter(
        field_name="article__editorassignment__editor",
        method="filter_user",
        label="Editor",
    )
    reviewer = django_filters.CharFilter(
        field_name="article__reviewassignment__reviewer",
        method="filter_user",
        label="Reviewer",
    )
    special_issue = django_filters.ModelChoiceFilter(
        field_name="article__primary_issue",
        queryset=Issue.objects.filter(issue_type__code="collection"),
    )
    status = django_filters.ChoiceFilter(
        choices=[],
        field_name="state",
        method="filter_status",
    )

    class Meta:
        model = ArticleWorkflow
        fields = ["article__language", "article__keywords", "article__section"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = self.select_filters()

    def select_filters(self):
        """Customize filters by journal."""
        filters = self.filters
        available_languages = get_journal_language_choices(self._journal)
        if len(available_languages) == 1:
            filters.pop("article__language")
        else:
            filters["article__language"].extra["choices"] = available_languages
        filters["special_issue"].queryset = (
            self.filters["special_issue"].queryset.filter(journal=self._journal).order_by("issue_title")
        )
        filters["article__keywords"].queryset = (
            self.filters["article__keywords"].queryset.filter(journal=self._journal).order_by("word")
        )
        filters["article__section"].queryset = (
            self.filters["article__section"].queryset.filter(journal=self._journal).order_by("name")
        )
        available_states = self.queryset.values_list("state", flat=True).distinct()
        filters["status"].field.choices = status_choices() + [
            state for state in ArticleWorkflow.ReviewStates.choices if state[0] in available_states
        ]
        return filters

    def filter_status(self, queryset: ArticleWorkflowQuerySet, name: str, value: str) -> QuerySet:
        """
        Filter by symbolic status cases.

        If the value matches one of the supported queryset methods, it will be called and the result returned,
        otherwise the queryset will be filtered on the state field matching the value.

        :param queryset: the queryset to filter
        :type queryset: QuerySet
        :param name: target article foreign key field name
        :type name: str
        :param value: the value to filter
        :type value: Union[str, int]

        :return: the filtered queryset
        :rtype: QuerySet
        """
        if value == "eo_unread_messages":
            return queryset.with_unread_messages(get_eo_user(self.request.journal))
        if value == "my_unread_messages":
            return queryset.with_unread_messages(self.request.user)
        if value == "with_unread_messages":
            return queryset.with_unread_messages()
        if value == "with_reviews":
            return queryset.with_reviews()
        if value == "with_pending_reviews":
            return queryset.with_pending_reviews()
        if value == "with_all_completed_reviews":
            return queryset.with_all_completed_reviews()
        if value:
            return queryset.filter(**{name: value})
        return queryset

    def filter_user(self, queryset: QuerySet, name: str, value: str) -> QuerySet:
        """
        Filter by user's email, first name, and last name by substring.

        :param queryset: the queryset to filter
        :type queryset: QuerySet
        :param name: target user foreign key field name
        :type name: str
        :param value: the value to filter
        :type value: str

        :return: the filtered queryset
        :rtype: QuerySet
        """
        if value:
            filters = (
                Q(**{f"{name}__email__icontains": value})
                | Q(**{f"{name}__first_name__icontains": value})
                | Q(**{f"{name}__last_name__icontains": value})
            )
            return queryset.filter(filters)
        return queryset


class EOArticleWorkflowFilter(StaffArticleWorkflowFilter):
    eo_in_charge = django_filters.CharFilter(field_name="eo_in_charge", method="filter_user")
