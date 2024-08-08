import datetime
from typing import Any, Dict, Iterable, Optional

from core import files as core_files
from core import models as core_models
from core.forms import ConfirmableForm
from core.models import File
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import formset_factory
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django_summernote.widgets import SummernoteWidget
from review.forms import GeneratedForm
from review.models import (
    ReviewAssignment,
    ReviewAssignmentAnswer,
    ReviewForm,
    ReviewFormElement,
)
from submission.models import Article
from utils.setting_handler import get_setting

from wjs.jcom_profile import permissions as base_permissions
from wjs.jcom_profile.constants import EO_GROUP, SECTION_EDITOR_ROLE

from . import communication_utils, conditions
from .logic import (
    AssignToEditor,
    AssignToReviewer,
    AuthorHandleRevision,
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
    WithdrawPreprint,
    render_template_from_setting,
)
from .models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    MessageRecipients,
    MessageThread,
    ProphyAccount,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

Account = get_user_model()


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


class SelectReviewerForm(forms.ModelForm):
    reviewer = forms.ModelChoiceField(
        label=_("Reviewer"), queryset=Account.objects.none(), widget=forms.HiddenInput, required=False
    )
    message = forms.CharField(label=_("Message"), widget=forms.Textarea(), required=False)
    acceptance_due_date = forms.DateField(label=_("Reviewer should accept/decline invite by"), required=False)
    state = forms.CharField(widget=forms.HiddenInput(), required=False)
    author_note_visible = forms.BooleanField(label=_("Author cover letter"), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        """Take care of htmx and reviewer-not-selected."""
        # Memoize the return value to call the function only once
        _today = now().date()
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        self.editor_assigns_themselves_as_reviewer = kwargs.pop("editor_assigns_themselves_as_reviewer", False)
        htmx = kwargs.pop("htmx", False)
        super().__init__(*args, **kwargs)
        # refs #648
        # https://gitlab.sissamedialab.it/wjs/specs/-/issues/648
        self.date_value = _today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_DAYS)
        self.date_min = _today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MIN)
        self.date_max = _today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MAX)
        date_attrs = {
            "type": "date",
            "value": self.date_value,
            "min": self.date_min,
            "max": self.date_max,
        }
        self.fields["acceptance_due_date"].widget = forms.DateInput(attrs=date_attrs)
        c_data = self.data.copy()
        c_data["state"] = self.instance.state
        self.data = c_data

        if not self.instance.article.comments_editor:
            self.fields["author_note_visible"].widget = forms.HiddenInput()

        # When loading during an htmx request fields are not required because we're only preseeding the reviewer
        # When loading during a normal request (ie: submitting the form) fields are required
        if not htmx and not self.editor_assigns_themselves_as_reviewer:
            self.fields["message"].required = True
            self.fields["reviewer"].required = True

        if self.editor_assigns_themselves_as_reviewer:
            self.fields["message"].widget = forms.HiddenInput()
            self.fields["author_note_visible"].widget = forms.HiddenInput()
        # If the reviewer is not set, other fields are disabled, because we need the reviewer to be set first
        elif not self.data.get("reviewer"):
            self.fields["acceptance_due_date"].widget.attrs["disabled"] = True
            self.fields["message"].widget.attrs["disabled"] = True
            self.fields["author_note_visible"].widget.attrs["disabled"] = True
        else:
            # reviewer is set
            if htmx:
                # we can load default data
                default_visibility = WorkflowReviewAssignment._meta.get_field("author_note_visible").default
                self.fields["message"].widget = SummernoteWidget()
                if not self.data.get("message", None):
                    default_message_rendered = render_template_from_setting(
                        setting_group_name="wjs_review",
                        setting_name="review_invitation_message",
                        journal=self.instance.article.journal,
                        request=self.request,
                        context=self.get_message_context(),
                        template_is_setting=True,
                    )
                    self.data["message"] = default_message_rendered
                if not self.data.get("acceptance_due_date", None):
                    interval_days = get_setting(
                        "wjs_review",
                        "acceptance_due_date_days",
                        self.instance.article.journal,
                    )
                    self.data["acceptance_due_date"] = _today + datetime.timedelta(days=interval_days.process_value())
                if not self.data.get("author_note_visible", None):
                    self.data["author_note_visible"] = default_visibility
            else:
                # the form has been submitted (for real, not htmx)
                # Nothing to do (the field-required thing has been done already some lines above).
                pass

        self.fields["reviewer"].queryset = Account.objects.get_reviewers_choices(self.instance)

    def get_message_context(self) -> Dict[str, Any]:
        """
        Return a dictionary with the context  to render default form message.

        The context is generated using AssignToReviewer._get_message_context method.

        Reviewer is a fake Account instance, as we don't have one yet: we only need its id to render the message.
        WorkflowReviewAssignment is a fake WorkflowReviewAssignment instance, as we don't have one yet.
        """
        form_data = self.data.copy()
        if reviewer_id := form_data.get("reviewer", False):
            form_data["reviewer"] = Account.objects.get(id=reviewer_id)
        else:
            fake_reviewer = Account(id=self.data.get("reviewer"))
            form_data["reviewer"] = fake_reviewer
        logic = self.get_logic_instance(form_data)
        logic.assignment = WorkflowReviewAssignment(id=1, access_code="sample")
        return logic._get_message_context()

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
            raise forms.ValidationError(_(f"Date must be between {self.date_min} and {self.date_max}"))
        return acceptance_due_date

    def clean_reviewer(self):
        """
        Validate the reviewer.

        A reviewer must not be any of the authors linked to the article being reviewed.
        """
        reviewer = self.cleaned_data["reviewer"]
        if not AssignToReviewer.check_reviewer_conditions(self.instance, reviewer):
            raise forms.ValidationError("A reviewer must not be an author of the article")
        return reviewer

    def clean_logic(self):
        """Run AssignToReviewer.check_conditions method."""
        if not self.get_logic_instance(self.cleaned_data).check_conditions():
            raise forms.ValidationError(_("Assignment conditions not met."))

    def clean(self) -> Dict[str, Any]:
        cleaned_data = super().clean()
        if self.editor_assigns_themselves_as_reviewer:
            cleaned_data["reviewer"] = self.user
        self.clean_logic()
        return cleaned_data

    def get_logic_instance(self, cleaned_data: Dict[str, Any]) -> AssignToReviewer:
        """Instantiate :py:class:`AssignToReviewer` class."""
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

    def save(self, commit: bool = True) -> ArticleWorkflow:
        """Change the state of the review using the transition method."""
        try:
            service = self.get_logic_instance(self.cleaned_data)
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class ReviewerSearchForm(forms.Form):
    search = forms.CharField(required=False, label=_("Name"))
    user_type = forms.ChoiceField(
        required=False,
        choices=[
            ("", "---"),
            ("all", "All"),
            ("past", "Reviewed previous version"),
            ("known", "My reviewer archive"),
            ("declined", "Declined/removed from previous version"),
            ("prophy", "Suggested by Prophy"),
        ],
    )


