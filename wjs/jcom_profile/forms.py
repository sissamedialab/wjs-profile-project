"""Forms for the additional fields in this profile extension."""

import uuid

from core.forms import EditAccountForm
from django import forms
from django.forms import ModelForm, inlineformset_factory
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from easy_select2.widgets import Select2Multiple
from submission.models import Keyword, Section
from utils.forms import CaptchaForm

from wjs.jcom_profile.models import (
    ArticleWrapper,
    EditorAssignmentParameters,
    EditorKeyword,
    JCOMProfile,
    SpecialIssue,
)


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
        fields = (
            # "keywords",
            "workload",
        )

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
    # this is a "fake" field added only to have a proper rendering of the keyword value, but without any link
    # to the model field
    keyword_str = forms.CharField(widget=forms.TextInput(attrs={"readonly": True}), label=_("Keyword"))
    field_order = ["keyword_str", "weight"]

    class Meta:
        model = EditorKeyword
        fields = ["weight"]

    def __init__(self, *args, **kwargs):  # noqa
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        # forcing the keyword content in the "fake" field allowed the field to be rendered, but it's disconnected
        # from the model field and is ignored on save
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
