from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from journal.models import Journal

from .plugin_settings import MANAGER_URL


class PluginConfig(models.Model):
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE, related_name="wjs_subscribe_newsletter_plugin_config")
    title = models.CharField(max_length=500, help_text=_("Section title"))
    intro = models.CharField(max_length=500, help_text=_("Introduction text"))

    def __str__(self):
        return f"Configuration for journal {self.journal}"

    def get_absolute_url(self):
        return reverse(MANAGER_URL)
