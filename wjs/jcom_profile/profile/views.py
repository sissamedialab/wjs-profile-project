from core import logic
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.forms.models import ModelForm
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
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


class BaseProfileEditView(LoginRequiredMixin, UpdateView):
    """
    BaseProfileEditView class provides functionality for editing a user's profile.

    This class is designed to handle the update functionality for a user's profile
    through a form-based view. It ensures that only authenticated users can access
    the view and provides additional methods for customizing form behavior and the
    retrieval of the profile object.
    """

    model = JCOMProfile
    update_message = _("Profile updated.")

    def form_valid(self, form: ModelForm) -> HttpResponse:
        """
        Process a valid submitted form and generate the response.

        A proper success message is emitted upon successful form processing.

        This method is called when a submitted form is valid. It first processes the
        form using the parent class's `form_valid` method, then adds a success message
        to the request using the `update_message` attribute.

        :param form: The form to be processed.
        :type form: ModelForm
        :return: The processed response.
        :rtype: HttpResponse
        """
        response = super().form_valid(form)
        messages.success(self.request, self.update_message)
        return response

    def get_form_kwargs(self):
        """
        Provide additional keyword arguments for the form.

        The method overrides the parent implementation to inject
        specific context necessary for form processing.


        :return: A dictionary containing all keyword arguments required by the form, including the injected `journal`
            context from the request object.
        :rtype: dict
        """
        kwargs = super().get_form_kwargs()
        kwargs["journal"] = self.request.journal
        return kwargs

    def get_object(self, queryset=None) -> JCOMProfile:
        """
        Gets the object associated with the currently authenticated user.

        This method retrieves an instance of the model associated with the user's
        profile based on the user's primary key (pk). The primary key is fetched
        from the current authenticated user's request object.

        :param queryset: The query set to be filtered by the primary key of the user's profile.
        :type queryset: QuerySet, optional
        :return: The model instance corresponding to the authenticated user's profile.
        :rtype: JCOMProfile, which is the model associated with the user's profile.
        :raise: JCOMProfile.DoesNotExist: If no object matching the user's primary key
            exists.
        """
        return self.model.objects.get(pk=self.request.user.pk)


class ProfilePersonalEditView(BaseProfileEditView):
    form_class = WjsPersonalInfoForm
    template_name = "wjs/profile/personal_edit.html"
    success_url = reverse_lazy("core_edit_profile")
    update_message = _("Profile updated.")


class ProfileEmailEditView(BaseProfileEditView):
    form_class = WjsEmailChangeForm
    template_name = "wjs/profile/personal_email_edit.html"
    success_url = reverse_lazy("core_edit_profile_email")
    update_message = _("Alternative email updated.")
    update_message_2 = _("Email change started.")

    def form_valid(self, form):
        try:
            if form.cleaned_data["new_email"]:
                logic.handle_email_change(
                    self.request, form.cleaned_data["new_email"], next_url=self.get_success_url()
                )
                messages.success(self.request, self.update_message_2)
                return redirect(self.get_success_url())
            else:
                return super().form_valid(form)
        except IntegrityError:
            return self.form_invalid(form)


class ProfilePasswordEditView(BaseProfileEditView):
    form_class = WjsPasswordChangeForm
    template_name = "wjs/profile/personal_password_edit.html"
    success_url = reverse_lazy("core_edit_profile_password")
    update_message = _("Password updated.")


class ProfileAdditionalEditView(BaseProfileEditView):
    form_class = WjsAdditionalInfoForm
    template_name = "wjs/profile/personal_info_edit.html"
    success_url = reverse_lazy("core_edit_profile_additional")
    update_message = _("Information updated.")


class ProfileInterestsEditView(BaseProfileEditView):
    model = JCOMProfile
    form_class = WjsInterestsForm
    template_name = "wjs/profile/personal_interests_edit.html"
    success_url = reverse_lazy("core_edit_profile_interests")
    update_message = _("Interests updated.")
