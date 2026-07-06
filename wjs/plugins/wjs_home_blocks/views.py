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
    fields = (
        "main_column_items",
        "show_login_in_personal_area",
        "title_1",
        "content_1",
        "title_2",
        "content_2",
        "title_3",
        "content_3",
        "title_4",
        "content_4",
        "title_login_box_auth",
        "content_login_box_auth",
        "title_login_box_unauth",
        "content_login_box_unauth",
        "title_personal_area_auth",
        "content_personal_area_auth",
        "title_personal_area_unauth",
        "content_personal_area_unauth",
    )
    plugin_name = PLUGIN_NAME
