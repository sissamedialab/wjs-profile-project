"""Configure this application."""
# https://docs.djangoproject.com/en/4.0/ref/applications/
from django.apps import AppConfig
from django.conf import settings
import logging


class JCOMProfileConfig(AppConfig):
    """Configuration for this django app."""

    name = "wjs.jcom_profile"
    verbose_name = 'WJS JCOM profile'

    def ready(self):
        """Call during initialization."""
        # import ipdb; ipdb.set_trace()
        from wjs.jcom_profile import signals
        # from wjs.jcom_profile import monkey_patching
        from wjs.jcom_profile import urls

        logging.warning("✨CALLED")
        self.register_hooks()

    def register_hooks(self):
        """Register my functions to Janeway's hooks."""
        hooks = [
            dict(nav_block=dict(module='wjs.jcom_profile.hooks',
                                function='prova_hook')),
        ]
        # import ipdb; ipdb.set_trace()
        # from core/plugin_loader.py:64
        # Register plugin hooks
        if settings.PLUGIN_HOOKS:
            super_hooks = settings.PLUGIN_HOOKS
        else:
            settings.PLUGIN_HOOKS = {}
            super_hooks = {}

        for _dict in hooks:
            if _dict:
                for k, v in _dict.items():
                    super_hooks.setdefault(k, []).append(v)

        for k, v in super_hooks.items():
            settings.PLUGIN_HOOKS[k] = v
