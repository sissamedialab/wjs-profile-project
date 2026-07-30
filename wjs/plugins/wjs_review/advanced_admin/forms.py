from django import forms
from django.apps import apps

WorkflowReviewAssignment = apps.get_model("wjs_review", "WorkflowReviewAssignment")


class WorkflowReviewAssignmentForm(forms.ModelForm):
    reviewer_report = forms.CharField(
        required=False,
        widget=forms.Textarea,
    )

    class Meta:
        model = WorkflowReviewAssignment
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["reviewer_report"].initial = self.instance.report_form_answers.get("author_review")

    def save(self, commit=True):
        instance = super().save(commit=False)

        report_form_answers = instance.report_form_answers or {}
        report_form_answers["author_review"] = self.cleaned_data["reviewer_report"]
        instance.report_form_answers = report_form_answers

        if commit:
            instance.save()

        return instance
