from core import logic
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.forms.models import ModelForm
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.generic import UpdateView, View
from plugins.wjs_submission.keywords import get_keywords_by_journal

from ..models import JCOMProfile
from ..utils import get_active_keywords
from .forms import (
    WjsAdditionalInfoForm,
    WjsEmailChangeForm,
    WjsInterestsForm,
    WjsPasswordChangeForm,
    WjsPersonalInfoForm,
)
from .required_fields import (
    get_correspondence_author_requirements,
    safe_back_to_submission_url,
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
    apply_correspondence_author_requirements = False
    """Whether the form marks the fields required for submission (see `CorrespondenceAuthorRequirementsFormMixin`)."""

    def get(self, *args, **kwargs):
        self.next_url = self.request.GET.get("next", None)
        return super().get(*args, **kwargs)

    @property
    def next_url(self):
        """
        Get the next URL stored in the session.

        This property fetches the value of the "next" key from the session object
        associated with the request.

        :returns: The value of the "next" key in the session, or None if it is not set.
        """
        return self.request.session.get("next")

    @next_url.setter
    def next_url(self, value):
        """
        Set the 'next' URL value in the current session.

        This setter updates the "next" key in the request session with the provided value.
        It is typically used to store a redirection target URL.

        :param value: The value to set the 'next' key in the session.
        """
        referer = self.request.headers.get("referer", "")
        profile_url = reverse("core_edit_profile")
        submission_url = reverse("wjs_submission_1")
        if profile_url not in referer and submission_url not in referer:
            value = None
        if not value:
            return
        self.request.session["next"] = value

    @property
    def back_to_submission_url(self) -> str | None:
        """
        Return the URL to go back to, if the user came here from the submission, and it is safe to follow.

        The profile is split in multiple views, so we cannot tell when it is complete: the user is not
        redirected automatically after saving, a "back to submission" link is shown instead.

        :return: The stored "next" URL, or None if it is missing or points to another site.
        """
        return safe_back_to_submission_url(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back_to_submission_url"] = self.back_to_submission_url
        return context

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
        if self.apply_correspondence_author_requirements:
            kwargs["correspondence_author_requirements"] = get_correspondence_author_requirements(self.request)
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


class KeywordsHierarchyContextMixin:
    """
    Add the hierarchical keywords structure to the context for keyword-selection pages.

    For journals with hierarchical keywords (e.g. JHEP, JQuant) the context gets the
    top-level keyword groups (``keywords_list``) and the journal keywords that belong
    to no group (``ungrouped_keywords``), so that templates can render the same
    group/subgroup accordion used in submission step 3. For the other journals
    (e.g. JCOM, JCOMAL) templates keep the flat first-letter accordion.

    Only active keywords are exposed: deactivated keywords and the groups left without active keywords are hidden.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        journal = self.request.journal
        hierarchical = bool(journal and journal.submissionconfiguration.hierarchical_keywords)
        context["hierarchical_keywords"] = hierarchical
        if hierarchical:
            context["keywords_list"] = get_keywords_by_journal(journal)
            context["ungrouped_keywords"] = (
                get_active_keywords(journal).filter(group__isnull=True).exclude(word="").order_by("word")
            )
        return context


class ProfilePersonalEditView(BaseProfileEditView):
    form_class = WjsPersonalInfoForm
    apply_correspondence_author_requirements = True
    template_name = "wjs/profile/personal_edit.html"
    success_url = reverse_lazy("core_edit_profile")
    update_message = _("Profile updated.")


class ProfileEmailEditView(BaseProfileEditView):
    form_class = WjsEmailChangeForm
    apply_correspondence_author_requirements = True
    template_name = "wjs/profile/personal_email_edit.html"
    success_url = reverse_lazy("core_edit_profile_email")
    update_message = _("Alternative email updated.")
    update_message_2 = _("Please, check your inbox.")

    def form_valid(self, form):
        try:
            alternative_email_updated = form.cleaned_data["alternative_email"] and hasattr(
                self.request.user, "jcomprofile"
            )
            if form.cleaned_data["new_email"] or alternative_email_updated:
                if alternative_email_updated:
                    self.request.user.jcomprofile.alternative_email = form.cleaned_data["alternative_email"]
                    self.request.user.jcomprofile.save()
                    messages.success(self.request, self.update_message)

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
    apply_correspondence_author_requirements = True
    template_name = "wjs/profile/personal_info_edit.html"
    success_url = reverse_lazy("core_edit_profile_additional")
    update_message = _("Information updated.")


class ProfileInterestsEditView(KeywordsHierarchyContextMixin, BaseProfileEditView):
    model = JCOMProfile
    form_class = WjsInterestsForm
    template_name = "wjs/profile/personal_interests_edit.html"
    success_url = reverse_lazy("core_edit_profile_interests")
    update_message = _("Interests updated.")


class ProfileBackToSubmissionView(LoginRequiredMixin, View):
    """Forget the stored "next" URL and send the user back to it (the submission they came from)."""

    def get(self, request, *args, **kwargs):
        next_url = request.session.pop("next", None)
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect("core_edit_profile")
