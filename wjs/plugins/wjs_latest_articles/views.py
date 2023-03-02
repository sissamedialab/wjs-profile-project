from django.http import Http404
from django.views.generic import UpdateView

from wjs.jcom_profile.plugins import BaseConfigUpdateView
from .models import PluginConfig
from .plugin_settings import PLUGIN_NAME


class ConfigUpdateView(BaseConfigUpdateView):
    model = PluginConfig
    fields = ["title", "count"]
    plugin_name = PLUGIN_NAME