class InviteUserForm(forms.Form):
    """Used by staff to invite external users for review activities."""

    first_name = forms.CharField(label=_("First name"))
    last_name = forms.CharField(label=_("Last name"))
    suffix = forms.CharField(widget=forms.HiddenInput(), required=False)
    email = forms.EmailField(label=_("Email"))
    message = forms.CharField(label=_("Personal notes for the reviewer"), widget=forms.Textarea, required=False)
    author_note_visible = forms.BooleanField(label=_("Allow reviewer to see author's cover letter"), required=False)

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.instance = kwargs.pop("instance")
        self.user = kwargs.pop("user")
        prophy_account = None
        if "prophy_account_id" in kwargs:
            prophy_account = ProphyAccount.objects.filter(author_id=kwargs.pop("prophy_account_id"))[0]
        super().__init__(*args, **kwargs)
        if not self.instance.article.comments_editor:
            self.fields["author_note_visible"].widget = forms.HiddenInput()
        if prophy_account:
            self.initial = {
                "first_name": f"{prophy_account.first_name} {prophy_account.middle_name}",
                "last_name": prophy_account.last_name,
                "suffix": prophy_account.suffix,
                "email": prophy_account.email,
            }

    def get_logic_instance(self) -> InviteReviewer:
        """Instantiate :py:class:`InviteReviewer` class."""
        service = InviteReviewer(
            workflow=self.instance,
            editor=self.user,
            form_data=self.cleaned_data,
            request=self.request,
        )
        return service

    def save(self, commit: bool = True):
        """
        Create user and send invitation.

        Errors are added to the form if the logic fails.
        """
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        return self.instance


