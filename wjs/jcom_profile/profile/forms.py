from core.forms import EditAccountForm
from core.models import Interest
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from submission.models import Keyword
from utils.middleware import context_request

from ..constants import GENDER_FORM_CHOICES, PROFESSIONS_FORM
from ..forms import _get_privacy_url
from ..models import JCOMProfile, WjsMiniHTMLFormField
from ..templatetags.wjs_tags import is_field_available


class WjsPersonalInfoForm(EditAccountForm):
    """Form used to change personal info."""

    first_name = forms.CharField(label=_("First name"), required=False)
    middle_name = forms.CharField(label=_("Middle name"), required=False)
    last_name = forms.CharField(label=_("Last name"), required=True)
    year_of_birth = forms.IntegerField(label=_("Year of birth"), required=False)
    gender = forms.ChoiceField(label=_("Gender"), choices=GENDER_FORM_CHOICES, required=False)
    profession = forms.ChoiceField(label=_("Profession"), required=False, choices=PROFESSIONS_FORM)
    privacy_notice = forms.DateTimeField(
        label=_("Privacy notice acknowledged on"), widget=forms.DateTimeInput(attrs={"readonly": True}), required=False
    )
    gdpr_checkbox = forms.BooleanField(
        required=True,
        label=_("Agree to our Privacy Policy"),
    )
    biography = WjsMiniHTMLFormField(
        label=_("Biography"),
        required=False,
        help_text=_("Biographies are compulsory for authors and will be included in published papers."),
    )

    class Meta:
        model = JCOMProfile
        fields = (
            "first_name",
            "middle_name",
            "last_name",
            "year_of_birth",
            "gender",
            "orcid",
            "profession",
            "preferred_timezone",
            "biography",
            "privacy_notice",
            "gdpr_checkbox",
        )
        exclude = None

    def __init__(self, *args, **kwargs):
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        if kwargs["instance"]:
            kwargs["initial"]["privacy_notice"] = kwargs["instance"].jcomprofile.gdpr_acceptance
        self.journal = kwargs.pop("journal")

        super().__init__(*args, **kwargs)

        privacy_url = _get_privacy_url(self.journal)
        self.fields["first_name"].required = False
        self.fields["gdpr_checkbox"].label = mark_safe(
            _('Agree to our <a href="%s">Privacy Policy</a>') % privacy_url,
        )
        if not is_field_available(self.journal, "profession"):
            self.fields["profession"].required = False
            self.fields["profession"].widget = forms.HiddenInput()
        if self.instance.jcomprofile.gdpr_acceptance:
            self.fields["gdpr_checkbox"].widget = forms.HiddenInput()
        else:
            self.fields["privacy_notice"].widget = forms.HiddenInput()
        for field in self.fields:
            if self.fields[field].required:
                self.fields[field].help_text = _("Required")

    def clean_profession(self) -> int | None:
        """
        Cleans and validates the 'profession' field.

        This method checks the 'profession' field provided in the cleaned data
        and ensures that it is not empty. If the field is empty, it returns None.
        Otherwise, it returns the validated profession.

        :return: The validated profession if present, otherwise None.
        """
        profession = self.cleaned_data["profession"]
        if not profession:
            return None
        try:
            return int(profession)
        except ValueError:
            return None


class WjsEmailChangeForm(EditAccountForm):
    """Form used to change password."""

    email = forms.EmailField(label=_("Current email address"), required=False)
    new_email = forms.EmailField(label=_("New email address"), required=False)

    class Meta:
        model = JCOMProfile
        fields = ("email", "alternative_email")
        exclude = None

    def clean_new_email(self):
        if not self.cleaned_data.get("new_email"):
            return None
        validate_email(self.cleaned_data["new_email"])
        if (
            self.instance.__class__.objects.filter(email=self.cleaned_data["new_email"])
            .exclude(id=self.instance.id)
            .exists()
        ):
            raise ValidationError(_("An account with that email address already exists."))
        return self.cleaned_data["new_email"]

    def __init__(self, *args, **kwargs):
        self.journal = kwargs.pop("journal")
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update({"readonly": True})


class WjsPasswordChangeForm(EditAccountForm):
    """Form used to change password."""

    current_password = forms.CharField(widget=forms.PasswordInput, label=_("Current Password"), required=True)
    new_password_one = forms.CharField(widget=forms.PasswordInput, label=_("New Password"), required=True)
    new_password_two = forms.CharField(widget=forms.PasswordInput, label=_("Repeat New Password"), required=True)

    class Meta:
        model = JCOMProfile
        fields = ("current_password",)
        exclude = None

    def __init__(self, *args, **kwargs):
        self.journal = kwargs.pop("journal")
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        if not self.instance.check_password(self.cleaned_data["current_password"]):
            raise forms.ValidationError(_("Old password is not correct."))
        return self.cleaned_data["current_password"]

    def clean(self):
        new_password_one = self.cleaned_data["new_password_one"]
        new_password_two = self.cleaned_data["new_password_two"]
        if new_password_one != new_password_two:
            self.add_error("new_password_one", _("Passwords do not match"))
        problems = self.instance.password_policy_check(context_request.get(), new_password_one)
        for problem in problems:
            self.add_error("new_password_one", problem)
        return self.cleaned_data

    def save(self, commit=True):
        self.instance.set_password(self.cleaned_data["new_password_one"])
        self.instance.save()
        return self.instance


class WjsAdditionalInfoForm(EditAccountForm):
    """Form used to change personal info."""

    facebook = forms.CharField(label=_("Facebook Handle"), required=False)
    twitter = forms.CharField(label=_("Bluesky Handle"), required=False)
    linkedin = forms.URLField(label=_("Linkedin Profile"), required=False)

    class Meta:
        model = JCOMProfile
        fields = (
            "facebook",
            "linkedin",
            "twitter",
            "records_scix",
            "records_inspire",
            "records_arxiv",
            "records_other",
        )

    def __init__(self, *args, **kwargs):
        self.journal = kwargs.pop("journal")
        super().__init__(*args, **kwargs)


class WjsInterestsForm(EditAccountForm):
    """Form used to change password."""

    keywords = forms.ModelMultipleChoiceField(label=_("Interests"), required=False, queryset=Keyword.objects.none())

    class Meta:
        model = JCOMProfile
        fields = ("keywords",)
        exclude = None

    def __init__(self, *args, **kwargs):
        """Set the required fields."""
        self.journal = kwargs.pop("journal")
        kwargs["initial"]["keywords"] = Keyword.objects.filter(
            word__in=kwargs["instance"].interest.values_list("name", flat=True)
        ).order_by("word")
        super().__init__(*args, **kwargs)
        self.fields["keywords"].queryset = self.journal.keywords.exclude(word="").order_by("word")

    def save(self, commit=True):
        self.instance.interest.clear()
        posted_interests = self.cleaned_data["keywords"]
        for interest in posted_interests:
            if interest:
                new_interest, _ = Interest.objects.get_or_create(
                    name=interest.word,
                )
                self.instance.interest.add(new_interest)
        return self.instance
