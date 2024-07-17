"""Forms for the additional fields in this profile extension."""

import uuid

from core import models as core_models
from core.forms import EditAccountForm
from django import forms
from django.conf import settings
from django.db.models import Count
from django.forms import ModelForm, inlineformset_factory
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from easy_select2.widgets import Select2Multiple
from journal.forms import SEARCH_SORT_OPTIONS
from journal.forms import SearchForm as JanewaySearchForm
from submission import models as submission_models
from submission.models import Keyword, Section
from utils import logic as utils_logic
from utils.forms import CaptchaForm
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import (
    ArticleWrapper,
    EditorAssignmentParameters,
    EditorKeyword,
    JCOMProfile,
    Recipient,
    SpecialIssue,
)
from wjs.jcom_profile.settings_helpers import get_journal_language_choices

logger = get_logger(__name__)


class GDPRAcceptanceForm(forms.Form):
    """A GDPR form, consisting in a checkbox.

    It is sued by JCOMRegistrationForm to let user explicitly accept
    the GDPR Policy.
    """

    gdpr_checkbox = forms.BooleanField(initial=False, required=True)


class AnonymousNewsletterSubscriptionAcceptanceForm(forms.Form):
    accepted_subscription = forms.BooleanField(initial=False, required=True)


def _get_privacy_url(journal):
    try:
        privacy_url = get_setting(
            "general",
            "privacy_policy_url",
            journal,
            create=False,
            default=True,
        ).processed_value
    except core_models.Setting.DoesNotExist:
        privacy_url = None
    if not privacy_url:
        privacy_url = reverse("cms_page", args=("privacy",))
    return privacy_url


class JCOMProfileForm(EditAccountForm):
    """Additional fields of the JCOM profile."""

    email = forms.EmailField(label=_("Email"), required=False)
    current_password = forms.CharField(widget=forms.PasswordInput, label=_("Current Password"), required=False)
    new_password_one = forms.CharField(widget=forms.PasswordInput, label=_("New Password"), required=False)
    new_password_two = forms.CharField(widget=forms.PasswordInput, label=_("Repeat New Password"), required=False)
    gdpr_checkbox = forms.BooleanField(
        initial=False,
        required=True,
        label=_("By registering an account you agree to our Privacy Policy"),
    )

    class Meta:
        model = JCOMProfile
        exclude = (
            "email",
            "username",
            "activation_code",
            "email_sent",
            "date_confirmed",
            "confirmation_code",
            "is_active",
            "is_staff",
            "is_admin",
            "date_joined",
            "password",
            "is_superuser",
            "janeway_account",
            "invitation_token",
        )

    def __init__(self, *args, **kwargs):
        """Set the required fields."""
        self.journal = kwargs.pop("journal")
        super().__init__(*args, **kwargs)
        privacy_url = _get_privacy_url(self.journal)
        self.fields["gdpr_checkbox"].label = mark_safe(
            _('By registering an account you agree to our <a href="%s">Privacy Policy</a>') % privacy_url,
        )


class JCOMRegistrationForm(ModelForm, CaptchaForm, GDPRAcceptanceForm):
    """A form that creates a user.

    With no privileges, from the given username and password.

    """

    password_1 = forms.CharField(widget=forms.PasswordInput, label=_("Password"))
    password_2 = forms.CharField(widget=forms.PasswordInput, label=_("Repeat Password"))
    gdpr_checkbox = forms.BooleanField(
        initial=False,
        required=True,
        label=_("By registering an account you agree to our Privacy Policy"),
    )

    class Meta:
        model = JCOMProfile
        fields = (
            "email",
            "salutation",
            "first_name",
            "middle_name",
            "last_name",
            "department",
            "institution",
            "country",
            "profession",
            "gdpr_checkbox",
        )

    def __init__(self, *args, **kwargs):
        """Set the required fields."""
        self.journal = kwargs.pop("journal")
        super().__init__(*args, **kwargs)
        privacy_url = _get_privacy_url(self.journal)
        self.fields["gdpr_checkbox"].label = mark_safe(
            _('By registering an account you agree to our <a href="%s">Privacy Policy</a>') % privacy_url,
        )

    def clean_password_2(self):
        """Validate password."""
        password_1 = self.cleaned_data.get("password_1")
        password_2 = self.cleaned_data.get("password_2")
        if password_1 and password_2 and password_1 != password_2:
            raise forms.ValidationError(
                "Your passwords do not match.",
                code="password_mismatch",
            )

        return password_2

    def save(self, commit=True):
        """Check and saves user's password."""
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password_1"])
        user.is_active = False
        user.confirmation_code = uuid.uuid4()
        user.email_sent = timezone.now()

        if commit:
            user.save()

        return user


