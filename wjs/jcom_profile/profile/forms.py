from core.forms import EditAccountForm
from core.models import Interest
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.forms.utils import pretty_name
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext_lazy as _
from plugins.wjs_submission.helpers.forms import required_help_text
from submission.models import Keyword
from utils.middleware import context_request

from ..constants import CAREER_STAGES_FORM, GENDER_FORM_CHOICES, PROFESSIONS_FORM
from ..forms import KeywordCheckboxesFormMixin, _get_privacy_url
from ..models import JCOMProfile, WjsMiniHTMLFormField
from ..templatetags.wjs_tags import is_field_available
from ..utils import get_active_keywords
from .required_fields import RequiredEntry


class CorrespondenceAuthorRequirementsFormMixin:
    """
    Mark with the "Required" help text the fields that the journal requires of a correspondence author.

    The form receives the `correspondence_author_requirements` kwarg (see `get_correspondence_author_requirements`;
    empty unless the author comes from the submission) and sets, for the entries of its own `submission_section`, the
    help text of each named field following wjs-submission's `required_help_text` pattern. An entry spanning several
    fields ("at least one of") says so in the help text of its optional fields (a field the form always requires
    just says Required): with the entry's own `group_help` if it has one, otherwise naming the fields. Without
    requirements the form is left untouched.
    """

    submission_section: str

    @staticmethod
    def _required_help_text(field: forms.Field, existing: str) -> SafeString:
        """
        Return the Required help text of the field.

        The theme colours (primary, bold) only the help text that directly follows an input or a select. For other
        widgets, such as the rich text editor of the biography, the word Required gets the same look explicitly.
        """
        if isinstance(field.widget, (forms.widgets.Input, forms.Select)):
            return required_help_text(existing)
        label = format_html('<strong class="text-primary">{}</strong>', _("Required"))
        if existing:
            return format_html("{} - {}", label, mark_safe(existing))  # noqa: S308
        return label

    def apply_correspondence_author_requirements(self, requirements: list[RequiredEntry] | None) -> None:
        """Set the Required help text on the fields named by the entries of this form's section."""
        plain_required = str(_("Required"))
        for entry in requirements or []:
            if entry.section != self.submission_section:
                continue
            for name in entry.fields:
                field = self.fields.get(name)
                if field is None:
                    continue
                existing = str(field.help_text)
                if existing == plain_required:
                    existing = ""
                if len(entry.fields) > 1 and not field.required:
                    # Not `self[name].label`: building a bound field now would cache the help text before it is set
                    labels = ", ".join(
                        str(self.fields[field_name].label or pretty_name(field_name)) for field_name in entry.fields
                    )
                    group = str(entry.group_help or _("fill in at least one of: %(fields)s") % {"fields": labels})
                    existing = f"{group}. {existing}" if existing else group
                field.help_text = self._required_help_text(field, existing)


class WjsPersonalInfoForm(CorrespondenceAuthorRequirementsFormMixin, EditAccountForm):
    """Form used to change personal info."""

    submission_section = "core_edit_profile"

    first_name = forms.CharField(label=_("First name"), required=False)
    middle_name = forms.CharField(label=_("Middle name"), required=False)
    last_name = forms.CharField(label=_("Last name"), required=True)
    year_of_birth = forms.IntegerField(label=_("Year of birth"), required=False)
    gender = forms.ChoiceField(label=_("Gender"), choices=GENDER_FORM_CHOICES, required=False)
    profession = forms.ChoiceField(label=_("Profession"), required=False, choices=PROFESSIONS_FORM)
    career_stage = forms.ChoiceField(label=_("Career stage"), required=False, choices=CAREER_STAGES_FORM)
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
            "profession",
            "career_stage",
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
        correspondence_author_requirements = kwargs.pop("correspondence_author_requirements", None)

        super().__init__(*args, **kwargs)

        privacy_url = _get_privacy_url(self.journal)
        self.fields["first_name"].required = False
        self.fields["gdpr_checkbox"].label = mark_safe(
            _('Agree to our <a href="%s">Privacy Policy</a>') % privacy_url,
        )
        if not is_field_available(self.journal, "profession"):
            self.fields["profession"].required = False
            self.fields["profession"].widget = forms.HiddenInput()
        else:
            self.fields["profession"].required = True
        if not is_field_available(self.journal, "career_stage"):
            self.fields["career_stage"].required = False
            self.fields["career_stage"].widget = forms.HiddenInput()
        else:
            self.fields["career_stage"].required = True
        if self.instance.jcomprofile.gdpr_acceptance:
            self.fields["gdpr_checkbox"].widget = forms.HiddenInput()
        else:
            self.fields["privacy_notice"].widget = forms.HiddenInput()
        for field in self.fields:
            if self.fields[field].required:
                self.fields[field].help_text = _("Required")
        self.apply_correspondence_author_requirements(correspondence_author_requirements)

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

    def clean_career_stage(self) -> int | None:
        """
        Cleans and validates the 'career_stage' field.

        This method checks the 'career_stage' field provided in the cleaned data
        and ensures that it is not empty. If the field is empty, it returns None.
        Otherwise, it returns the validated career stage.

        :return: The validated career stage if present, otherwise None.
        """
        career_stage = self.cleaned_data["career_stage"]
        if not career_stage:
            return None
        try:
            return int(career_stage)
        except ValueError:
            return None


class WjsEmailChangeForm(CorrespondenceAuthorRequirementsFormMixin, EditAccountForm):
    """Form used to change password."""

    submission_section = "core_edit_profile_email"

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
        correspondence_author_requirements = kwargs.pop("correspondence_author_requirements", None)
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update({"readonly": True})
        self.apply_correspondence_author_requirements(correspondence_author_requirements)


class WjsPasswordChangeForm(EditAccountForm):
    """Form used to change password."""

    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}), label=_("Current Password"), required=True
    )
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


class WjsAdditionalInfoForm(CorrespondenceAuthorRequirementsFormMixin, EditAccountForm):
    """Form used to change personal info."""

    submission_section = "core_edit_profile_additional"

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
        correspondence_author_requirements = kwargs.pop("correspondence_author_requirements", None)
        super().__init__(*args, **kwargs)
        self.apply_correspondence_author_requirements(correspondence_author_requirements)


class WjsInterestsForm(KeywordCheckboxesFormMixin, EditAccountForm):
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
        self.fields["keywords"].queryset = get_active_keywords(self.journal).exclude(word="").order_by("word")

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
