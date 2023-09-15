from datetime import timedelta
from typing import Any, Dict, Iterable, Optional

from dateutil.utils import today
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
from utils.setting_handler import get_setting

from .logic import (
    AssignToReviewer,
    EvaluateReview,
    HandleDecision,
    InviteReviewer,
    SubmitReview,
)
from .models import ArticleWorkflow

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
    reviewer = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.HiddenInput, required=False)
    message = forms.CharField(widget=forms.Textarea(), required=False)
    acceptance_due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        htmx = kwargs.pop("htmx", False)
        super().__init__(*args, **kwargs)
        c_data = self.data.copy()
        c_data["state"] = self.instance.state
        self.data = c_data
        # When loading during an htmx request fields are not required because we're only preseeding the reviewer
        # When loading during a normal request (ie: submitting the form) fields are required
        if not htmx:
            self.fields["message"].required = True
            self.fields["reviewer"].required = True
        # iF reviewer is not set, other fields are disabled, because we need the reviewer to be set first
        if not self.data.get("reviewer"):
            self.fields["acceptance_due_date"].widget.attrs["disabled"] = True
            self.fields["message"].widget.attrs["disabled"] = True
        else:
            # reviewer is set, so we can load default data
            self.fields["message"].widget = SummernoteWidget()
            interval_days = get_setting("wjs_review", "acceptance_due_date_days", self.instance.article.journal)
            default_message = get_setting("wjs_review", "review_invitation_message", self.instance.article.journal)
            self.data["acceptance_due_date"] = today() + timedelta(days=interval_days.process_value())
            self.data["message"] = default_message.process_value()
        self.fields["reviewer"].queryset = Account.objects.get_reviewers_choices(self.instance)

    def clean_date(self):
        due_date = self.cleaned_data["acceptance_due_date"]
        if due_date < now().date():
            raise forms.ValidationError(_("Date must be in the future"))
        return due_date

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
        self.clean_logic()
        return cleaned_data

    def get_logic_instance(self, cleaned_data: Dict[str, Any]) -> AssignToReviewer:
        """Instantiate :py:class:`AssignToReviewer` class."""
        return AssignToReviewer(
            reviewer=cleaned_data["reviewer"],
            workflow=self.instance,
            editor=self.user,
            form_data={
                "acceptance_due_date": cleaned_data["acceptance_due_date"],
                "message": cleaned_data["message"],
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
    search = forms.CharField(required=False)
    user_type = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Tutti"),
            ("past", "R. who have already worked on this paper"),
            ("known", "R. w/ whom I've already worked"),
            ("declined", "R. who declined previous assignments (for this paper)"),
        ],
    )


class InviteUserForm(forms.Form):
    """Used by staff to invite external users for review activities."""

    first_name = forms.CharField()
    last_name = forms.CharField()
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.instance = kwargs.pop("instance")
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> InviteReviewer:
        """Instantiate :py:class:`InviteReviewer` class."""
        service = InviteReviewer(
            workflow=self.instance,
            editor=self.user,
            form_data=self.cleaned_data,
            request=self.request,
        )
        return service

    def save(self):
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
    reviewer_decision = forms.ChoiceField(choices=(("1", _("Accept")), ("0", _("Reject"))), required=True)
    decline_reason = forms.CharField(
        label=_("Please provide a reason for declining"),
        widget=SummernoteWidget(),
        required=False,
    )
    accept_gdpr = forms.BooleanField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = ReviewAssignment
        fields = ["reviewer_decision", "comments_for_editor"]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        self.token = kwargs.pop("token")
        super().__init__(*args, **kwargs)
        if self.token and not self.instance.reviewer.jcomprofile.gdpr_checkbox:
            self.fields["accept_gdpr"].widget = forms.CheckboxInput()
        if self.instance.date_accepted:
            self.fields["reviewer_decision"].required = False

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
        choices=ArticleWorkflow.Decisions.choices,
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
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> HandleDecision:
        """Instantiate :py:class:`EvaluateReview` class."""
        service = HandleDecision(
            workflow=self.instance,
            form_data=self.cleaned_data,
            user=self.user,
            request=self.request,
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
