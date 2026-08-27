import datetime
from typing import Any, Dict, Iterable, Optional

from core import files
from core import files as core_files
from core import models as core_models
from core.forms import ConfirmableForm
from core.models import File
from dateutil.parser import parse
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import formset_factory
from django.forms.fields import CharField
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.safestring import mark_safe
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from PIL import Image
from review.forms import GeneratedForm
from review.models import (
    ReviewAssignment,
    ReviewAssignmentAnswer,
    ReviewForm,
    ReviewFormElement,
)
from submission.models import Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile import permissions as base_permissions
from wjs.jcom_profile.constants import EO_GROUP, SECTION_EDITOR_ROLE
from wjs.jcom_profile.models import WjsMiniHTMLFormField, WjsSimpleBleach
from wjs.jcom_profile.permissions import has_eo_role
from wjs.jcom_profile.utils import get_eo_user, render_template_from_setting

from . import ac_service, communication_utils, conditions
from .communication_utils import MESSAGE_TYPE_ICONS
from .logic import (
    AssignToEditor,
    AssignToReviewer,
    AuthorHandleRevisionObsolete,
    DeselectReviewer,
    EvaluateReview,
    HandleDecision,
    HandleEditorDeclinesAssignment,
    HandleMessage,
    InviteReviewer,
    OpenAppeal,
    PostponeReviewerDueDate,
    PostponeRevisionRequestDueDate,
    SubmitReview,
    SupervisorChangeEditorAssignment,
    WithdrawPreprint,
)
from .logic__visibility import get_recipient_label
from .models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    MessageRecipients,
    MessageThread,
    PastEditorAssignment,
    ProphyAccount,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

Account = get_user_model()
logger = get_logger(__name__)


def min_size_validator(min_width, min_height):
    """Validate that an uploaded image meets a minimum size requirement."""

    def validate_image(image):
        try:
            img = Image.open(image)
            width, height = img.size
        except Exception as e:
            logger.error(
                f"The following exeption was raised while trying to open the image: {e}. Invalid image uploaded:"
                f" {image}. If this happens often, please check where min_size_validator is used and review the UI/UX."
            )
            raise ValidationError(_("Uploaded file is not an image."))
        if width < min_width or height < min_height:
            raise ValidationError(_(f"Image must be at least {min_width}x{min_height} pixels."))

    return validate_image


class ArticleReviewStateForm(forms.ModelForm):
    action = forms.ChoiceField(choices=[])
    state = forms.CharField(widget=forms.HiddenInput(), required=False)
    editor = forms.ModelChoiceField(queryset=Account.objects.filter(), required=False)
    reviewer = forms.ModelChoiceField(queryset=Account.objects.filter(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state", "action"]

    def __init__(self, *args, **kwargs):
        """Set the available transitions as choices for the state field."""
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)
        self.fields["action"].choices = [
            (t.name, t.name) for t in self.instance.get_available_user_state_transitions(user=self.user)
        ]

    def clean(self) -> Dict[str, Any]:
        """Validate the action field and set the state field to the transition method."""
        cleaned_data = super().clean()
        action = cleaned_data["action"]
        transitions = {t.name: t for t in self.instance.get_available_user_state_transitions(user=self.user)}
        if action not in transitions:
            raise forms.ValidationError("Invalid state")
        cleaned_data["state"] = self.instance.state
        return cleaned_data

    def save(self, commit: bool = True) -> ArticleWorkflow:
        """Change the state of the review using the transition method."""
        transition_method = getattr(self.instance, self.cleaned_data["action"])
        transition_method()
        instance = super().save()
        return instance


class BaseInviteSelectReviewerForm(forms.Form):
    acceptance_due_date = forms.DateField(label=_("Reviewer should accept/decline invite by"), required=False)
    message_subject = forms.CharField(
        label=_("Message Subject"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "readonly": "readonly",
                "disabled": "disabled",
                "class": "form-control",
            }
        ),
    )
    message = WjsMiniHTMLFormField(label=_("Message"), required=False)
    author_note_visible = forms.BooleanField(
        label=_("Allow reviewer to see author's cover letter"), required=False, initial=True
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

        c_data = self.data.copy()
        c_data["state"] = self.instance.state
        if reviewer := self.request.GET.get("reviewer"):
            c_data["reviewer"] = reviewer
        self.data = c_data
        self._today = now().date()
        # specs #648 #1158
        self.default_acceptance_due_date = self._today + datetime.timedelta(
            days=self.request.journal.get_setting("wjs_review", "default_review_acceptance_days"),
        )
        self.date_min = self._today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MIN)
        self.date_max = self._today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MAX)
        date_attrs = {
            "type": "date",
            "value": self.default_acceptance_due_date,
            "min": self.date_min,
            "max": self.date_max,
        }
        self.fields["acceptance_due_date"].widget = forms.DateInput(attrs=date_attrs)
        if not self.data.get("acceptance_due_date", None):
            self.data["acceptance_due_date"] = self.default_acceptance_due_date

    def _prepare_message_and_subject(self):
        """Prepare the default message and subject.

        The context used in rendering these settings might change if we are
        - selecting an existing user
        - inviting a new user (either a Prophy account or a new user entirely)

        To allow for this, derived classes should define/ovverride their own get_message_context methods.

        Also, this part is not included in the default initialization (__init__) because SelectReviewerForm also deals
        with the case editor-selects-himself as reviewer, where this is not necessary.

        """
        message_context = self.get_message_context()
        if not self.data.get("message", None):
            default_message_rendered = render_template_from_setting(
                setting_group_name="email",
                setting_name="review_assignment",
                journal=self.instance.article.journal,
                request=self.request,
                context=message_context,
                template_is_setting=True,
            )
            self.data["message"] = default_message_rendered
            self.fields["message"].initial = default_message_rendered

        default_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_assignment",
            journal=self.instance.article.journal,
            request=self.request,
            context=message_context,
            template_is_setting=True,
        )
        self.data["message_subject"] = default_subject
        self.fields["message_subject"].initial = default_subject

    def clean_acceptance_due_date(self):
        """Ensure that the due date is in the future.

        We don't see any valid reason for a reviewer to change the date and move it into the past 🙂
        """
        acceptance_due_date = self.cleaned_data["acceptance_due_date"]
        if not acceptance_due_date:
            return acceptance_due_date
        if acceptance_due_date < now().date():
            raise forms.ValidationError(_("Date must be in the future"))
        if (self.date_min and self.date_max) and not (self.date_min <= acceptance_due_date <= self.date_max):
            raise forms.ValidationError(
                _(
                    f"Date must be between {date_format(self.date_min, settings.DATE_FORMAT)} and "
                    f"{date_format(self.date_max, settings.DATE_FORMAT)}"
                )
            )
        return acceptance_due_date

    def clean_logic(self):
        """Run logic instance's check_conditions method."""
        if not self.get_logic_instance(self.cleaned_data).check_conditions():
            raise forms.ValidationError(_("Assignment conditions not met."))

    def clean(self) -> Dict[str, Any]:
        """Run clean_logic method and return cleaned data."""
        self.clean_logic()
        return self.cleaned_data

    def save(self, commit: bool = True):
        try:
            service = self.get_logic_instance(self.cleaned_data)
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        return self.instance