class EvaluateReviewForm(forms.ModelForm):
    reviewer_decision = forms.ChoiceField(
        choices=(("1", _("Accept")), ("0", _("Reject")), ("2", _("Update"))),
        required=True,
    )
    decline_reason = forms.CharField(
        label=_("Please provide a reason for declining"),
        widget=SummernoteWidget(),
        required=False,
    )
    accept_gdpr = forms.BooleanField(required=False, widget=forms.HiddenInput())
    # https://docs.djangoproject.com/en/3.2/ref/forms/widgets/#dateinput
    # By default DateInput is an <input type="text">
    date_due = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = ReviewAssignment
        fields = ["reviewer_decision", "comments_for_editor", "date_due"]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.token = kwargs.pop("token")
        super().__init__(*args, **kwargs)
        if self.token and not self.instance.reviewer.jcomprofile.gdpr_checkbox:
            self.fields["accept_gdpr"].widget = forms.CheckboxInput()
        if self.instance.date_accepted:
            self.fields["reviewer_decision"].required = False
        if self.instance.date_due:
            self.fields["date_due"].widget.attrs["min"] = self.instance.date_due

    def clean_date_due(self):
        date_due = self.cleaned_data.get("date_due", None)
        if date_due and date_due < self.instance.date_due:
            raise forms.ValidationError(_("Date must be in the future"))
        return date_due

    def clean(self):
        cleaned_data = super().clean()
        # Decision is optional if form is submitted when submitting a report
        if cleaned_data.get("reviewer_decision", None):
            if cleaned_data["reviewer_decision"] == "0" and not cleaned_data["decline_reason"]:
                self.add_error("comments_for_editor", _("Please provide a reason for declining"))
            elif cleaned_data["reviewer_decision"] == "0" and cleaned_data["decline_reason"]:
                # we use comments_for_editor to store the decline_reason if the user has declined, or as cover letter
                # if the user submits a report. As decline reason is less important we use an alias field
                cleaned_data["comments_for_editor"] = cleaned_data["decline_reason"]
            if cleaned_data["reviewer_decision"] == "1" and self.token and not cleaned_data["accept_gdpr"]:
                self.add_error("accept_gdpr", _("You must accept GDPR to continue"))
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
                self.fields[str(element.pk)].widget = SummernoteWidget()

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
        choices=ArticleWorkflow.Decisions.decision_choices,
        required=True,
    )
    decision_editor_report = forms.CharField(
        label=_("Editor Report"),
        widget=SummernoteWidget(),
        required=False,
    )
    decision_internal_note = forms.CharField(
        label=_("Internal notes"),
        widget=SummernoteWidget(),
        required=False,
    )
    withdraw_notice = forms.CharField(
        label=_("Notice to reviewers"),
        widget=SummernoteWidget(),
        required=False,
    )
    date_due = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.request = kwargs.pop("request", None)
        self.admin_form = kwargs.pop("admin_form", False)
        self.hide_date_due = kwargs["initial"].get("decision", None) not in (
            ArticleWorkflow.Decisions.MINOR_REVISION,
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            ArticleWorkflow.Decisions.OPEN_APPEAL,
        )
        self.hide_decision = kwargs["initial"].get("decision", None)
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        kwargs["initial"]["withdraw_notice"] = get_setting(
            "wjs_review",
            "review_withdraw_notice",
            self.request.journal,
        ).processed_value
        super().__init__(*args, **kwargs)
        if self.admin_form:
            del self.fields["withdraw_notice"]

    def clean_date_due(self):
        date_due = self.cleaned_data["date_due"]
        if (
            self.cleaned_data["decision"]
            in (
                ArticleWorkflow.Decisions.MINOR_REVISION,
                ArticleWorkflow.Decisions.MAJOR_REVISION,
                ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            )
            and not date_due
        ):
            raise forms.ValidationError(_("Please provide a date due for author to submit a revision"))
        if date_due and date_due < now().date():
            raise forms.ValidationError(_("Date must be in the future"))
        return date_due

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


class UploadArticleForm(forms.Form):
    file_type = forms.ChoiceField(choices=(("manuscript", _("Manuscript")), ("data", _("Data/Figure"))))
    label = forms.CharField(widget=forms.TextInput(attrs={"placeholder": "Label"}))
    file = forms.FileField(widget=forms.FileInput())


class UploadRevisionAuthorCoverLetterFileForm(forms.ModelForm):
    class Meta:
        model = EditorRevisionRequest
        fields = ["cover_letter_file"]
        widgets = {"cover_letter_file": forms.ClearableFileInput()}


