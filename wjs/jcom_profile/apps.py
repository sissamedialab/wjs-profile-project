from django.apps import AppConfig


class JCOMProfileConfig(AppConfig):
    """Configuration for this django app."""

    name = "wjs_profession"
    verbose_name = 'WJS account profession'

    def ready(self):
        """Call during initialization."""
        from wjs.jcom_profile import signals
        # from wjs.jcom_profile import monkey_patching
        from wjs.jcom_profile import urls
