"""Forms for the additional fields in this profile extension."""

import uuid

from core.forms import EditAccountForm
from django import forms
from django.forms import ModelForm
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from utils.forms import CaptchaForm

from wjs.jcom_profile.models import ArticleWrapper, JCOMProfile, SpecialIssue


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

    password_1 = forms.CharField(widget=forms.PasswordInput, label=_("Password"))
    password_2 = forms.CharField(widget=forms.PasswordInput, label=_("Repeat Password"))
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
        """Check and saves user's password."""
        user = super().save(commit=False)
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
        queryset=None,
        required=False,
        # cannot use blank=True,  # django > 1.11
        empty_label="➙ Normal submission ➙",
        # TODO: maybe widget=forms.RadioSelect()
    )

    def __init__(self, *args, **kwargs):
        """Init the query set now, otherwise we are missing a current_journal."""
        # https://docs.djangoproject.com/en/4.1/ref/forms/fields/#fields-which-handle-relationships
        super().__init__(*args, **kwargs)
        self.fields["special_issue"].queryset = SpecialIssue.objects.current_journal().open_for_submission()

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
