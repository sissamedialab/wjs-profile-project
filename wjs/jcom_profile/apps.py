"""Configure this application."""

# https://docs.djangoproject.com/en/4.0/ref/applications/
from django.apps import AppConfig


class JCOMProfileConfig(AppConfig):
    """Configuration for this django app."""

    name = "wjs.jcom_profile"
    verbose_name = "WJS JCOM profile"

    def ready(self):
        """Call during initialization."""
        # TODO: Clarify this line (unused import but without them process breaks)
        from wjs.jcom_profile import signals, urls  # NOQA

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
