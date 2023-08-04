from datetime import timedelta
from typing import Any, Dict

from dateutil.utils import today
from django import forms
from django.contrib.auth import get_user_model
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django_summernote.widgets import SummernoteWidget
from review.models import ReviewAssignment
from utils.setting_handler import get_setting

from .logic import AssignToReviewer, EvaluateReview
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
        except ValueError as e:
            self.add_error(None, e)
            raise forms.ValidationError(e)
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


class EvaluateReviewForm(forms.ModelForm):
    reviewer_decision = forms.ChoiceField(choices=(("1", _("Accept")), ("0", _("Reject"))), required=True)
    comments_for_editor = forms.CharField(
        label=_("Please provide a reason for declining"),
        widget=SummernoteWidget(),
        required=False,
    )

    class Meta:
        model = ReviewAssignment
        fields = ["reviewer_decision", "comments_for_editor"]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data["reviewer_decision"] == "0" and not cleaned_data["comments_for_editor"]:
            self.add_error("comments_for_editor", _("Please provide a reason for declining"))
        return cleaned_data

    def get_logic_instance(self) -> EvaluateReview:
        """Instantiate :py:class:`EvaluateReview` class."""
        service = EvaluateReview(
            assignment=self.instance,
            reviewer=self.instance.reviewer,
            editor=self.instance.editor,
            form_data=self.cleaned_data,
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
        except ValueError as e:
            self.add_error(None, e)
            raise forms.ValidationError(e)
        self.instance.refresh_from_db()
        return self.instance
