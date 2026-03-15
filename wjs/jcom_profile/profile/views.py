from core import logic
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import UpdateView

from ..models import JCOMProfile
from .forms import (
    WjsAdditionalInfoForm,
    WjsEmailChangeForm,
    WjsInterestsForm,
    WjsPasswordChangeForm,
    WjsPersonalInfoForm,
)


class ProfilePersonalEditView(LoginRequiredMixin, UpdateView):
    model = JCOMProfile
    form_class = WjsPersonalInfoForm
    template_name = "wjs/profile/personal_edit.html"
    success_url = reverse_lazy("core_edit_profile")

    def get_object(self, queryset=None):
        return self.model.objects.get(pk=self.request.user.pk)


class ProfileEmailEditView(LoginRequiredMixin, UpdateView):
    model = JCOMProfile
    form_class = WjsEmailChangeForm
    template_name = "wjs/profile/personal_email_edit.html"
    success_url = reverse_lazy("core_edit_profile_email")

    def get_object(self, queryset=None):
        return self.model.objects.get(pk=self.request.user.pk)

    def form_valid(self, form):
        try:
            logic.handle_email_change(self.request, form.cleaned_data["new_email"], next_url=self.get_success_url())
            return redirect(reverse("website_index"))
        except IntegrityError:
            return self.form_invalid(form)


class ProfilePasswordEditView(LoginRequiredMixin, UpdateView):
    model = JCOMProfile
    form_class = WjsPasswordChangeForm
    template_name = "wjs/profile/personal_password_edit.html"
    success_url = reverse_lazy("core_edit_profile_password")

    def get_object(self, queryset=None):
        return self.model.objects.get(pk=self.request.user.pk)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.add_message(self.request, messages.SUCCESS, _("Password updated."))
        return response


class ProfileAdditionalEditView(LoginRequiredMixin, UpdateView):
    model = JCOMProfile
    form_class = WjsAdditionalInfoForm
    template_name = "wjs/profile/personal_info_edit.html"
    fields = None
    success_url = reverse_lazy("core_edit_profile_additional")

    def get_object(self, queryset=None):
        return self.model.objects.get(pk=self.request.user.pk)


class ProfileInterestsEditView(LoginRequiredMixin, UpdateView):
    model = JCOMProfile
    form_class = WjsInterestsForm
    fields = None
    template_name = "wjs/profile/personal_interests_edit.html"
    success_url = reverse_lazy("core_edit_profile_interests")

    def get_object(self, queryset=None):
        return self.model.objects.get(pk=self.request.user.pk)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["journal"] = self.request.journal
        return kwargs
