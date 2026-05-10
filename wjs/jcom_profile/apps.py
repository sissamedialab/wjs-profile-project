"""Configure this application."""

import os.path
from pathlib import Path

# https://docs.djangoproject.com/en/4.0/ref/applications/
from django.apps import AppConfig
from django.conf import settings


class JCOMProfileConfig(AppConfig):
    """Configuration for this django app."""

    name = "wjs.jcom_profile"
    verbose_name = "WJS JCOM profile"
    path = str(Path(__file__).parent.absolute())

    def _prevent_public_email_send(self):
        """
        Force console backend for emails during development.

        Overrides EMAIL_BACKEND with console backend and store the original EMAIL_BACKEND in NEWSLETTER_EMAIL_BACKEND.
        """
        debug_active = settings.DEBUG
        mailpit_configured = settings.EMAIL_PORT == 1025
        if debug_active and not mailpit_configured:
            settings.NEWSLETTER_EMAIL_BACKEND = settings.EMAIL_BACKEND
            settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

    def ready(self):
        """Call during initialization."""
        # TODO: Clarify this line (unused import but without them process breaks)
        from core import forms as core_forms

        from wjs.jcom_profile import forms, signals, urls  # NOQA

        core_forms.RegistrationForm = forms.JCOMRegistrationForm

        # Inject bas jcom templates directory to allow overriding templates from wjs-themes to inject wjs templates
        settings.TEMPLATES[0]["DIRS"].insert(0, os.path.join(self.path, "templates"))
        self.register_hooks()
        self._prevent_public_email_send()

    def register_hooks(self):
        """Register my functions to Janeway's hooks."""
        hooks = [
            {"extra_corefields": {"module": "wjs.jcom_profile.hooks", "function": "extra_core_fields_hook"}},
            {"extra_article_metadata": {"module": "wjs.jcom_profile.hooks", "function": "wjs_section_information"}},
            {
                "extra_edit_profile_parameters": {
                    "module": "wjs.jcom_profile.hooks",
                    "function": "extra_edit_profile_parameters_hook",
                },
            },
            {
                "extra_edit_subscription": {
                    "module": "wjs.jcom_profile.hooks",
                    "function": "extra_edit_subscription_hook",
                },
            },
        ]
        # NB: do not `import core...` before `ready()`,
        # otherwise django setup process breaks
        from core import plugin_loader

        plugin_loader.register_hooks(hooks)