class SIForm(forms.ModelForm):
    """Used to choose the destination special issue during submission."""

    class Meta:
        model = ArticleWrapper
        fields = ("special_issue",)

    special_issue = forms.ModelChoiceField(
        queryset=None,
        required=False,
        blank=True,
        empty_label="Normal Issue",
        widget=forms.RadioSelect(),
    )

    def __init__(self, *args, **kwargs):
        """Init the query set now, otherwise we are missing a current_journal."""
        # https://docs.djangoproject.com/en/4.1/ref/forms/fields/#fields-which-handle-relationships
        super().__init__(*args, **kwargs)
        self.fields["special_issue"].queryset = (
            SpecialIssue.objects.current_journal().open_for_submission().current_user()
        )

    # TODO: how do I represent the "no special issue" case?
    # - A1 keep a special issue called "normal submission" always open
    # - A2 dynamically attach a choice called "normal submission" that is not a s.i. and deal with it in the form
    # - A3 add a field called "normal submission" to the form
    # - A4 use a radio-button widget (+reset button) and organize the
    #   submission form as follows:
    #    +--------------------------------------------------+
    #    |     If your submission is not related to any     |
    #    |     special issue, click here to continue        |
    #    |                 +------------+                   |
    #    |                 |  Continue  |                   |
    #    |                 +------------+                   |
    #    |                                                  |
    #    |   ----------------Special Issues---------------  |
    #    |   +---+                                          |
    #    |   |   |   Special Issue 1                        |
    #    |   +---+                                          |
    #    |   +---+                                          |
    #    |   |   |   Special Issue 2                        |
    #    |   +---+                                          |
    #    |   +---+                                          |
    #    |   |   |   Special Issue 3                        |
    #    |   +---+                                          |
    #    +--------------------------------------------------+


class UpdateAssignmentParametersForm(forms.ModelForm):
    keywords = forms.ModelMultipleChoiceField(
        label=_("Keywords"),
        queryset=Keyword.objects.all(),
        # TODO: Ad this in app.css .select2-container {width: 100% !important;}
        widget=Select2Multiple(),
        required=False,
    )

    class Meta:
        model = EditorAssignmentParameters
        fields = ("workload",)

    def __init__(self, *args, **kwargs):
        """Know your kwds."""
        if "initial" not in kwargs:
            kwargs["initial"] = {}

        kwargs["initial"]["keywords"] = kwargs["instance"].keywords.all()

        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        """Save m2m with through and with not _meta.auto_created."""
        # salviamo il form senza il m2m per le kwds: solo worload
        instance = super().save(commit=commit)

        kwds = self.cleaned_data["keywords"]
        for kwd in kwds:
            through, _ = EditorKeyword.objects.get_or_create(keyword=kwd, editor_parameters=instance)
            # don't look at weight, because the editor does not set it
            # (it is managed by the director).
            # ... through.weight = ...

        EditorKeyword.objects.filter(editor_parameters=instance).exclude(keyword__in=kwds).delete()
        return instance


class DirectorEditorAssignmentParametersForm(forms.ModelForm):
    class Meta:
        model = EditorAssignmentParameters
        fields = [
            "brake_on",
            "workload",
        ]
        widgets = {
            "workload": forms.TextInput(attrs={"readonly": True}),
        }


class EditorKeywordForm(forms.ModelForm):
    # this is a "fake_factory" field added only to have a proper rendering of the keyword value, but without any link
    # to the model field
    keyword_str = forms.CharField(widget=forms.TextInput(attrs={"readonly": True}), label=_("Keyword"))
    field_order = ["keyword_str", "weight"]

    class Meta:
        model = EditorKeyword
        fields = ["weight"]

    def __init__(self, *args, **kwargs):  # noqa
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        # forcing the keyword content in the "fake_factory" field allowed the field to be rendered, but it's
        # disconnected from the model field and is ignored on save
        kwargs["initial"]["keyword_str"] = kwargs["instance"].keyword.word
        super().__init__(*args, **kwargs)


