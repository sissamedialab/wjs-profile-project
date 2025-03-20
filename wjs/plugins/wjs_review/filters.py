from typing import Union

import django_filters
from core.models import Account
from django import forms
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _
from django_filters.fields import ModelChoiceField
from journal.models import Issue
from submission.models import Article, Keyword, Section

from wjs.jcom_profile import constants, permissions
from wjs.jcom_profile.settings_helpers import get_journal_language_choices

from .logic__visibility import get_recipient_label
from .managers import ArticleWorkflowQuerySet
from .models import ArticleWorkflow, Message, Reminder


class SpecialIssueFilterField(ModelChoiceField):
    """
    Field for the Issue model filter.

    Overrides standard ModelChoiceFilter to display `Issue.issue_title` as label of the choices.
    """

    def label_from_instance(self, obj):
        return obj.issue_title


class SpecialIssueFilter(django_filters.ModelChoiceFilter):
    """
    Field for the Issue model filter.

    Overrides standard ModelChoiceFilter to display a custom string as label of the choices.
    """

    field_class = SpecialIssueFilterField


class SectionFilterField(ModelChoiceField):
    """
    Field for the Section model filter.

    Overrides standard ModelChoiceFilter to display `Section.name` as label of the choices.
    """

    def label_from_instance(self, obj):
        return obj.name


class SectionFilterFilter(django_filters.ModelChoiceFilter):
    """
    Filter for the Section model.

    Overrides standard ModelChoiceFilter to display `Section.name` as label of the choices.
    """

    field_class = SectionFilterField


def eo_status_choices() -> list[tuple[str, str]]:
    """
    Return the list of status choices for the ArticleWorkflowFilter for EO users.

    It includes the symbolic status cases, ArticleWorkflow.ReviewStates.choices must be loaded during filterset
    initialization.
    """
    return [
        ("with_unread_messages", _("With unread messages")),
        ("submitted_re", _("(Re)Submitted")),
        ("with_pending_reviews", _("Being reviewed")),
        ("waiting_for_decision", _("Waiting for decision")),
    ]


def status_choices() -> list[tuple[str, str]]:
    """
    Return the list of status choices for the ArticleWorkflowFilter for non EO users.

    It includes the symbolic status cases, ArticleWorkflow.ReviewStates.choices must be loaded during filterset
    initialization.
    """
    return [
        ("with_unread_messages", _("With unread messages")),
        ("submitted_re", _("(Re)Submitted")),
        ("with_pending_reviews", _("Being reviewed")),
        ("waiting_for_decision", _("Waiting for decision")),
    ]


