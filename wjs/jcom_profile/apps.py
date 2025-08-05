"""Configure this application."""

from pathlib import Path

# https://docs.djangoproject.com/en/4.0/ref/applications/
from django.apps import AppConfig
from django.conf import settings


class JCOMProfileConfig(AppConfig):
    """Configuration for this django app."""

    name = "wjs.jcom_profile"
    verbose_name = "WJS JCOM profile"
    path = Path(__file__).parent.absolute()

    def ready(self):
        """Call during initialization."""
        # TODO: Clarify this line (unused import but without them process breaks)
        from wjs.jcom_profile import signals, urls  # NOQA

        # Inject bas jcom templates directory to allow overriding templates from wjs-themes to inject wjs templates
        settings.TEMPLATES[0]["DIRS"].insert(0, self.path / "templates")
        self.register_hooks()

    def register_hooks(self):
        """Register my functions to Janeway's hooks."""
        hooks = [
            {"extra_corefields": {"module": "wjs.jcom_profile.hooks", "function": "extra_core_fields_hook"}},
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
