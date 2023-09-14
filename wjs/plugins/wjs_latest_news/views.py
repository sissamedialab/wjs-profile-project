from django.utils.decorators import method_decorator
from security.decorators import editor_user_required
from utils.decorators import GET_language_override

from wjs.jcom_profile.plugins import BaseConfigUpdateView

from .models import PluginConfig
from .plugin_settings import PLUGIN_NAME


@method_decorator(GET_language_override, "dispatch")
@method_decorator(editor_user_required, "dispatch")
class ConfigUpdateView(BaseConfigUpdateView):
    model = PluginConfig
    fields = ["title", "count", "secondbox_title", "secondbox_tag", "secondbox_count"]
    plugin_name = PLUGIN_NAME