EditorKeywordFormset = inlineformset_factory(
    EditorAssignmentParameters,
    EditorKeyword,
    fk_name="editor_parameters",
    extra=0,
    can_delete=False,
    form=EditorKeywordForm,
)


class IMUForm(forms.Form):
    """Import Many Users.

    Let the op upload a spreadsheet with author/title data.
    """

    # This feature was called "IMU" on PoS (:nostalgic:)

    data_file = forms.FileField(
        allow_empty_file=False,
        required=True,
        help_text=_("Upload odt file with first, middle, last name, email, affiliation, paper title; one per row."),
    )
    create_articles_on_import = forms.BooleanField(
        required=False,
        initial=True,
        help_text=_("If set to false, articles are not created. The authors must start a submission themselves."),
    )
    EURISTICS = (
        ("optimistic", "Optimistic - risk to merge different people"),
        ("convervative", "Conservative - risk multiple accounts for the same person"),
    )
    match_euristic = forms.ChoiceField(
        choices=EURISTICS,
        required=True,
        initial="optimistic",
        label=_("Match euristics - NOT IMPLEMENTED"),
        help_text=_("Being optimistic ... TODO WRITE ME!"),
    )
    type_of_new_articles = forms.ModelChoiceField(
        queryset=Section.objects.none(),
        required=True,
        help_text=_("All new contributions will have the choosen section (article type)."),
    )

    def __init__(self, *args, **kwargs):
        """Populate type_of_new_articles queryset from the allowed_section of the current s.i."""
        special_issue_id = kwargs.pop("special_issue_id")
        super().__init__(*args, **kwargs)
        if not self.data.get("type_of_new_articles", None):
            special_issue = SpecialIssue.objects.get(pk=special_issue_id)
            queryset = special_issue.allowed_sections.all()
            self.fields["type_of_new_articles"].queryset = queryset
            self.fields["type_of_new_articles"].initial = queryset.first()
        else:
            self.fields["type_of_new_articles"].queryset = Section.objects.filter(
                pk=self.data["type_of_new_articles"],
            )


class IMUEditExistingAccounts(forms.ModelForm):
    """Form to allow the modification of exising account during IMU process."""

    apply_changes = forms.BooleanField(
        required=False,
        initial=False,
        help_text=_("Apply changes to this user account"),
    )

    class Meta:
        model = core_models.Account
        fields = [
            "first_name",
            "middle_name",
            "last_name",
            "email",
            "institution",
        ]


class IMUHelperForm(forms.Form):
    """Form to help in the validation of user data from step 2 used in step 3.

    Fields should agree with fields of core.Account collected from the ods.
    """

    first_name = forms.CharField(max_length=300, required=True, strip=True)
    middle_name = forms.CharField(
        max_length=300,
        required=False,
        strip=True,
        empty_value=None,
    )
    last_name = forms.CharField(max_length=300, required=True, strip=True)
    email = forms.EmailField(required=True)
    institution = forms.CharField(
        max_length=1000,
        required=False,
        strip=True,
        empty_value=None,
    )
    title = forms.CharField(max_length=999, required=False, strip=True, empty_value=None)


class SIUpdateForm(forms.ModelForm):
    class Meta:
        model = SpecialIssue
        # same fields as SICreate; do not add "documents": they are dealt with "manually"

        fields = ["name", "short_name", "description", "open_date", "close_date", "journal", "allowed_sections"]

    def __init__(self, *args, **kwargs):
        """Filter sections to show only sections of the special issue's journal."""
        super().__init__(*args, **kwargs)
        self.fields["allowed_sections"].queryset = Section.objects.filter(
            journal=self.instance.journal,
        )