class EditorRevisionRequestEditForm(ConfirmableForm, forms.ModelForm):
    confirm_title = forms.BooleanField(
        label=_("I confirm that title and abstract on this web page correspond to those written in the preprint file.")
    )
    confirm_styles = forms.BooleanField(
        label=_(
            "I confirm that this resubmission fulfills the stylistic guidelines of the Journal and its ethical policy "
            "in all its aspects including use of AI, authorship, etc.."
        )
    )
    confirm_blind = forms.BooleanField(
        label=_("I confirm that the file does not contain any author information and has line numbering..")
    )
    confirm_cover = forms.BooleanField(
        label=_(
            "I confirm that the cover letter lists and describes clearly the changes implemented in the preprint "
            "and motivates any modifications that have not been made.."
        )
    )

    class Meta:
        model = EditorRevisionRequest
        fields = ["author_note"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> AuthorHandleRevision:
        """Instantiate :py:class:`AuthorHandleRevision` class."""
        service = AuthorHandleRevision(
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
        return errors


class MessageRecipientForm(forms.Form):
    """Helper form to collect a message recipients.

    This will be the base for an inline form.
    """

    # TODO: when switching to django >= 4, see https://github.com/jrief/django-formset
    recipient = forms.ModelChoiceField(queryset=None)

    def __init__(self, *args, **kwargs):
        """Set the queryset for the recipient."""
        actor = kwargs.pop("actor")
        article = kwargs.pop("article")
        super().__init__(*args, **kwargs)
        allowed_recipients = HandleMessage.allowed_recipients_for_actor(
            actor=actor,
            article=article,
        )
        self.fields["recipient"].queryset = allowed_recipients  # used at display


class MessageForm(forms.ModelForm):
    attachment = forms.FileField(required=False, label=_("Optional attachment"))
    recipients = forms.ModelMultipleChoiceField(queryset=None, required=True, widget=forms.widgets.HiddenInput())

    class Meta:
        model = Message
        fields = [
            "subject",
            "body",
            "actor",
            "content_type",
            "object_id",
            "message_type",
        ]
        widgets = {
            "subject": forms.TextInput(),
            "body": SummernoteWidget(),
            "actor": forms.widgets.HiddenInput(),
            "content_type": forms.widgets.HiddenInput(),
            "object_id": forms.widgets.HiddenInput(),
            "message_type": forms.widgets.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        """Set subject and body as required and store actor and target gotten from the view."""
        self.actor = kwargs.pop("actor", None)
        self.target = kwargs.pop("target")
        initial_recipient = kwargs.pop("initial_recipient")
        super().__init__(*args, **kwargs)
        self.fields["subject"].required = True
        self.fields["body"].required = True
        self.fields["recipients"].queryset = self._get_allowed_recipients()  # used at validation
        self.MessageRecipientsFormSet = formset_factory(  # noqa N806
            MessageRecipientForm,
            can_delete=True,
            min_num=1,
            extra=0,
        )
        self.recipients_formset = self.MessageRecipientsFormSet(
            prefix="recipientsFS",
            form_kwargs={
                "actor": self.actor,
                "article": self.target,
            },
            initial=[{"recipient": initial_recipient.id}],
        )

    def _get_allowed_recipients(self):
        """Use a logic class to return a queryset of allowed recipients for the current actor/article combination."""
        # TODO: see the note about refactoring this part in HandleMessage code
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
        clean_data["message_type"] = Message.MessageTypes.VERBOSE
        return clean_data

    # TODO: IMPORTANT: enforce security:
    def save(self, commit: bool = True) -> Message:
        """Set the logged-in user as actor for this message and save.

        TODO: at the moment only attachments related to Article are managed! I.e. attachments for messages not related
        to a specific article are not managed.
        """
        instance: Message = self.instance
        with transaction.atomic():
            instance = super().save()
            instance.recipients.set(self.cleaned_data["recipients"])
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

        return instance


class ToggleMessageReadForm(forms.ModelForm):
    class Meta:
        model = MessageRecipients
        fields = ["read"]


class ToggleMessageReadByEOForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ["read_by_eo"]


class UpdateReviewerDueDateForm(forms.ModelForm):
    class Meta:
        model = ReviewAssignment
        fields = ["date_due"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

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
            form_data=self.cleaned_data,
            request=self.request,
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
    date_due = forms.DateField(required=True, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = EditorRevisionRequest
        fields = ["date_due"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

    def clean_date_due(self):
        date_due = self.cleaned_data["date_due"]
        if date_due and date_due < now().date():
            raise forms.ValidationError(_("Date must be in the future"))
        return date_due

    def get_logic_instance(self):
        service = PostponeRevisionRequestDueDate(
            revision_request=self.instance,
            form_data=self.cleaned_data,
            request=self.request,
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
                "eo_in_charge": self.instance.eo_in_charge,
            },
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.instance.article,
            message_subject=message_subject,
            message_body=message_body,
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
    send_notification = forms.BooleanField(label=_("Send notification to the reviewer"), required=False, initial=True)
    notification_subject = forms.CharField(label=_("Subject"))
    notification_body = forms.CharField(label=_("Body"), widget=SummernoteWidget())

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.user = kwargs.pop("user")
        self.instance = kwargs.pop("instance")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> DeselectReviewer:
        """Instantiate :py:class:`DeselectReviewer` class."""
        return DeselectReviewer(
            assignment=self.instance,
            editor=self.user,
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
    editor = forms.ModelChoiceField(queryset=Account.objects.none(), required=True)
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        self.editor = kwargs.pop("selectable_editors")
        super().__init__(*args, **kwargs)
        self.fields["editor"].queryset = self.editor

    def get_logic_instance(self) -> AssignToEditor:
        """Instantiate :py:class:`AssignToEditor` class."""
        return AssignToEditor(
            editor=self.cleaned_data["editor"],
            article=self.instance.article,
            request=self.request,
        )

    def get_deassignment_logic_instance(self) -> Optional[HandleEditorDeclinesAssignment]:
        """
        Instantiate :py:class:`DeassignFromEditor` class.

        If no existing assignment is found, return None (ie: no deassignment is needed).
        """
        try:
            assignment = WjsEditorAssignment.objects.get_current(self.instance)
            return HandleEditorDeclinesAssignment(
                # Like in the view, assume that there is only one editorassignment for each article,
                # the condition in the logic will double-check it.
                assignment=assignment,
                editor=assignment.editor,
                request=self.request,
            )
        except WjsEditorAssignment.DoesNotExist:
            return None

    def save(self, commit: bool = True):
        try:
            service = self.get_logic_instance()
            if service_deassignment := self.get_deassignment_logic_instance():
                service_deassignment.run()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class ForwardMessageForm(forms.ModelForm):
    """Form used by the EO who wants to forward an existing message.

    Usually a message that the typesetter would like to send to the author.
    """

    class Meta:
        model = Message
        fields = ["subject", "body", "attachment"]
        widgets = {"body": SummernoteWidget()}

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
                message_type=Message.MessageTypes.VERBOSE,
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

            return message


class ArticleExtraInformationUpdateForm(forms.ModelForm):
    social_media_image = forms.ImageField(required=False, label=_("Social media image"))
    english_title = forms.CharField(label=_("Article title - English language"))
    english_abstract = forms.CharField(label=_("Article abstract - English language"), widget=SummernoteWidget())

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

        # If no conditions are met, fields list is empty but this is not an issue as at least on condition must be met
        # for the view to be accessible.
        if not needs_english:
            del self.fields["english_title"]
            del self.fields["english_abstract"]
        if not is_published_piecemeal:
            del self.fields["social_media_image"]
            del self.fields["social_media_short_description"]

    def save(self, commit=True):
        instance = super().save(commit)
        if self.cleaned_data.get("social_media_image"):
            instance.article.meta_image = self.cleaned_data["social_media_image"]
            instance.article.save()
        # this step is entirely skipped if the journal doesn't need english content, so there is no risk to overwrite
        # the original title and abstract
        if self.cleaned_data.get("english_title"):
            instance.article.title_en = self.cleaned_data["english_title"]
            instance.article.abstract_en = self.cleaned_data["english_abstract"]
            instance.article.save()
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
        author_ids = self.instance.article.authors.values_list("id", flat=True)
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
    """Form used by an author who wants to withdraw a preprint."""

    notification_subject = forms.CharField(label=_("Subject"))
    notification_body = forms.CharField(label=_("Body"), widget=SummernoteWidget())

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> WithdrawPreprint:
        """Instantiate :py:class:`WithdrawPreprint` class."""
        return WithdrawPreprint(
            workflow=self.instance,
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
