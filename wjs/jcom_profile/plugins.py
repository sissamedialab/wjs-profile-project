from django import forms
from django.core.exceptions import FieldDoesNotExist
from django.http import Http404
from django.views.generic import UpdateView


class BaseConfigUpdateView(UpdateView):
    template_name = "jcom_profile/plugins/pluginconfig_form.html"

    def _get_translated_fields(self):
        """Gets the translated fields from the list of view fields."""
        for field in self.fields:
            try:
                self.model._meta.get_field(f"{field}_{self.request.override_language}")
                yield f"{field}_{self.request.override_language}"
            except FieldDoesNotExist:
                pass

    def _get_untranslated_fields(self):
        """Gets the untranslated fields from the list of view fields."""
        for field in self.fields:
            try:
                self.model._meta.get_field(f"{field}_{self.request.override_language}")
            except FieldDoesNotExist:
                yield field

    def get_form_class(self):
        """
        Create a form to update untranslated fields and translated fields matching current request.override_language.
        """
        translated_fields = self._get_translated_fields()
        untranslated_fields = self._get_untranslated_fields()

        class PluginFormClass(forms.ModelForm):
            class Meta:
                model = self.model
                fields = list(translated_fields) + list(untranslated_fields)

        return PluginFormClass

    def get_object(self, queryset=None):
        """
        Get or create the configuration model instance for the current journal.

        If used outside a journal, return 404
        """
        if not queryset:
            queryset = self.get_queryset()
        if self.request.journal:
            try:
                return queryset.get(journal=self.request.journal)
            except self.model.DoesNotExist:
                return self.model.objects.create(journal=self.request.journal)
        else:
            raise Http404()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["PLUGIN_NAME"] = self.plugin_name
        return context