class NewsletterTopicForm(forms.ModelForm):
    topics = forms.ModelMultipleChoiceField(
        label="",
        queryset=None,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    news = forms.BooleanField(required=False, label=_("I want to receive alerts about news published in the journal."))
    language = forms.ChoiceField(
        required=True,
        label=_("Preferred language for alerts"),
        choices=settings.LANGUAGES,
    )

    class Meta:
        model = Recipient
        fields = (
            "topics",
            "news",
            "language",
        )

    def __init__(self, *args, **kwargs):
        """Prepare the queryset for topics."""
        self.base_fields["topics"].queryset = kwargs.get("instance").journal.keywords.all().order_by("word")

        # Manage the language field's choices
        request = utils_logic.get_current_request()
        available_languages = []
        if request and request.journal:
            available_languages = get_journal_language_choices(request.journal)

        super().__init__(*args, **kwargs)

        if len(available_languages) > 1:
            self.fields["language"].choices = available_languages
        else:
            # Let's hide the language select if there is only one choice
            del self.fields["language"]

    def clean(self):
        """Log a warning if the user choose no topics and no news.

        We do _not_ raise a Validation error untill specs#474 is done.
        """
        cleaned_data = super().clean()

        topics = cleaned_data.get("topics")
        news = cleaned_data.get("news")
        if len(topics) == 0 and news is False:
            logger.warning(f"Recipient {self.instance.email}/{self.instance.user} selected no topics and no news.")
            # after #474 # raise ValidationError(
            # after #474 #     _('You have selected no news and no topics.
            # after #474 #        Please either choose something or click "Unsubscribe".'),
            # after #474 # )

        return cleaned_data


class RegisterUserNewsletterForm(CaptchaForm):
    """Register an Anonymous user to a newsletter."""

    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={"placeholder": _("Your email address")}))


class SearchForm(JanewaySearchForm):
    SEARCH_FILTERS = [
        "title",
        "abstract",
        "authors",
        "keywords__word",
        "full_text",
        "orcid",
    ]

    article_search = forms.CharField(
        label=_("Search titles, keywords, and authors"),
        min_length=3,
        max_length=100,
        required=False,
    )
    sections = forms.ModelMultipleChoiceField(
        label=_("Filter by section"),
        queryset=Section.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    keywords = forms.ModelMultipleChoiceField(
        label=_("Filter by keyword"),
        queryset=Keyword.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    year = forms.CharField(
        label=_("Filter by year"),
        required=False,
        widget=forms.NumberInput(attrs={"placeholder": "YYYY", "min": 1990, "max": 2100}),
    )
    show = forms.ChoiceField(
        label=_("Show"),
        required=False,
        choices=[(item, item) for item in (10, 25, 50, 100)],
    )
    page = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        """Populate the sections and keywords queryset."""
        self.journal = kwargs.pop("journal")
        kwargs["initial"] = kwargs.get("initial", {})
        original_data = kwargs.get("data", {})
        kwargs["data"] = original_data.copy()
        kwargs["data"]["sections"] = kwargs["data"].getlist("sections", None)
        kwargs["data"]["keywords"] = kwargs["data"].getlist("keywords", None)
        kwargs["data"]["show"] = kwargs["data"].get("show", 10)
        kwargs["data"]["sort"] = kwargs["data"].get("sort", "-date_published")
        super().__init__(*args, **kwargs)
        self.fields["sections"].queryset = Section.objects.filter(
            journal=self.journal,
            is_filterable=True,
        ).order_by("sequence", "name")
        self.fields["keywords"].queryset = (
            Keyword.objects.filter(
                article__journal=self.journal,
                article__stage=submission_models.STAGE_PUBLISHED,
                article__date_published__lte=timezone.now(),
            )
            .annotate(articles_count=Count("article"))
            .order_by("word")
        )

    def get_search_filters(self):
        """Generates a dictionary of search_filters from a search form"""
        return {
            "full_text": self.cleaned_data["article_search"],
            "title": self.cleaned_data["article_search"],
            "authors": self.cleaned_data["article_search"],
            "abstract": self.cleaned_data["article_search"],
            "keywords__word": self.cleaned_data["article_search"],
            "orcid": self.cleaned_data["article_search"],
        }

    @cached_property
    def has_filter(self):
        """Determines if the user has selected at least one search filter
        :return: Boolean indicating if there are any search filters selected
        """
        return self.data.get("article_search", "")

    def clean_year(self):
        """Check if the year is a valid year."""
        year = self.cleaned_data.get("year")
        if year and not year.isdigit():
            raise forms.ValidationError("Please enter a valid year.")
        return year

    def clean_show(self):
        """Check if the show is a valid number."""
        show = self.cleaned_data.get("show")
        if not show.isdigit():
            raise forms.ValidationError("Please enter a valid number.")
        return show

    def clean_sort(self):
        """Check if the sort is a valid sort."""
        sort = self.cleaned_data.get("sort")
        if sort not in dict(SEARCH_SORT_OPTIONS):
            return "-date_published"
        return sort
