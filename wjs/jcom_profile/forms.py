"""Forms for the additional fields in this profile extension."""

import uuid

from core.forms import EditAccountForm
from django import forms
from django.forms import ModelForm
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from wjs.jcom_profile.models import ArticleWrapper, JCOMProfile, SpecialIssue

from utils.forms import CaptchaForm


class GDPRAcceptanceForm(forms.Form):
    """A GDPR form, consisting in a checkbox.

    It is sued by JCOMRegistrationForm to let user explicitly accept
    the GDPR Policy.
    """

    gdpr_checkbox = forms.BooleanField(initial=False, required=True)


class JCOMProfileForm(EditAccountForm):
    """Additional fields of the JCOM profile."""

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


class JCOMRegistrationForm(ModelForm, CaptchaForm, GDPRAcceptanceForm):
    """A form that creates a user.

    With no privileges, from the given username and password.

    """

    password_1 = forms.CharField(
        widget=forms.PasswordInput, label=_("Password")
    )
    password_2 = forms.CharField(
        widget=forms.PasswordInput, label=_("Repeat Password")
    )
    gdpr_checkbox = forms.BooleanField(initial=False, required=True)

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
        user = super(JCOMRegistrationForm, self).save(commit=False)
        user.set_password(self.cleaned_data["password_1"])
        user.is_active = False
        user.confirmation_code = uuid.uuid4()
        user.email_sent = timezone.now()

        if commit:
            user.save()

        return user


class InviteUserForm(forms.Form):
    """Used by staff to invite external users for review activities."""

    first_name = forms.CharField()
    last_name = forms.CharField()
    email = forms.EmailField()
    institution = forms.CharField()
    department = forms.CharField()
    message = forms.CharField(widget=forms.Textarea)


class SIForm(forms.ModelForm):
    """Used to choose the destination special issue during submission."""

    class Meta:
        model = ArticleWrapper
        fields = ("special_issue",)

    special_issue = forms.ModelChoiceField(
        queryset=SpecialIssue.objects.filter(is_open_for_submission=True),
        initial=0,
    )
