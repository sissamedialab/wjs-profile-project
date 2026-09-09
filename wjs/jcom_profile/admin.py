"""Register the models with the admin interface."""

from core.admin import AccountAdmin
from core.models import Account
from django import forms
from django.contrib import admin
from django.http import HttpRequest
from journal.admin import IssueAdmin
from journal.models import Issue
from modeltranslation.admin import TranslationAdmin
from submission.admin import KeywordAdmin
from submission.models import Keyword

from .models import (
    Correspondence,
    JCOMProfile,
    Recipient,
    StaffKeyword,
    StaffWorkloadParameters,
)
from .templatetags.wjs_tags import is_field_available

admin.site.unregister(Account)
admin.site.unregister(Issue)
admin.site.unregister(Keyword)


class JCOMProfileAdminForm(forms.ModelForm):
    """Helper class to "inline" account profession."""

    class Meta:
        model = JCOMProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        self.journal = kwargs.pop("journal", None)
        super().__init__(*args, **kwargs)
        # Fiels are removed in JCOMProfileInline.get_fields, so if the field is in the list, it's required
        if "profession" in self.fields:
            self.fields["profession"].required = True
        if "career_stage" in self.fields:
            self.fields["career_stage"].required = True


class JCOMProfileInline(admin.StackedInline):
    """Helper class to "inline" account profession."""

    form = JCOMProfileAdminForm
    model = JCOMProfile

    def get_fields(self, request: HttpRequest, obj: JCOMProfile | None = ...) -> list[str]:
        fields = ["gdpr_checkbox", "invitation_token", "usernotes"]
        if is_field_available(request.journal, "profession"):
            fields.append("profession")
        if is_field_available(request.journal, "career_stage"):
            fields.append("career_stage")
        return fields


@admin.register(Account)
class UserAdmin(AccountAdmin):
    """Another layer..."""

    inlines = (JCOMProfileInline,)


@admin.register(Correspondence)
class CorrespondenceAdmin(admin.ModelAdmin):
    """Helper class to "admin" correspondence."""

    list_filter = ("source",)
    search_fields = ["account__last_name", "email", "account__email"]


@admin.register(StaffWorkloadParameters)
class StaffWorkloadParametersAdmin(admin.ModelAdmin):
    """Helper class to "admin" editor assignment parameters."""


@admin.register(StaffKeyword)
class EditorKeywordAdmin(admin.ModelAdmin):
    """Helper class to "admin" editor keyword."""


@admin.register(Recipient)
class RecipientAdmin(admin.ModelAdmin):
    """Helper class to "admin" recipient."""

    list_filter = ["journal"]
    search_fields = ["email", "user__email"]


@admin.register(Keyword)
class KeywordTranslationAdmin(KeywordAdmin, TranslationAdmin):
    """Keyword translations."""

    list_filter = ["journal"]
    list_display = ["word", "id"]


@admin.register(Issue)
class IssueTranslationAdmin(IssueAdmin, TranslationAdmin):
    """Issue translations."""