class BaseArticleWorkflowFilter(django_filters.FilterSet):
    template_name = "wjs_review/lists/elements/filters_base.html"

    article = django_filters.CharFilter(field_name="article", method="filter_article", label=_("Title"))
    article_identifiers = django_filters.CharFilter(
        field_name="article",
        method="filter_identifiers",
        label=_("Preprint ID/DOI"),
    )
    correspondence_author = django_filters.CharFilter(
        field_name="article__correspondence_author", method="filter_user", label=_("Corresponding author")
    )
    language = django_filters.ChoiceFilter(
        field_name="article__language",
        label=_("Language"),
        empty_label=_("All"),
    )
    keywords = django_filters.ModelChoiceFilter(
        field_name="article__keywords",
        queryset=Keyword.objects.all(),
        label=_("Keywords"),
        empty_label=_("All"),
    )
    section = SectionFilterFilter(
        field_name="article__section",
        queryset=Section.objects.all(),
        label=_("Article type"),
        empty_label=_("All"),
    )
    special_issue = SpecialIssueFilter(
        field_name="article__primary_issue",
        queryset=Issue.objects.none(),
        label=_("Special Issue"),
        empty_label=_("All"),
    )
    status = django_filters.ChoiceFilter(
        choices=[],
        field_name="state",
        method="filter_status",
        label=_("Status"),
        empty_label=_("All"),
    )
    editor = django_filters.CharFilter(
        field_name="article__editorassignment__editor",
        method="filter_user",
        label=_("Editor"),
    )

    class Meta:
        model = ArticleWorkflow
        fields = ["article", "language", "keywords", "section"]

    def __init__(self, *args, **kwargs):
        self._journal = kwargs.pop("journal", None)
        super().__init__(*args, **kwargs)
        self.filters = self.select_filters()

    def select_filters(self):
        """Customize filters by journal."""
        filters = self.filters
        available_languages = get_journal_language_choices(self._journal)
        available_primary_issues = self.queryset.values_list("article__primary_issue", flat=True).distinct()
        filters["special_issue"].queryset = Issue.objects.filter(
            journal=self._journal, pk__in=available_primary_issues, issue_type__code="collection"
        ).order_by("issue_title")
        if len(available_languages) == 1:
            filters.pop("language")
        else:
            filters["language"].extra["choices"] = available_languages
        available_states = self.queryset.values_list("state", flat=True).exclude(state="EditorSelected").distinct()
        filters["status"].field.choices = [
            state for state in ArticleWorkflow.ReviewStates.choices if state[0] in available_states
        ]
        filters["keywords"].queryset = self.filters["keywords"].queryset.filter(journal=self._journal).order_by("word")
        filters["section"].queryset = self.filters["section"].queryset.filter(journal=self._journal).order_by("name")
        return filters

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

    def filter_identifiers(self, queryset: QuerySet, name: str, value: Union[str, int]) -> QuerySet:
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
            filters = Q(**{f"{name}__identifier__identifier__icontains": value})
            try:
                # We might get the preprint-id (AW.preprint_id);
                # if so, we can strip the journal code to get the article id
                value = value.replace(f"{self._journal.code}_", "")
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
    template_name = "wjs_review/lists/elements/filters_staff.html"

    author = django_filters.CharFilter(field_name="article__authors", method="filter_user", label=_("Authors"))
    reviewer = django_filters.CharFilter(
        field_name="article__reviewassignment__reviewer",
        method="filter_user",
        label=_("Reviewer"),
    )
    typesetter = django_filters.CharFilter(
        field_name="article__typesettinground__typesettingassignment__typesetter",
        method="filter_user",
        label=_("Typesetter"),
    )

    def select_filters(self):
        """Customize filters by journal."""
        filters = super().select_filters()
        available_states = self.queryset.values_list("state", flat=True).exclude(state="EditorSelected").distinct()
        if self.request.user and self.request.user.is_authenticated and permissions.has_eo_role(self.request.user):
            full_choices = eo_status_choices() + [
                state for state in ArticleWorkflow.ReviewStates.choices if state[0] in available_states
            ]
        else:
            full_choices = status_choices() + [
                state for state in ArticleWorkflow.ReviewStates.choices if state[0] in available_states
            ]
        filters["status"].field.choices = full_choices
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
        if value == "with_unread_messages":
            return queryset.with_unread_messages(self.request.user, other_users_messages=True)
        if value == "submitted_re":
            return queryset.submitted_re()
        if value == "with_pending_reviews":
            return queryset.with_pending_reviews()
        if value == "waiting_for_decision":
            return queryset.waiting_for_decision()
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
            if isinstance(value, int):
                return queryset.filter(**{f"{name}__id": value})
            elif isinstance(value, Account):
                return queryset.filter(**{name: value})
            else:
                filters = (
                    Q(**{f"{name}__email__icontains": value})
                    | Q(**{f"{name}__first_name__icontains": value})
                    | Q(**{f"{name}__last_name__icontains": value})
                )
                return queryset.filter(filters)
        return queryset


class EOArticleWorkflowFilter(StaffArticleWorkflowFilter):
    template_name = "wjs_review/lists/elements/filters_eo.html"
    eo_in_charge = django_filters.ModelChoiceFilter(
        field_name="eo_in_charge",
        method="filter_user",
        label=_("EO in charge"),
        empty_label=_("All"),
        queryset=Account.objects.filter(groups__name=constants.EO_GROUP),
    )