class SelectReviewerForm(BaseInviteSelectReviewerForm, forms.ModelForm):
    reviewer = forms.ModelChoiceField(
        label=_("Reviewer"), queryset=Account.objects.none(), widget=forms.HiddenInput, required=False
    )
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self._today = now().date()
        self.editor_assigns_themselves_as_reviewer = kwargs.pop("editor_assigns_themselves_as_reviewer", False)
        self.revision = kwargs.pop("revision")
        super().__init__(*args, **kwargs)

        review_round = self.instance.article.current_review_round()

        has_cover_letter = self.revision.cover_letter.text or self.revision.cover_letter.file
        if review_round == 1 and not has_cover_letter:
            self.fields["author_note_visible"].widget = forms.HiddenInput()

        if self.editor_assigns_themselves_as_reviewer:
            self.fields["acceptance_due_date"].label = _("I will send my review by")
            self.default_acceptance_due_date = self._today + datetime.timedelta(
                days=self.request.journal.get_setting("general", "default_review_days"),
            )
            self.date_min = self._today
            self.date_max = None
            date_attrs = {
                "type": "date",
                "value": self.default_acceptance_due_date,
                "min": self.date_min,
            }
            self.fields["acceptance_due_date"].widget = forms.DateInput(attrs=date_attrs)
            self.fields["message_subject"].widget = forms.HiddenInput()
            self.fields["message"].widget = forms.HiddenInput()
            self.fields["author_note_visible"].widget = forms.HiddenInput()
        else:
            # we can load default data
            self.fields["message"].required = True
            self.fields["reviewer"].required = True
            self._prepare_message_and_subject()
        self.fields["reviewer"].queryset = Account.objects.get_reviewers_choices(self.instance)

    def get_message_context(self) -> Dict[str, Any]:
        """
        Return a dictionary with the context to render the default form message.

        The context is generated using AssignToReviewer._get_message_context method.

        Reviewer is a fake Account instance, as we don't have one yet: we only need its id to render the message.
        WorkflowReviewAssignment is a fake WorkflowReviewAssignment instance, as we don't have one yet.
        """
        form_data = self.data.copy()
        if reviewer_id := form_data.get("reviewer", False):
            form_data["reviewer"] = Account.objects.get(id=reviewer_id)
        else:
            form_data["reviewer"] = None
        logic = self.get_logic_instance(form_data)
        logic.assignment = WorkflowReviewAssignment(id=1, access_code="sample")
        return logic._get_message_context()

    def clean_reviewer(self):
        """
        Validate the reviewer.

        A reviewer must not be any of the authors linked to the article being reviewed.
        """
        reviewer = self.cleaned_data["reviewer"]
        if not AssignToReviewer.check_reviewer_conditions(self.instance, reviewer):
            raise forms.ValidationError("A reviewer must not be an author of the article")
        return reviewer

    def clean(self) -> Dict[str, Any]:
        cleaned_data = super().clean()
        if self.editor_assigns_themselves_as_reviewer:
            cleaned_data["reviewer"] = self.user
        return cleaned_data

    def get_logic_instance(self, cleaned_data: Dict[str, Any]) -> AssignToReviewer:
        """Instantiate :py:class:`AssignToReviewer` class.

        The logic class will be used during the form's save to check for logic-related errors.
        """
        return AssignToReviewer(
            reviewer=cleaned_data["reviewer"],
            workflow=self.instance,
            editor=self.user,
            form_data={
                "acceptance_due_date": cleaned_data.get("acceptance_due_date", None),
                "message": cleaned_data.get("message", ""),
                "author_note_visible": cleaned_data.get("author_note_visible", False),
                "reviewer": cleaned_data.get("reviewer", False),
            },
            request=self.request,
        )


class InviteUserForm(BaseInviteSelectReviewerForm):
    """Used by staff to invite external users for review activities."""

    first_name = forms.CharField(label=_("First name"))
    last_name = forms.CharField(label=_("Last name"))
    suffix = forms.CharField(widget=forms.HiddenInput(), required=False)
    email = forms.EmailField(label=_("Email"))

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance")
        self.prophy_account = None
        if "prophy_account_id" in kwargs:
            # FIXME: breaks badly if filter returns an empty queryset
            prophy_account = ProphyAccount.objects.filter(author_id=kwargs.pop("prophy_account_id"))[0]
            # Cleanup None values where they could be
            if prophy_account.middle_name in [None, "None"]:
                prophy_account.middle_name = ""
            if prophy_account.suffix in [None, "None"]:
                prophy_account.suffix = ""
            # Set the attribute full_name, that could be used in the template of the initial message
            # (adapted from Account.full_name())
            prophy_account.full_name = " ".join(
                [
                    piece
                    for piece in [
                        prophy_account.first_name,
                        prophy_account.middle_name,
                        prophy_account.last_name,
                        prophy_account.suffix,
                    ]
                    if piece
                ]
            )
            self.prophy_account = prophy_account

        super().__init__(*args, **kwargs)
        if self.prophy_account:
            self.initial = {
                # Add .strip() to remove trailing spaces in case of empty middle_name
                "first_name": f"{prophy_account.first_name} {prophy_account.middle_name}".strip(),
                "last_name": prophy_account.last_name,
                "suffix": prophy_account.suffix,
                "email": prophy_account.email,
            }
        if not self.instance.article.comments_editor:
            self.fields["author_note_visible"].widget = forms.HiddenInput()

        self._prepare_message_and_subject()

    def get_message_context(self):
        # TODO: refactor message-preview code!
        # A behavior similar to what we do here can be found in:
        # - SelectReviewerForm.get_message_context
        # - logic.AssignReviewer._get_message_context

        # Ensure that acceptance_due_date is a date object
        # (otherwise it won't be rendered in the template)
        if acceptance_due_date := self.data.get("acceptance_due_date", ""):
            if isinstance(acceptance_due_date, str):
                acceptance_due_date = parse(acceptance_due_date).date()
        else:
            acceptance_due_date = ""
        return {
            "article": self.instance.article,
            "reviewer": self.prophy_account,
            "review_assignment": WorkflowReviewAssignment(id=1, access_code="sample", article=self.instance.article),
            "user_message_content": self.data.get("message", ""),
            "acceptance_due_date": acceptance_due_date,
        }

    def get_logic_instance(self, cleaned_data: Dict[str, Any]) -> InviteReviewer:
        """Instantiate :py:class:`InviteReviewer` class.

        The logic class will be used during the form's save to check for logic-related errors.
        """
        # TBV: are we calling the correct logic class???
        service = InviteReviewer(
            workflow=self.instance,
            editor=self.user,
            form_data=cleaned_data,
            request=self.request,
        )
        return service


class ReviewerSearchForm(forms.Form):
    search = forms.CharField(required=False, label=_("Name"))
    user_type = forms.ChoiceField(
        required=False,
        choices=[
            ("", "---"),
            ("all", _("All")),
            ("past", _("Reviewed previous version")),
            ("known", _("My reviewer archive")),
            ("declined", _("Declined/removed from previous version")),
        ],
    )


class DeclineReviewForm(forms.Form):
    additional_comments = WjsMiniHTMLFormField(
        label=_("Additional comments"),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.instance = kwargs.pop("instance")
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("additional_comments"):
            self.add_error("additional_comments", _("Please provide a reason for declining"))
        cleaned_data["reviewer_decision"] = "0"
        return cleaned_data

    def get_logic_instance(self) -> EvaluateReview:
        """Instantiate :py:class:`EvaluateReview` class."""
        service = EvaluateReview(
            assignment=self.instance,
            reviewer=self.instance.reviewer,
            editor=self.instance.editor,
            form_data=self.cleaned_data,
            request=self.request,
            token="",
        )
        return service

    def save(self, commit: bool = True) -> ReviewAssignment:
        """
        Change the state of the review using :py:class:`EvaluateReview`.

        Errors are added to the form if the logic fails.
        """
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class EvaluateReviewForm(forms.ModelForm):
    reviewer_decision = forms.ChoiceField(
        choices=(("1", _("Accept")), ("0", _("Reject")), ("2", _("Update"))),
        required=True,
    )
    # refs https://gitlab.sissamedialab.it/wjs/specs/-/issues/1159
    # To remove the placeholder, add `placeholder=""` to the {% bootstrap_field %} tag in the template
    additional_comments = WjsMiniHTMLFormField(
        label=_("Additional comments for the editor-in-charge"),
        required=False,
    )
    accept_gdpr = forms.BooleanField(required=False)
    # https://docs.djangoproject.com/en/3.2/ref/forms/widgets/#dateinput
    # By default DateInput is an <input type="text">
    date_due = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("If you accept the invite, your review is expected by"),
    )

    class Meta:
        model = ReviewAssignment
        fields = ["reviewer_decision", "comments_for_editor", "date_due"]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.token = kwargs.pop("token")
        super().__init__(*args, **kwargs)
        privacy_policy_url = self.instance.article.journal.get_setting(
            group_name="general",
            setting_name="privacy_policy_url",
        )
        self.fields["accept_gdpr"].label = mark_safe(
            _('I acknowledge the <a href="{url}" target="_blank">privacy policy</a>').format(url=privacy_policy_url)
        )
        if self.instance.reviewer.jcomprofile.gdpr_checkbox:
            self.fields["accept_gdpr"].widget = forms.HiddenInput()
        if self.instance.date_accepted:
            self.fields["reviewer_decision"].required = False
        if self.instance.date_due:
            self.fields["date_due"].widget.attrs["min"] = self.instance.date_due
        default_review_days = self.instance.article.journal.get_setting(
            group_name="general",
            setting_name="default_review_days",
        )
        self.initial["date_due"] = now().date() + datetime.timedelta(days=default_review_days)

    def clean_date_due(self):
        date_due = self.cleaned_data.get("date_due", None)
        if date_due and date_due < self.instance.date_due:
            raise forms.ValidationError(_("Date must be in the future"))
        return date_due

    def clean(self):
        cleaned_data = super().clean()
        # Decision is optional if form is submitted when submitting a report
        if cleaned_data.get("reviewer_decision", None):
            if cleaned_data["reviewer_decision"] == "0" and not cleaned_data["additional_comments"]:
                self.add_error("additional_comments", _("Please provide a reason for declining"))
            elif cleaned_data["reviewer_decision"] == "0" and cleaned_data["additional_comments"]:
                # we use comments_for_editor to store the additional_comments if the user has declined, or as cover
                # letter if the user submits a report. As decline reason is less important we use an alias field
                cleaned_data["comments_for_editor"] = cleaned_data["additional_comments"]
            if (
                cleaned_data["reviewer_decision"] == "1"
                and not self.instance.reviewer.jcomprofile.gdpr_checkbox
                and not cleaned_data["accept_gdpr"]
            ):
                self.add_error("accept_gdpr", _("Please acknowledge the privacy policy to continue"))
        return cleaned_data

    def get_logic_instance(self) -> EvaluateReview:
        """Instantiate :py:class:`EvaluateReview` class."""
        service = EvaluateReview(
            assignment=self.instance,
            reviewer=self.instance.reviewer,
            editor=self.instance.editor,
            form_data=self.cleaned_data,
            request=self.request,
            token=self.token,
        )
        return service

    def save(self, commit: bool = True) -> ReviewAssignment:
        """
        Change the state of the review using :py:class:`EvaluateReview`.

        Errors are added to the form if the logic fails.
        """
        # Also save the gdpr-flag. This may be needed when a reviewer is using the access_code.
        if "accept_gdpr" in self.changed_data:
            self.instance.reviewer.jcomprofile.gdpr_checkbox = self.cleaned_data["accept_gdpr"]
            self.instance.reviewer.jcomprofile.save()

        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class RichTextGeneratedForm(GeneratedForm):
    """Extends GeneratedForm to use SummernoteWidget for textarea fields."""

    def __init__(self, *args, **kwargs):
        answer = kwargs.get("answer", None)
        preview = kwargs.get("preview", None)
        self.request = kwargs.pop("request", None)
        self.instance = kwargs.get("review_assignment", None)
        super().__init__(*args, **kwargs)

        elements = self.get_elements(answer=answer, preview=preview, review_assignment=self.instance)
        for element in elements:
            if element.kind == "textarea":
                self.fields[str(element.pk)] = WjsMiniHTMLFormField()  # FIXME: this class is to be deprecated anyway

    def get_elements(
        self,
        answer: Optional[ReviewAssignmentAnswer] = None,
        preview: Optional[ReviewForm] = None,
        review_assignment: Optional[ReviewAssignment] = None,
    ) -> Iterable[ReviewFormElement]:
        """
        Return the elements to be used in the form.

        This is a duplication of the same code used in original GeneratedForm, but we can't reuse upstream, and it's
        more efficient than just retrieving the elements from the database again by looping on the form fields.
        """
        if answer:
            return [answer.element]
        elif preview:
            return preview.elements.all()
        else:
            return review_assignment.form.elements.all()


