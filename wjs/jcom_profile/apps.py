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
        self.register_events()

    def register_hooks(self):
        """Register my functions to Janeway's hooks."""
        hooks = [
            {"extra_corefields": {"module": "wjs.jcom_profile.hooks", "function": "prova_hook"}},
        ]
        # NB: do not `import core...` before `ready()`,
        # otherwise django setup process breaks
        from core import plugin_loader

        plugin_loader.register_hooks(hooks)

    def register_events(self):
        """Register our function in Janeway's events logic."""
        from events import logic as events_logic

        from wjs.jcom_profile.events.wjs_events import (
            notify_coauthors_article_submission,
        )

        events_logic.Events.register_for_event(
            events_logic.Events.ON_ARTICLE_SUBMITTED,
            notify_coauthors_article_submission,
        )
