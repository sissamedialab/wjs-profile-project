from core.forms import EditAccountForm
from core.models import Interest
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext_lazy as _
from submission.models import Keyword
from utils.middleware import context_request

from ..constants import GENDER_FORM_CHOICES, PROFESSIONS_FORM
from ..models import JCOMProfile, WjsMiniHTMLFormField


class WjsPersonalInfoForm(EditAccountForm):
    """Form used to change personal info."""

    first_name = forms.CharField(label=_("First name"), help_text=_("Required"), required=True)
    middle_name = forms.CharField(label=_("Middle name"), required=False)
    last_name = forms.CharField(label=_("Last name"), help_text=_("Required"), required=True)
    year_of_birth = forms.IntegerField(
        label=_("Year of birth"),
        required=False,
    )
    gender = forms.ChoiceField(
        label=_("Gender"),
        choices=GENDER_FORM_CHOICES,
        required=False,
    )
    profession = forms.ChoiceField(
        label=_("Profession"), help_text=_("Required"), required=True, choices=PROFESSIONS_FORM
    )
    privacy_notice = forms.DateField(
        label=_("Privacy notice acknowledge on"), widget=forms.DateInput(attrs={"readonly": True}), required=False
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
        )
        exclude = None

    def __init__(self, *args, **kwargs):
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        if kwargs["instance"]:
            kwargs["initial"]["privacy_notice"] = kwargs["instance"].jcomprofile.gdpr_acceptance

        super().__init__(*args, **kwargs)


class WjsEmailChangeForm(EditAccountForm):
    """Form used to change password."""

    email = forms.EmailField(label=_("Current email address"), required=False)
    new_email = forms.EmailField(label=_("New email address"), required=False)

    class Meta:
        model = JCOMProfile
        fields = ("email",)
        exclude = None

    def clean_new_email(self):
        validate_email(self.cleaned_data["new_email"])
        if (
            self.instance.__class__.objects.filter(email=self.cleaned_data["new_email"])
            .exclude(id=self.instance.id)
            .exists()
        ):
            raise ValidationError(_("An account with that email address already exists."))
        return self.cleaned_data["new_email"]

    def __init__(self, *args, **kwargs):
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


class WjsInterestsForm(EditAccountForm):
    """Form used to change password."""

    keywords = forms.ModelMultipleChoiceField(label=_("Interests"), required=False, queryset=Keyword.objects.none())

    class Meta:
        model = JCOMProfile
        fields = ("keywords",)
        exclude = None

    def __init__(self, *args, **kwargs):
        """Set the required fields."""
        journal = kwargs.pop("journal")
        kwargs["initial"]["keywords"] = Keyword.objects.filter(
            word__in=kwargs["instance"].interest.values_list("name", flat=True)
        ).order_by("word")
        super().__init__(*args, **kwargs)
        self.fields["keywords"].queryset = journal.keywords.exclude(word="").order_by("word")

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
