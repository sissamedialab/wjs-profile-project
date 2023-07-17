from datetime import timedelta
from typing import Any, Dict

from core.models import Account
from dateutil.utils import today
from django import forms
from django.utils.timezone import now
from plugins.wjs_review.logic import AssignToReviewer
from plugins.wjs_review.users import get_available_users_by_role

from .models import ArticleWorkflow


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
    reviewer = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.HiddenInput, required=True)
    message = forms.CharField(widget=forms.Textarea, required=True)
    acceptance_due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    state = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ArticleWorkflow
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)
        self.initial["acceptance_due_date"] = today() + timedelta(days=7)
        self.fields["reviewer"].queryset = get_available_users_by_role(
            self.instance.article.journal,
            "reviewer",
            exclude=self.instance.article_authors.values_list("pk", flat=True),
        )

    def clean_date(self):
        due_date = self.cleaned_data["acceptance_due_date"]
        if due_date < now().date():
            raise forms.ValidationError("Date must be in the future")
        return due_date

    def clean_reviewer(self):
        # a reviewer must not be any of the authors linked to the article being reviewed
        reviewer = self.cleaned_data["reviewer"]
        if not AssignToReviewer.check_reviewer_conditions(self.instance, reviewer):
            raise forms.ValidationError("A reviewer must not be an author of the article")
        return reviewer

    def save(self, commit: bool = True) -> ArticleWorkflow:
        """Change the state of the review using the transition method."""
        try:
            AssignToReviewer(
                reviewer=self.cleaned_data["reviewer"],
                workflow=self.instance,
                editor=self.user,
                form_data={
                    "acceptance_due_date": self.cleaned_data["acceptance_due_date"],
                    "message": self.cleaned_data["message"],
                },
                request=self.request,
            ).run()
        except ValueError as e:
            self.add_error(None, e)
            raise forms.ValidationError(e)
        self.instance.refresh_from_db()
        return self.instance


class ReviewerSearchForm(forms.Form):
    first_name = forms.CharField(required=False)
    last_name = forms.CharField(required=False)
    email = forms.EmailField(required=False)