class ReportForm(RichTextGeneratedForm):
    def __init__(self, *args, **kwargs):
        self.submit_final = kwargs.pop("submit_final", None)
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> SubmitReview:
        """Instantiate :py:class:`SubmitReview` class."""
        service = SubmitReview(
            assignment=self.instance,
            form=self,
            submit_final=self.submit_final,
            request=self.request,
        )
        return service

    def save(self, commit: bool = True) -> ReviewAssignment:
        """
        Change the state of the review using :py:class:`SubmitReview`.

        Errors are added to the form if the logic fails.
        """
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class DecisionForm(forms.ModelForm):
    decision = forms.ChoiceField(
        label=_("Decision"),
        choices=ArticleWorkflow.Decisions.decision_choices,
        required=True,
    )
    decision_editor_report = WjsMiniHTMLFormField(
        label=_("Editor Report for authors"),
        required=False,
    )
    withdraw_notice = WjsMiniHTMLFormField(
        label=_("Courtesy notes for reviewers who did not send review"), required=False
    )
    date_due = forms.DateField(
        label=_("Revision due date"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.today = now().date()
        self.user = kwargs.pop("user", None)
        self.request = kwargs.pop("request", None)
        self.admin_form = kwargs.pop("admin_form", False)
        self.date_due_max = kwargs.pop("date_due_max", None)
        self.revision_days_max = kwargs.pop("revision_days_max", None)
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        self.hide_date_due = kwargs["initial"].get("decision", None) not in (
            ArticleWorkflow.Decisions.MINOR_REVISION,
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.OPEN_APPEAL,
        )
        self.hide_decision = kwargs["initial"].get("decision", None)
        self.has_pending_reviews = kwargs.pop("has_pending_reviews", False)

        # It's easier to set initial here, even if we might drop the field later on,
        # because kwargs is going to be passed to super().__init__() for standard initialization.
        #
        # The message template needs {{ article }} to be set in order to get the journal, so we need to pass a dummy
        # article object.
        stub_article = Article()
        stub_article.journal = self.request.journal
        kwargs["initial"]["withdraw_notice"] = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_withdrawl",
            journal=self.request.journal,
            request=self.request,
            context={
                "article": stub_article,
                "request": self.request,
            },
            template_is_setting=True,
        )

        super().__init__(*args, **kwargs)
        if self.admin_form:
            del self.fields["withdraw_notice"]
        elif not self.has_pending_reviews:
            del self.fields["withdraw_notice"]
        reviewer_report_type = get_setting(
            setting_group_name="wjs_review", setting_name="reviewer_report_type", journal=self.request.journal
        ).value
        if "tex" in reviewer_report_type:
            self.fields["decision_editor_report"] = CharField(
                label=_("Editor Report for authors"),
                required=False,
            )
            self.fields["decision_editor_report"].widget = forms.Textarea()
        if kwargs["initial"].get("decision", None) == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
            del self.fields["decision_editor_report"]
            if "withdraw_notice" in self.fields:
                del self.fields["withdraw_notice"]
        self.fields["date_due"].widget.attrs["min"] = self.today
        if self.date_due_max:
            self.fields["date_due"].widget.attrs["max"] = self.date_due_max
        if not self.hide_decision:
            self.fields["decision"].choices = [
                choice
                for choice in self.fields["decision"].choices
                if choice[0] != ArticleWorkflow.Decisions.TECHNICAL_REVISION
            ]

    def _clean_date_due_depending_on_decision(self):
        """Validate the due date with respect to the decision."""
        # Please note that this method is not called automagically by django form framework,
        # but manually by the form's generic `clean` method below because the validation inside
        # depends on another field value, and as a Django best practice this has to be done in the
        # main form's clean() method, not in the clean_fieldname() one.
        date_due = self.cleaned_data["date_due"]
        decision = self.cleaned_data["decision"]
        if (
            decision
            in (
                ArticleWorkflow.Decisions.MINOR_REVISION,
                ArticleWorkflow.Decisions.MAJOR_REVISION,
                ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            )
            and not date_due
        ):
            self.add_error("date_due", _("Please provide a date due for author to submit a revision"))
        if date_due and date_due < self.today:
            self.add_error("date_due", _("Date must be in the future"))
        if decision in (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.Decisions.MAJOR_REVISION):
            if date_due and self.date_due_max and self.revision_days_max and (date_due > self.date_due_max):
                self.add_error(
                    "date_due", _("Date must be less than {} days in the future").format(self.revision_days_max)
                )
        return date_due

    def _get_review_files_pks(self):
        send_review_file_pks = []

        for key, value in self.data.items():
            if key.startswith("send_review_file_"):
                review_pk = key.split("_")[-1]
                send_review_file_pks.append((review_pk, value))
        return send_review_file_pks

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["date_due"] = self._clean_date_due_depending_on_decision()
        send_review_file_pks = self._get_review_files_pks()
        cleaned_data["send_review_file"] = send_review_file_pks
        for pk in send_review_file_pks:
            if WorkflowReviewAssignment.objects.get(pk=pk) not in WorkflowReviewAssignment.objects.completed().filter(
                article=self.instance.article
            ):
                raise forms.ValidationError(_("Form data was compromised"))
        return cleaned_data

    def get_logic_instance(self) -> HandleDecision:
        """Instantiate :py:class:`EvaluateReview` class."""
        service = HandleDecision(
            workflow=self.instance,
            form_data=self.cleaned_data,
            user=self.user,
            request=self.request,
            admin_form=self.admin_form,
        )
        return service

    def save(self, commit: bool = True) -> ArticleWorkflow:
        """
        Apply the editor decision using :py:class:`logic.HandleDecision`.

        Any ValueError/ValidationError raised by the logic is left to propagate: the calling
        view's form_valid() catches it and adds it to the form as a non-field error (rendered
        on the decision template). Handling it here as well would duplicate the message.
        """
        service = self.get_logic_instance()
        service.run()
        self.instance.refresh_from_db()
        return self.instance


class UploadArticleForm(forms.Form):
    file_type = forms.ChoiceField(
        label=_("File type"), choices=(("manuscript", _("Manuscript")), ("data", _("Data/Figure"))), required=False
    )
    label = forms.CharField(label=_("File label"), widget=forms.TextInput(attrs={"placeholder": "Label"}))
    file = forms.FileField(label=_("Source file"), widget=forms.FileInput())
    next_location = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        self.file_type = kwargs.pop("file_type", "")
        self.instance: EditorRevisionRequest = kwargs.pop("instance")
        self.user = kwargs.pop("user")
        self.original_file = kwargs.pop("original_file", None)
        self.new_file = None
        super().__init__(*args, **kwargs)
        if self.file_type:
            self.fields["file_type"].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["file_type"] = self.file_type
        return cleaned_data

    def save(self, commit: bool = True) -> File:
        uploaded_file = self.cleaned_data["file"]
        label = self.cleaned_data["label"]
        file_type = self.cleaned_data["file_type"]
        article = self.instance.article
        if file_type in ["manuscript", "data", "cover_letter"]:
            new_file = files.save_file_to_article(
                uploaded_file,
                article,
                self.user,
                label=label,
            )
            if file_type == "manuscript":
                if self.original_file:
                    # unlink the original file from manytomany, file object is not modified as it might be referenced
                    # elsewhere
                    article.manuscript_files.remove(self.original_file)
                article.manuscript_files.set([new_file])
            if file_type == "data":
                if self.original_file:
                    # unlink the original file from manytomany, file object is not modified as it might be referenced
                    # elsewhere
                    article.data_figure_files.remove(self.original_file)
                article.data_figure_files.add(new_file)
            if file_type == "cover_letter":
                if self.instance.cover_letter_file:
                    # unlink the existing file because it should not be referenced anywhere else
                    self.instance.cover_letter_file.delete()
                self.instance.cover_letter_file = new_file
                self.instance.save()
            self.new_file = new_file
        else:
            logger.error(f"Unknown file type '{file_type}' while {self.user} was uploading revision {self.instance}.")
        return self.instance


class BaseEditorRevisionRequestEditForm(ConfirmableForm, forms.ModelForm):
    # Author notes (aka cover letter text) are not required, because the
    # "cover letter" can be the text or the cover letter file or both.
    # Subclasses will check that this condition is honored.
    author_note = WjsMiniHTMLFormField(label=_("Author notes"), required=False)

    class Meta:
        model = EditorRevisionRequest
        fields = ["author_note"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.request = kwargs.pop("request", None)
        self.save_cover_letter = kwargs.pop("save_cover_letter", None)
        self.confirm_previous_version = kwargs.pop("confirm_previous_version", None)
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> AuthorHandleRevisionObsolete:
        """Instantiate :py:class:`AuthorHandleRevision` class."""
        service = AuthorHandleRevisionObsolete(
            revision=self.instance,
            form_data=self.cleaned_data,
            user=self.user,
            request=self.request,
        )
        return service

    def finish(self) -> EditorRevisionRequest:
        """
        Change the state of the review using :py:class:`AuthorHandleRevision`.

        Errors are added to the form if the logic fails.
        """
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class EditMetadataForm(BaseEditorRevisionRequestEditForm):
    confirm_cover_metadata = forms.BooleanField(
        label=_(
            "If I have modified title and/or abstract, I will take care of updating them in my preprint file as soon "
            "as possible. Either in a revised version or during the stage of proofreading "
            "(if my preprint is accepted for publication)."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.save_cover_letter:
            self.fields["confirm_cover_metadata"].required = False

    def check_for_potential_errors(self):
        """Check if the user has confirmed all the required fields."""
        errors = []
        if not self.cleaned_data.get("confirm_cover_metadata", False):
            errors.append(_("You must confirm that the cover letter lists and describes the changes."))
        if not self.instance.author_note and not self.instance.cover_letter_file:
            errors.append(_("You should provide and save a cover letter."))
        return errors


class EditorRevisionRequestEditForm(BaseEditorRevisionRequestEditForm):
    confirm_title = forms.BooleanField(
        label=_(
            "I confirm that title and abstract on this web page correspond to those written in the preprint file."
        ),
    )
    confirm_styles = forms.BooleanField(
        label=_(
            "I confirm that this resubmission fulfills the stylistic guidelines of the Journal and its ethical policy "
            "in all its aspects including use of Al, authorship, etc."
        ),
    )
    confirm_blind = forms.BooleanField(
        label=_("I confirm that the file does not contain any author information and has line numbering."),
    )
    confirm_cover = forms.BooleanField(
        label=_(
            "I confirm that the cover letter lists and describes clearly the changes implemented in the preprint "
            "and motivates any modifications that have not been made."
        ),
    )

    class Meta:
        model = EditorRevisionRequest
        fields = ["author_note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.save_cover_letter:
            self.fields["confirm_title"].required = False
            self.fields["confirm_styles"].required = False
            self.fields["confirm_blind"].required = False
            self.fields["confirm_cover"].required = False

    def check_for_potential_errors(self):
        """Check if the user has confirmed all the required fields."""
        errors = []
        if not self.cleaned_data.get("confirm_title", False):
            errors.append(_("You must confirm that the title and abstract correspond to the preprint file."))
        if not self.cleaned_data.get("confirm_styles", False):
            errors.append(_("You must confirm that the resubmission fulfills the stylistic guidelines."))
        if not self.cleaned_data.get("confirm_blind", False):
            errors.append(_("You must confirm that the file does not contain any author information."))
        if not self.cleaned_data.get("confirm_cover", False):
            errors.append(_("You must confirm that the cover letter lists and describes the changes."))
        if not self.instance.author_note and not self.instance.cover_letter_file:
            errors.append(_("You should provide and save a cover letter."))
        if not self.instance.article.manuscript_files.exists() or not self.instance.has_changed_manuscript_files:
            errors.append(_("You must provide a manuscript."))
        return errors


class MessageRecipientForm(forms.Form):
    """Helper form to collect a message recipients.

    This will be the base for an inline form.
    """

    recipient = forms.ModelChoiceField(
        queryset=None,
        widget=forms.widgets.Select(attrs={"class": "rounded-0 rounded-start"}),
    )

    def __init__(self, *args, **kwargs):
        """Set the queryset for the recipient."""
        actor = kwargs.pop("actor")
        article = kwargs.pop("article")
        super().__init__(*args, **kwargs)
        allowed_recipients = HandleMessage.allowed_recipients_for_actor(
            actor=actor,
            article=article,
        )
        self.fields["recipient"].queryset = allowed_recipients
        # Use logic__visibility to hide the name of the recipient if necessary:
        self.fields["recipient"].label_from_instance = lambda obj: get_recipient_label(
            workflow=article.articleworkflow,
            user=actor,
            recipient=obj,
            with_role=True,
        )


class MessageForm(forms.ModelForm):
    attachment = forms.FileField(required=False, label=_("Optional attachment"))
    recipients = forms.ModelMultipleChoiceField(queryset=None, required=True, widget=forms.widgets.HiddenInput())
    subject = forms.CharField(label=_("Title"))

    class Meta:
        model = Message
        fields = [
            "subject",
            "body",
            "actor",
            "content_type",
            "object_id",
            "message_type",
            "to_be_forwarded_to",
        ]
        widgets = {
            "subject": forms.TextInput(),
            "actor": forms.widgets.HiddenInput(),
            "content_type": forms.widgets.HiddenInput(),
            "object_id": forms.widgets.HiddenInput(),
            "message_type": forms.widgets.HiddenInput(),
            "to_be_forwarded_to": forms.widgets.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        """Set subject and body as required and store actor and target gotten from the view."""
        self.actor = kwargs.pop("actor")
        self.target = kwargs.pop("target")
        self.note = kwargs.pop("note", False)
        self.hide_recipients = kwargs.pop("hide_recipients", False)
        self.current_note = kwargs.pop("current_note", None)
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.actor = self.instance.actor
        self.fields["subject"].required = True
        self.fields["body"].required = True
        self.fields["actor"].required = False
        self.fields["content_type"].required = False
        self.fields["object_id"].required = False
        self.fields["message_type"].required = False
        self.fields["recipients"].queryset = self._get_allowed_recipients()  # used at validation
        if self.hide_recipients:
            self.fields["recipients"].widget = forms.widgets.HiddenInput()
        if self.note:
            # The recipients of a personal note is only the actor;
            # it is automatically set during clean()
            self.fields["recipients"].required = False
            self.fields["subject"].required = False
        initial_recipients = []
        if self.initial.get("recipients"):
            initial_recipients = [{"recipient": recipient} for recipient in self.initial["recipients"]]
        self.recipients_formset = self.get_formset_class()(
            prefix="recipientsFS",
            form_kwargs={
                "actor": self.actor,
                "article": self.target,
            },
            initial=initial_recipients,
        )

    @classmethod
    def get_formset_class(cls):
        return formset_factory(
            MessageRecipientForm,
            can_delete=True,
            min_num=1,
            extra=0,
        )

    def _get_allowed_recipients(self):
        """
        Use a logic class to return a queryset of allowed recipients for the current actor/article combination.

        It only applies if :py:attr:`hide_recipients` is False. If it's True, we dont' apply any restrictions
        as it means recipient is forced by the system.
        """
        # TODO: see the note about refactoring this part in HandleMessage code
        if self.hide_recipients:
            return Account.objects.all()
        allowed_recipients = HandleMessage.allowed_recipients_for_actor(actor=self.actor, article=self.target)
        return allowed_recipients

    def clean(self):
        """Ignore what's coming from the web form and use what the view provided.

        This should prevent any tampering of these fields.

        These fields (actor, content_type, object_id, message_type) are in Meta.fields, because keeping them there
        ensures that they are managed during save().

        """
        clean_data = self.cleaned_data
        clean_data["actor"] = self.actor
        clean_data["content_type"] = ContentType.objects.get_for_model(self.target)
        clean_data["object_id"] = self.target.pk
        if self.initial.get("to_be_forwarded_to"):
            # to_be_forwarded_to cannot be customized by the user, so we always inject the initial value
            clean_data["to_be_forwarded_to"] = self.initial["to_be_forwarded_to"]
        if self.hide_recipients:
            clean_data["recipients"] = self.initial["recipients"]
        if self.note:
            clean_data["message_type"] = Message.MessageTypes.NOTE
            clean_data["recipients"] = [self.actor]
        else:
            clean_data["message_type"] = Message.MessageTypes.USER
        return clean_data

    # TODO: IMPORTANT: enforce security:
    def save(self, commit: bool = True) -> Message:
        """Set the logged-in user as actor for this message and save.

        TODO: at the moment only attachments related to Article are managed! I.e. attachments for messages not related
        to a specific article are not managed.
        """
        with transaction.atomic():
            if self.current_note and self.current_note.attachments.all().first() and self.cleaned_data["attachment"]:
                self.current_note.attachments.all().first().delete()
            instance: Message = super().save()
            instance.recipients.set(self.cleaned_data["recipients"])

            if self.note:
                # All personal notes are considered "read"
                # ATM (24W11) personal notes only have _one_ recipient (the actor), but this way
                # we allow for future changes (for instance, if EO want to share notes with typ)
                MessageRecipients.objects.filter(message=instance).update(read=True)
            if has_eo_role(self.actor):
                instance.read_by_eo = True
                instance.save()

            # Message to eo_user should have read and read-by-eo flags coherent.
            # See also ToggleMessageReadByEOForm.save()
            MessageRecipients.objects.filter(
                message=instance,
                recipient=get_eo_user(instance.target),
            ).update(read=instance.read_by_eo)

            if self.cleaned_data["attachment"]:
                if instance.content_type.model_class() != Article:
                    # TODO: where do we save attachements of messages not related to articles?
                    # flat structure? "user files" (e.g. files/users/ID/uuid.ext)?
                    raise ValidationError("Unhandled type. Please go back and try again.")

                target: Article = get_object_or_404(Article, id=instance.object_id)
                attachment: core_models.File = core_files.save_file_to_article(
                    file_to_handle=self.cleaned_data["attachment"],
                    article=target,
                    owner=instance.actor,
                    label=None,  # TODO: TBD: no label (default)
                    description=None,  # TODO: TBD: no description (default)
                )
                instance.attachments.add(attachment)
            instance.emit_notification()

            # -- Materialized AC updates --
            # New message: the recipients that are left with an unread message
            # get HAS_UNREAD_MESSAGE (notes are excluded, since they are always
            # flagged as read above).
            ac_service.sync_unread_message_acs_for_message(instance)

        return instance


# ---------------------------------------------------------------------------
# AC updates for HAS_UNREAD_MESSAGE are handled inline in save().
#
# TODO (New Issue 6, 260318-SISSA-Specifications-for-attention-conditions.md):
#   Refactor to use a logic class for message read-status updates, following
#   the pattern used by HandleMessage. This would centralize AC updates.
# ---------------------------------------------------------------------------


class ToggleMessageReadForm(forms.ModelForm):

    class Meta:
        model = MessageRecipients
        fields = ["read"]

    def save(self, commit: bool = True) -> MessageRecipients:
        """Toggle read status and re-evaluate HAS_UNREAD_MESSAGE for the user."""
        instance = super().save(commit=commit)
        # Re-evaluate HAS_UNREAD_MESSAGE: the AC is resolved only if no message
        # is left unread for this recipient, and comes back if the message is
        # flagged as unread again.
        if isinstance(instance.message.target, Article):
            ac_service.sync_unread_message_ac(instance.message.target, instance.recipient)
        return instance


class ToggleMessageReadByEOForm(forms.ModelForm):

    class Meta:
        model = Message
        fields = ["read_by_eo"]

    def save(self, commit: bool = True) -> Message:
        """Sync read-by-eo and recipient-read flags if necessary."""
        # See also communication_utils.log_operation()
        # and forms.MessageForm.save()
        instance = super().save(commit=commit)
        MessageRecipients.objects.filter(
            message=instance,
            recipient=get_eo_user(instance.target),
        ).update(read=instance.read_by_eo)

        # -- Materialized AC updates --
        # Toggling read-by-eo changes what the editorial office has left to read
        # on this article: re-evaluate its HAS_UNREAD_MESSAGE (the AC comes back
        # if the message is flagged as unread again).
        if isinstance(instance.target, Article):
            ac_service.sync_unread_message_ac_for_eo(instance.target)

        return self.instance


class UpdateReviewerDueDateForm(forms.ModelForm):
    date_due = forms.DateField(label=_("Date due"), required=True, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = WorkflowReviewAssignment
        fields = ["date_due"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)
        if self.instance.date_accepted:
            self.fields["date_due"].label = _("Review due date")
        else:
            self.fields["date_due"].label = _("Accept/decline due date")
        self._original_date = self.instance.date_due

    def clean(self):
        """
        Allow only dates in the future
        """
        cleaned_data = super().clean()
        date_due = cleaned_data.get("date_due")
        if date_due and date_due <= now().date():
            raise ValidationError(_("The due date must be in the future."))
        return cleaned_data

    def get_logic_instance(self) -> PostponeReviewerDueDate:
        """Instantiate :py:class:`PostponeReviewerReportDueDate` class."""
        service = PostponeReviewerDueDate(
            assignment=self.instance,
            editor=self.instance.editor,
            user=self.user,
            form_data=self.cleaned_data,
            request=self.request,
            original_due_date=self._original_date,
        )
        return service

    def save(self, commit=True) -> ReviewAssignment:
        """Change the reviewer report due date using :py:class:`PostponeReviewerReportDueDate`."""
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class EditorRevisionRequestDueDateForm(forms.ModelForm):
    date_due = forms.DateField(label=_("Due date"), required=True, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = EditorRevisionRequest
        fields = ["date_due"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)
        self._original_date = self.instance.date_due

    def clean_date_due(self):
        date_due = self.cleaned_data["date_due"]
        if date_due and date_due < now().date():
            raise forms.ValidationError(_("Date must be in the future"))
        return date_due

    def get_logic_instance(self) -> PostponeRevisionRequestDueDate:
        service = PostponeRevisionRequestDueDate(
            revision_request=self.instance,
            form_data=self.cleaned_data,
            request=self.request,
            original_due_date=self._original_date,
        )
        return service

    def save(self, commit: bool = True) -> EditorRevisionRequest:
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class AssignEoForm(forms.ModelForm):
    eo_in_charge = forms.ModelChoiceField(queryset=Account.objects.filter(groups__name=EO_GROUP), required=True)

    class Meta:
        model = ArticleWorkflow
        fields = ["eo_in_charge"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

    def _log_eo_if_eo_assigned(self):
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="eo_assignment_subject",
            journal=self.instance.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="eo_assignment_body",
            journal=self.instance.article.journal,
            request=self.request,
            context={
                "article": self.instance.article,
                "eo": self.instance.eo_in_charge,
            },
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.instance.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.FULL,
            recipients=[self.instance.eo_in_charge],
        )

    def clean(self):
        cleaned_data = super().clean()
        if not base_permissions.has_eo_role(cleaned_data["eo_in_charge"]):
            raise forms.ValidationError(_("Selected user must be part of EO."))
        if not base_permissions.has_eo_role(self.user):
            raise forms.ValidationError(_("Executing users must be part of EO."))
        return cleaned_data

    def save(self, commit: bool = True):
        super().save(commit)
        self._log_eo_if_eo_assigned()
        return self.instance


class DeselectReviewerForm(forms.Form):
    notification_subject = forms.CharField(label=_("Subject"))
    notification_body = WjsMiniHTMLFormField(label=_("Body"))
    send_notification = forms.BooleanField(label=_("Send notification to the reviewer"), required=False, initial=True)

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.user = kwargs.pop("user")
        self.instance = kwargs.pop("instance")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> DeselectReviewer:
        """Instantiate :py:class:`DeselectReviewer` class."""
        return DeselectReviewer(
            assignment=self.instance,
            actor=self.user,
            send_reviewer_notification=self.cleaned_data["send_notification"],
            request=self.request,
            form_data=self.data,
        )

    def save(self, commit=True):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class SupervisorAssignEditorForm(forms.ModelForm):
    selected_editor = forms.ModelChoiceField(queryset=Account.objects.none(), required=True, initial=None)
    state = forms.CharField(widget=forms.HiddenInput(), required=False)
    note_for_past_editor_subject = forms.CharField(required=False, disabled=True, label=_("Subject"))
    note_for_past_editor = WjsMiniHTMLFormField(
        label=_("Body"),
        required=True,
    )
    note_for_new_editor_subject = forms.CharField(required=False, disabled=True, label=_("Subject"))
    note_for_new_editor = WjsMiniHTMLFormField(
        label=_("Body"),
        required=True,
    )
    search = forms.CharField(required=False, label=_("Search..."))
    set_visibility_rights = forms.BooleanField(initial=False, required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        self.editors = kwargs.pop("selectable_editors")
        self.selected_editor = kwargs.pop("selected_editor", None)
        super().__init__(*args, **kwargs)
        self.fields["selected_editor"].queryset = self.editors

        try:
            assignment = WjsEditorAssignment.objects.get_current(self.instance)
        except WjsEditorAssignment.DoesNotExist:
            self.current_editor_assignment = None
            self.fields["note_for_past_editor"].required = False
            self.fields["note_for_past_editor_subject"].required = False
        else:
            self.current_editor_assignment = assignment
        if not self.data.get("note_for_new_editor", None):
            self.fields["note_for_new_editor"].initial = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="editor_assignment_manual_body",
                journal=self.instance.article.journal,
                request=self.request,
                context=self.get_new_editor_message_context(),
                template_is_setting=True,
            )
            self.initial["note_for_new_editor_subject"] = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="editor_assignment_manual_subject",
                journal=self.instance.article.journal,
                request=self.request,
                context={},
                template_is_setting=True,
            )
        # initialize the note for the old editor with a default message
        if not self.data.get("note_for_past_editor", None):
            self.initial["note_for_past_editor"] = render_template_from_setting(
                setting_group_name="email",
                setting_name="unassign_editor",
                journal=self.instance.article.journal,
                request=self.request,
                context=self.get_old_editor_message_context(),
                template_is_setting=True,
            )
            # subject is fixed; we always render it from the setting;
            # (this also avoids pesky users tampering with it)
            self.initial["note_for_past_editor_subject"] = render_template_from_setting(
                setting_group_name="email_subject",
                setting_name="subject_unassign_editor",
                journal=self.instance.article.journal,
                request=self.request,
                context={},
                template_is_setting=True,
            )

    def get_old_editor_message_context(self):
        """Build a context suitable to render the unassign_editor message."""
        return {
            "editor": self.current_editor_assignment.editor if self.current_editor_assignment else None,
            "article": self.instance.article,
        }

    def get_new_editor_message_context(self):
        """Build a context suitable to render the unassign_editor message."""
        return {
            "editor": self.selected_editor,
            "article": self.instance.article,
        }

    def get_logic_instance(self) -> SupervisorChangeEditorAssignment | AssignToEditor:
        """
        Instantiate :py:class:`AssignToEditor` class or :py:class:`SupervisorChangeEditorAssignment` to handle action.

        If the article has no current assignment, it means the article is being assigned to an editor for the first
        time and we can call plain AssignToEditor service. Otherwise, we call SupervisorChangeEditorAssignment
        that handles existing review assignments as well as re-assigning the article to a new editor and removing the
        previous one.
        """
        try:
            assignment = WjsEditorAssignment.objects.get_current(self.instance)
            return SupervisorChangeEditorAssignment(
                article=self.instance.article,
                assignment=assignment,
                new_editor=self.cleaned_data["selected_editor"],
                request=self.request,
                assignment_message=self.cleaned_data["note_for_new_editor"],
                deassignment_message=self.cleaned_data["note_for_past_editor"],
            )
        except WjsEditorAssignment.DoesNotExist:
            return AssignToEditor(
                editor=self.cleaned_data["selected_editor"],
                article=self.instance.article,
                request=self.request,
                assignment_message=self.cleaned_data["note_for_new_editor"],
            )

    def save(self, commit: bool = True):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance

    @property
    def assign_permissions(self):
        return self.cleaned_data.get("set_visibility_rights", None)


class ForwardMessageForm(forms.ModelForm):
    """Form used by the EO who wants to forward an existing message.

    Usually a message that the typesetter would like to send to the author.
    """

    class Meta:
        model = Message
        fields = ["subject", "body", "attachment"]
        widgets = {"subject": forms.TextInput()}

    attachment = forms.FileField(required=False, label=_("Optional attachment"))

    def __init__(self, *args, **kwargs):
        """Store away data needed for the new message."""
        self.user = kwargs.pop("user")
        self.original_message = kwargs.pop("original_message")
        self.article = self.original_message.target
        self.recipients = [self.original_message.to_be_forwarded_to.pk]
        # Let the view decide who the actor of the new message should be:
        self.actor = kwargs.pop("actor")
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        """Create and send the "moderated" message.

        Create a new message (m2) which is a forward of an original message (m1).

        Use m1.to_be_forwarded_to as recipient for m2.
        Use m1.subject and body as base for m2 (and let the operator edit them).
        """
        with transaction.atomic():
            message = Message.objects.create(
                actor=self.actor,
                message_type=Message.MessageTypes.USER,
                content_type=ContentType.objects.get_for_model(self.article),
                object_id=self.article.pk,
                subject=self.cleaned_data["subject"],
                body=self.cleaned_data["body"],
            )
            message.recipients.set(self.recipients)

            if self.cleaned_data["attachment"]:
                attachment: File = core_files.save_file_to_article(
                    file_to_handle=self.cleaned_data["attachment"],
                    article=self.article,
                    owner=self.actor,
                    label=None,
                    description=None,
                )
                message.attachments.add(attachment)

            message.emit_notification()

            if base_permissions.has_eo_role(self.user):
                message.read_by_eo = True
                message.save()

            MessageThread.objects.create(
                parent_message=self.original_message,
                child_message=message,
                relation_type=MessageThread.MessageRelation.FORWARD,
            )

            # -- Materialized AC updates --
            # The forwarded message is a new message for its recipient.
            ac_service.sync_unread_message_acs_for_message(message)

            return message


class TimelineFilterForm(forms.Form):
    """A form to allow user to filter timeline items.

    Do not confuse with the filter of the messages page
    (see filters.MessageFilter).
    """

    message_type = forms.ChoiceField(
        required=False,
        label=_("Filter by type"),
        choices=(
            (
                "",
                f'<i class="bi {MESSAGE_TYPE_ICONS.get(None)}"></i> {_("All")}',
            ),
            (
                Message.MessageTypes.USER,
                f'<i class="bi {MESSAGE_TYPE_ICONS.get(Message.MessageTypes.USER)}"></i> {_("User messages")}',
            ),
            (
                Message.MessageTypes.NOTE,
                f'<i class="bi {MESSAGE_TYPE_ICONS.get(Message.MessageTypes.NOTE)}"></i> {_("User notes")}',
            ),
            (
                Message.MessageTypes.SYSTEM,
                f'<i class="bi {MESSAGE_TYPE_ICONS.get(Message.MessageTypes.SYSTEM)}"></i> {_("System")}',
            ),
        ),
    )


# ---------------------------------------------------------------------------
# AC updates for MISSING_SOCIAL_MEDIA and MISSING_ENGLISH_CONTENT are handled
# inline in save().
#
# Note: Article.meta_image is also writable via core ArticleMetaImageForm
# (janeway/src/core/forms/forms.py). That path is handled by a monkey-patch
# in apps.py. Django admin is not used in production and is covered by the
# daily rebuild_attention_conditions task.
# ---------------------------------------------------------------------------


class ArticleExtraInformationUpdateForm(forms.ModelForm):
    social_media_image = forms.ImageField(
        required=False,
        label=_("Image for social media - .JPG/.PNG, recommended size 1200x630 pixels (minimum 600x315 pixels)"),
        validators=[min_size_validator(600, 315)],
        widget=forms.ClearableFileInput(
            attrs={"accept": ".png, .jpg, .jpeg"},
        ),
    )
    english_title = forms.CharField(label=_("Article title - English language"))
    english_abstract = WjsMiniHTMLFormField(label=_("Article abstract - English language"))

    class Meta:
        model = ArticleWorkflow
        fields = [
            "social_media_short_description",
        ]

    def __init__(self, *args, **kwargs):
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        if kwargs["instance"]:
            kwargs["initial"]["social_media_image"] = kwargs["instance"].article.meta_image
            # this is always true even if the journal has no english language
            # because models are common to all journals, access to it
            kwargs["initial"]["english_title"] = kwargs["instance"].article.title_en
            kwargs["initial"]["english_abstract"] = kwargs["instance"].article.abstract_en
        super().__init__(*args, **kwargs)

        needs_english = conditions.journal_requires_english_content(self.instance.article.journal)
        is_published_piecemeal = conditions.article_is_published_piecemeal(self.instance)
        self.fields["social_media_short_description"].label = _(
            "Short description for social media - max 250 characters"
        )
        # If no conditions are met, fields list is empty but this is not an issue as at least on condition must be met
        # for the view to be accessible.
        if not needs_english:
            del self.fields["english_title"]
            del self.fields["english_abstract"]
        if not is_published_piecemeal:
            del self.fields["social_media_image"]
            del self.fields["social_media_short_description"]

    def clean_social_media_short_description(self):
        social_media_short_description = self.cleaned_data.get("social_media_short_description")
        if len(social_media_short_description) > 250:
            self.add_error(
                "social_media_short_description",
                (_("Short description for social media must be 250 characters or less")),
            )
        return social_media_short_description

    def save(self, commit=True):
        instance = super().save(commit)
        if self.cleaned_data.get("social_media_image"):
            instance.article.meta_image = self.cleaned_data["social_media_image"]
            instance.article.save()
        if self.data.get("social_media_image-clear") == "on":
            instance.article.meta_image.delete()
            instance.article.meta_image = None
            instance.article.save()
        # this step is entirely skipped if the journal doesn't need english content, so there is no risk to overwrite
        # the original title and abstract
        if self.cleaned_data.get("english_title"):
            instance.article.title_en = self.cleaned_data["english_title"]
            instance.article.abstract_en = self.cleaned_data["english_abstract"]
            instance.article.save()

        # -- Materialized AC updates --
        # Re-evaluate MISSING_SOCIAL_MEDIA and MISSING_ENGLISH_CONTENT after
        # the fields that determine them have been updated.
        article = instance.article
        if article.articleworkflow.state == "ReadyForPublication":
            from .ac_service import ACStateEvaluator

            evaluator = ACStateEvaluator(state="ReadyForPublication", article=article)
            evaluator._evaluate_code(ac_service.MISSING_SOCIAL_MEDIA)
            evaluator._evaluate_code(ac_service.MISSING_ENGLISH_CONTENT)

        return instance


class OpenAppealForm(forms.ModelForm):
    editor = forms.ModelChoiceField(queryset=Account.objects.none(), required=True)
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)
        author_ids = self.instance.article.author_accounts.values_list("id", flat=True)
        self.fields["editor"].queryset = Account.objects.filter(
            accountrole__role__slug=SECTION_EDITOR_ROLE,
            accountrole__journal=self.instance.article.journal,
        ).exclude(id__in=author_ids)
        self.fields["editor"].initial = WjsEditorAssignment.objects.get_current(article=self.instance.article).editor

    def get_logic_instance(self):
        """Instantiate :py:class:`AssignToEditor` class."""
        return OpenAppeal(
            new_editor=self.cleaned_data["editor"],
            article=self.instance.article,
            request=self.request,
        )

    def save(self, commit=True):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class WithdrawPreprintForm(forms.Form):
    """Form used by an author or owner who wants to withdraw a preprint."""

    notification_subject = forms.CharField(label=_("Subject"), disabled=True)
    notification_body = WjsMiniHTMLFormField(label=_("Body"))

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> WithdrawPreprint:
        """Instantiate :py:class:`WithdrawPreprint` class."""
        return WithdrawPreprint(
            workflow=self.instance,
            request=self.request,
            form_data=self.cleaned_data,
        )

    def save(self, commit=True):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class BaseReportForm(forms.Form):
    YES_NO_CHOICES = [
        ("yes", _("Yes")),
        ("no", _("No")),
    ]
    conflict_of_interest = forms.ChoiceField(
        required=True,
        label=_("Any conflict of interest to declare?"),
        widget=forms.RadioSelect,
        choices=YES_NO_CHOICES,
    )
    editor_cover_letter = WjsMiniHTMLFormField(
        label=_("Cover letter for the Editor-in-Charge"),
        required=True,
        error_messages={
            "required": mark_safe(
                _(
                    "Cover letter for the Editor-in-Charge is required.<br> Important: if you had "
                    "uploaded a file, this will need to be uploaded again."
                )
            ),
        },
    )
    review_choice = forms.ChoiceField(
        choices=[("tex", _("TeX")), ("rich_text", _("Text"))],
        widget=forms.RadioSelect,
        required=False,
        label=_("Review format"),
    )
    author_review = WjsMiniHTMLFormField(
        label=_("Review for authors (Rich text)"),
        required=False,
        help_text=_(
            r"Please write your comments in the text area and/or upload a file."
            r"<br><br>Please DO NOT SIGN THE REPORT."
        ),
    )
    author_review_tex = forms.CharField(
        label=_("Review for authors (LaTeX)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 10, "placeholder": _("Review for authors (LaTeX)")}),
        help_text=_(
            r"Please write your report in an offline editor and save a copy to avoid losing the content in case of a "
            r"system failure. Please do not add the LaTeX preamble (i.e. drop from '\documentclass...' "
            r"to '\begin{document}' included).<br>Use the test pdf button below "
            r"to preview your report before the final upload. The report will be automatically compiled and forwarded "
            r"after clicking “submit”.<br><br>Please DO NOT SIGN THE REPORT."
        ),
    )
    # This is saved in ReviewAssignment.review_file
    review_file = forms.FileField(
        label="File for authors (any format)", required=False, widget=forms.ClearableFileInput()
    )
    author_file_title = forms.CharField(label=_("File title"), required=False)

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("review_assignment", None)
        self.submit_final = kwargs.pop("submit_final", None)
        self.request = kwargs.pop("request", None)
        # This kwarg may be redundant but is useful when we call the form just to retrieve the fields and we need
        # minimal initialization (and review assignments may be unavailable/not submitted yet)
        self.journal = kwargs.pop("journal", None)
        super().__init__(*args, **kwargs)
        self.reviewer_report_type = get_setting(
            setting_group_name="wjs_review", setting_name="reviewer_report_type", journal=self.journal
        ).value
        if self.reviewer_report_type == "tex":
            self.fields["review_choice"].initial = "tex"
            del self.fields["author_review"]
        elif self.reviewer_report_type == "text":
            self.fields["review_choice"].initial = "rich_text"
            del self.fields["author_review_tex"]
        elif self.reviewer_report_type == "tex+text":
            self.fields["review_choice"].required = True

    def get_logic_instance(self) -> SubmitReview:
        """Instantiate :py:class:`SubmitReview` class."""
        service = SubmitReview(
            assignment=self.instance.workflowreviewassignment,
            form=self,
            submit_final=self.submit_final,
            request=self.request,
        )
        return service

    def save(self, commit: bool = True) -> ReviewAssignment:
        """
        Change the state of the review using :py:class:`SubmitReview`.

        Errors are added to the form if the logic fails.
        """
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance

    def clean(self):
        cleaned_data = super().clean()
        conflict_of_interest = cleaned_data.get("conflict_of_interest")
        recommendation = cleaned_data.get("recommendation")
        follow_up_action = cleaned_data.get("follow_up_action")
        author_review = cleaned_data.get("author_review")
        author_file = cleaned_data.get("review_file")
        author_review_tex = cleaned_data.get("author_review_tex")
        # follow_up_action is required only if recommendation is to revise_minor or revise_major
        if conflict_of_interest == "yes":
            write_url = reverse(
                "wjs_message_write",
                kwargs={"pk": self.instance.article.articleworkflow.pk, "recipient_id": self.instance.editor_id},
            )
            self.add_error(
                "conflict_of_interest",
                _(
                    f"Your review cannot be uploaded since you have declared a conflict of interest.<br>"
                    f'Please <a href="{write_url}">write the Editor-in-charge</a>'
                    f"to discuss with them whether you should send your review."
                ),
            )
        if recommendation in ["revise_minor", "revise_major"] and "follow_up_action" in cleaned_data:
            if not follow_up_action:
                self.add_error("follow_up_action", _("This field is required if the recommendation is to revise."))
        if self.reviewer_report_type == "tex+text":
            if not (author_review or author_file or author_review_tex):
                self.add_error(
                    "author_review",
                    _(
                        'Please provide either "Review (to be sent to Authors)" or "Files (to be sent to Authors)", '
                        "or LaTeX review."
                    ),
                )
                self.add_error(
                    "author_review_tex",
                    _(
                        'Please provide either "Review (to be sent to Authors)" or "Files (to be sent to Authors)", '
                        "or LaTeX review."
                    ),
                )
        elif self.reviewer_report_type == "text":
            if not (author_review or author_file):
                self.add_error(
                    "author_review",
                    _('Please provide either "Review (to be sent to Authors)" or "Files (to be sent to Authors)".'),
                )
        elif self.reviewer_report_type == "tex":
            if not author_review_tex:
                self.add_error(
                    "author_review_tex",
                    _("Please provide the LaTeX review."),
                )
        return cleaned_data


class JCOMReportForm(BaseReportForm):
    EVALUATION_CHOICES = [
        ("", "---"),
        ("Poor", _("Poor")),
        ("Acceptable", _("Acceptable")),
        ("Good", _("Good")),
        ("Excellent", _("Excellent")),
    ]
    EVALUATION_CHOICES_NOT_APPLICABLE = EVALUATION_CHOICES + [("Not applicable", _("Not applicable"))]
    RECOMMENDATION_CHOICES = [
        ("", "---"),
        ("publish", _("It can be published in this form.")),
        (
            "revise_minor",
            _(
                "There are some weaknesses or errors. The author(s) should revise the paper, taking the reviewers` "
                "comments into account."
            ),
        ),
        (
            "revise_major",
            _(
                "There are major weaknesses or errors. The author(s) should rewrite the paper, along the lines "
                "indicated by the reviewers` comments."
            ),
        ),
        ("reject", _("The paper is not to be published.")),
    ]
    FOLLOWUP_CHOICES = [
        ("", "---"),
        ("no_review", _("I don't think it will be necessary for me to review the article again.")),
        ("second_review", _("Send me back the revised paper for a second review.")),
        ("another_reviewer", _("Send the paper for review to another reviewer.")),
    ]
    # EVALUATION
    structure_and_writing_style = forms.ChoiceField(
        choices=EVALUATION_CHOICES, label=_("Structure and Writing Style"), required=True
    )
    originality = forms.ChoiceField(choices=EVALUATION_CHOICES, label=_("Originality"), required=True)
    scope_and_methods = forms.ChoiceField(
        choices=EVALUATION_CHOICES_NOT_APPLICABLE, label=_("Scope and Methods"), required=True
    )
    argument_and_discussion = forms.ChoiceField(
        choices=EVALUATION_CHOICES_NOT_APPLICABLE, label=_("Argument and Discussion"), required=True
    )
    # RECOMMENDATION
    recommendation = forms.ChoiceField(choices=RECOMMENDATION_CHOICES, label=_("Recommendation"), required=True)
    # FOLLOW-UP ACTIONS
    follow_up_action = forms.ChoiceField(choices=FOLLOWUP_CHOICES, label=_("Follow-up Action"), required=False)
    suggested_reviewers = forms.CharField(
        label=_("Suggested reviewer(s)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("name/email")}),
    )


class JQuantReportForm(BaseReportForm):
    EVALUATION_CHOICES = [
        ("", "---"),
        ("Poor", _("Poor")),
        ("Fair", _("Fair")),
        ("Good", _("Good")),
        ("Excellent", _("Excellent")),
    ]
    EVALUATION_CHOICES_NOT_APPLICABLE = EVALUATION_CHOICES + [("Not applicable", _("Not applicable"))]
    RECOMMENDATION_CHOICES = [
        ("", "---"),
        ("accept", _("Accept")),
        (
            "revise_minor",
            _("Minor revision"),
        ),
        (
            "revise_major",
            _("Major revision"),
        ),
        ("reject", _("Reject")),
    ]
    # EVALUATION
    soundness = forms.ChoiceField(
        choices=EVALUATION_CHOICES, label=_("Soundness of the methodology and arguments"), required=True
    )
    originality = forms.ChoiceField(
        choices=EVALUATION_CHOICES, label=_("Originality and contribution to the existing literature"), required=True
    )
    significance = forms.ChoiceField(
        choices=EVALUATION_CHOICES_NOT_APPLICABLE, label=_("Significance and potential impact"), required=True
    )
    clarity = forms.ChoiceField(
        choices=EVALUATION_CHOICES_NOT_APPLICABLE, label=_("Clarity and organization of the manuscript"), required=True
    )
    suitability = forms.ChoiceField(
        choices=EVALUATION_CHOICES_NOT_APPLICABLE, label=_("Suitability for the journal’s scope"), required=True
    )
    appropriateness = forms.ChoiceField(
        choices=EVALUATION_CHOICES_NOT_APPLICABLE, label=_("Appropriateness of the manuscript length"), required=True
    )
    # RECOMMENDATION
    recommendation = forms.ChoiceField(choices=RECOMMENDATION_CHOICES, label=_("Recommendation"), required=True)


class ConfirmVersionForm(BaseEditorRevisionRequestEditForm):
    confirm_version = forms.BooleanField(
        label=_(
            "I confirm that my cover letter to the Editor includes my reasons for asking for reconsideration "
            "of this version."
        ),
    )

    class Meta:
        model = EditorRevisionRequest
        fields = ["author_note", "confirm_previous_version"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.save_cover_letter or self.confirm_previous_version:
            self.fields["confirm_version"].required = False

    def check_for_potential_errors(self):
        """Check if the user has confirmed all the required fields."""
        errors = []
        if not self.cleaned_data.get("confirm_version", False):
            errors.append(_("You must confirm that the cover letter includes reasons for reconsideration."))
        if not self.instance.author_note and not self.instance.cover_letter_file:
            errors.append(_("You should provide and save a cover letter."))
        return errors

    def finish(self) -> EditorRevisionRequest:
        self.instance.confirm_previous_version = True
        self.instance.save()
        return super().finish()


class EditorDeclinesAssignmentForm(forms.Form):
    """Form to decline an editor's decision."""

    decline_reason = forms.ChoiceField(choices=PastEditorAssignment.DeclineReasons.choices, required=True)
    decline_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label=_("Please write here any additional comments for the Editor in Chief"),
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.instance = kwargs.pop("instance")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self):
        """Instantiate :py:class:`HandleEditorDeclinesAssignment` class."""
        service = HandleEditorDeclinesAssignment(
            assignment=WjsEditorAssignment.objects.get_all(self.instance).get(editor=self.request.user),
            editor=self.request.user,
            request=self.request,
            form_data=self.cleaned_data,
        )
        return service

    def save(self, commit=True) -> ReviewAssignment:
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


# ---------------------------------------------------------------------------
# AC updates for time-based ACs affected by reminder enable/disable are
# handled inline in save() via evaluate_time_based().
#
# TODO (New Issue 10, 260318-SISSA-Specifications-for-attention-conditions.md):
#   Refactor to use a logic class for reminder enable/disable, following the
#   pattern used by other DML operations (e.g., BaseAssignToEditor). This would
#   centralize AC updates and make the write path consistent.
# ---------------------------------------------------------------------------


class ToggleDisableRemindersForm(forms.ModelForm):
    class Meta:
        model = Reminder
        fields = ["disabled"]

    def save(self, commit=True):
        self.instance.refresh_from_db()
        self.instance.disabled = not self.instance.disabled
        instance = super().save(commit=commit)

        # -- Materialized AC updates --
        # Re-evaluate time-based ACs for the article. NB: condition functions
        # currently ignore Reminder.disabled (they only check date_sent), so
        # the toggle itself cannot change any AC: this is just an opportunistic
        # refresh. If "late" becomes due-date-aware for disabled reminders
        # (see the reminders-vs-late issue), this hook becomes effective.
        article = instance.get_related_article()
        if article and hasattr(article, "articleworkflow"):
            from .ac_service import ACStateEvaluator

            ACStateEvaluator(
                state=article.articleworkflow.state,
                article=article,
            ).evaluate_time_based()

        return instance


class EditorRevisionRequestForm(forms.ModelForm):
    title = WjsSimpleBleach()

    class Meta:
        model = EditorRevisionRequest
        fields = ["title", "abstract"]
