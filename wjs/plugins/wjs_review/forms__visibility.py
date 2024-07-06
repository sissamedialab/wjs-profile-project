from typing import Optional

from django import forms
from django.forms import BaseFormSet
from django.utils.translation import gettext as _

from .models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    PermissionAssignment,
    WorkflowReviewAssignment,
)


class BaseUserPermissionFormSet(BaseFormSet):
    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        self.article = kwargs.pop("article")
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["article"] = self.article
        kwargs["user"] = self.user
        kwargs["object"] = self.initial[index].get("object")
        kwargs["round"] = self.initial[index].get("round")
        kwargs["author_notes"] = self.initial[index].get("author_notes")
        return kwargs

    def save(self, commit=True):
        for form in self.forms:
            form.save()
        return True


class UserPermissionsForm(forms.Form):
    object_type = forms.CharField(widget=forms.HiddenInput)
    object_id = forms.IntegerField(widget=forms.HiddenInput)
    permission_secondary = forms.ChoiceField(
        label=_("Cover Letter"),
        choices=PermissionAssignment.BinaryPermissionType.choices,
        widget=forms.RadioSelect,
        required=False,
    )
    permission = forms.ChoiceField(
        choices=PermissionAssignment.PermissionType.choices, widget=forms.RadioSelect, required=False
    )

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        self.article = kwargs.pop("article")
        self.user = kwargs.pop("user")
        self.object = kwargs.pop("object")
        self.round = kwargs.pop("round")
        self.author_notes = kwargs.pop("author_notes")
        super().__init__(*args, **kwargs)
        if isinstance(self.object, WorkflowReviewAssignment):
            self.fields["permission_secondary"].widget = forms.HiddenInput()
        elif isinstance(self.object, ArticleWorkflow):
            self.fields["permission"].widget = forms.HiddenInput()
        elif isinstance(self.object, EditorRevisionRequest):
            if self.author_notes:
                self.fields["permission"].widget = forms.HiddenInput()
            else:
                self.fields["permission_secondary"].widget = forms.HiddenInput()
        self.fields["permission"].label = self.object.permission_label

    def save(self) -> Optional[PermissionAssignment]:
        """Creates / updates custom PermissionAssignment if value is changed."""
        permission_changes = "permission" in self.changed_data or "permission_secondary" in self.changed_data
        valid_data = self.cleaned_data.get("permission") or self.cleaned_data.get("permission_secondary")
        if permission_changes and valid_data:
            permission, __ = PermissionAssignment.objects.update_or_create(
                content_type_id=self.cleaned_data.get("object_type"),
                object_id=self.cleaned_data.get("object_id"),
                user=self.user,
                defaults={
                    "permission": self.cleaned_data.get("permission"),
                    "permission_secondary": self.cleaned_data.get("permission_secondary"),
                },
            )
            return permission
