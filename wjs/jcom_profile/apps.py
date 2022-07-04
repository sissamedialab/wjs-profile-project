"""Configure this application."""
# https://docs.djangoproject.com/en/4.0/ref/applications/
from django.apps import AppConfig
# import logging


class JCOMProfileConfig(AppConfig):
    """Configuration for this django app."""

    name = "wjs.jcom_profile"
    verbose_name = 'WJS JCOM profile'

    def ready(self):
        """Call during initialization."""
        # import ipdb; ipdb.set_trace()
        from wjs.jcom_profile import signals
        from wjs.jcom_profile import urls

        # logging.warning("✨CALLED")
        self.register_hooks()

    def register_hooks(self):
        """Register my functions to Janeway's hooks."""
        hooks = [
            # dict(nav_block=dict(module='wjs.jcom_profile.hooks',
            #                     function='prova_hook')),
            dict(extra_corefields=dict(module='wjs.jcom_profile.hooks',
                                       function='prova_hook')),
        ]
        # NB: do not `import core...` before `ready()`,
        # otherwise django setup process breaks
        from core import plugin_loader
        plugin_loader.register_hooks(hooks)