class WorkOnAPaperArticleWorkflowFilter(EOArticleWorkflowFilter):
    template_name = "wjs_review/lists/elements/filters_workon.html"

    abstract_or_title = django_filters.CharFilter(
        field_name="article",
        method="filter_abstract_and_title",
        label=_("Title or Abstract"),
    )
    author_country = django_filters.CharFilter(
        field_name="article__correspondence_author__country__name",
        lookup_expr="icontains",
        label=_("Author's country"),
    )
    author_institution = django_filters.CharFilter(
        field_name="article__correspondence_author__institution",
        lookup_expr="icontains",
        label=_("Author's Institution"),
    )

    def filter_abstract_and_title(self, queryset: QuerySet, name: str, value: Union[str, int]) -> QuerySet:
        """
        Filter by article's title and abstract by substring.

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
            filters |= Q(**{f"{name}__abstract__icontains": value})
            return queryset.filter(filters)
        return queryset


class MessageFilter(django_filters.FilterSet):
    """A filter-set to allow the user to filter the messages in the messages page.

    Do not confuse with the form to filter the timeline
    (see forms.TimelinFilterForm).
    """

    actor_recipients = django_filters.ModelChoiceFilter(
        method="filter_actor_recipients",
        label=_("Sender/Recipient"),
        empty_label=_("Sender/Recipient"),
    )
    content = django_filters.CharFilter(
        method="filter_content",
        label=_("Subject/Body"),
    )
    type = django_filters.ChoiceFilter(  # noqa: A003
        field_name="message_type",
        label=_("Type"),
        empty_label=_("All"),
        choices=(
            (Message.MessageTypes.USER, _("User messages")),
            (Message.MessageTypes.NOTE, _("User notes")),
            (Message.MessageTypes.SYSTEM, _("System")),
        ),
    )

    class Meta:
        model = Message
        fields = ["actor_recipients"]

    def __init__(self, *args, **kwargs):
        """Prepare the actor/recipients filter.

        Keep only actors and recipients that appear in any message,
        and ensure that their name are hidden if needed by
        patching the filter's field's label_from_instance method.
        """
        workflow = kwargs.pop("workflow")
        user = kwargs.pop("user")
        super().__init__(*args, **kwargs)
        actors = self.queryset.values_list("actor", flat=True)
        recipients = self.queryset.values_list("messagerecipients__recipient", flat=True)
        self.filters["actor_recipients"].queryset = Account.objects.filter(Q(pk__in=actors) | Q(pk__in=recipients))
        self.filters["actor_recipients"].field.label_from_instance = lambda obj: get_recipient_label(
            workflow=workflow,
            user=user,
            recipient=obj,
        )

    def filter_content(self, queryset: QuerySet, name: str, value: str):
        if value:
            queryset = queryset.filter(Q(subject__icontains=value) | Q(body__icontains=value))
        return queryset

    def filter_actor_recipients(self, queryset: QuerySet, name: str, value: str):
        if value:
            queryset = queryset.filter(Q(actor=value) | Q(messagerecipients__recipient=value))
        return queryset


class MessagesOverviewFilter(django_filters.FilterSet):
    """
    A filter-set to allow the EO to filter the messages in the messages overview page.
    """

    from_date = django_filters.DateFilter(
        field_name="created",
        lookup_expr="gte",
        label=_("From date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    to_date = django_filters.DateFilter(
        field_name="created",
        lookup_expr="lte",
        label=_("To date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    eo_in_charge = django_filters.ModelChoiceFilter(
        field_name="eo_in_charge",
        method="filter_eo_in_charge",
        label=_("EO in charge"),
        empty_label=_("All"),
        queryset=Account.objects.filter(groups__name=constants.EO_GROUP),
    )
    actor_recipients = django_filters.ModelChoiceFilter(
        method="filter_actor_recipients",
        label=_("Sender/Recipient"),
        empty_label=_("Sender/Recipient"),
    )
    content = django_filters.CharFilter(
        method="filter_content",
        label=_("Search on subject / body"),
    )
    type = django_filters.ChoiceFilter(  # noqa: A003
        field_name="message_type",
        label=_("Type"),
        empty_label=_("All"),
        choices=(
            (Message.MessageTypes.USER, _("User messages")),
            (Message.MessageTypes.NOTE, _("User notes")),
            (Message.MessageTypes.SYSTEM, _("System")),
        ),
    )

    def __init__(self, *args, **kwargs):
        """ """
        super().__init__(*args, **kwargs)
        self.filters["actor_recipients"].queryset = Account.objects.all()

    def filter_content(self, queryset: QuerySet, name: str, value: str):
        if value:
            queryset = queryset.filter(Q(subject__icontains=value) | Q(body__icontains=value))
        return queryset

    def filter_actor_recipients(self, queryset: QuerySet, name: str, value: str):
        if value:
            queryset = queryset.filter(Q(actor=value) | Q(messagerecipients__recipient=value))
        return queryset

    def filter_eo_in_charge(self, queryset: QuerySet, name: str, value: str):
        if value:
            queryset = queryset.filter(
                content_type=ContentType.objects.get_for_model(Article),
                object_id__in=ArticleWorkflow.objects.filter(eo_in_charge=value).values("article__id"),
            )
        return queryset


class ReminderFilter(django_filters.FilterSet):
    recipient = django_filters.ModelChoiceFilter(
        label=_("Filter by recipient"),
        empty_label=_("Filter by recipient"),
        queryset=Account.objects.none(),
    )
    code__startswith = django_filters.ChoiceFilter(
        label=_("Filter by reminder type"),
        empty_label=_("Filter by reminder type"),
        choices=Reminder.ReminderClasses.choices,
        field_name="code",
        lookup_expr="startswith",
    )

    class Meta:
        model = Reminder
        fields = {
            "recipient": ["exact"],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        recipients = self.queryset.values_list("recipient", flat=True)
        self.filters["recipient"].queryset = Account.objects.filter(pk__in=recipients)
