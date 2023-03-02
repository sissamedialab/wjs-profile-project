from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from journal.models import Journal

from .plugin_settings import MANAGER_URL


class PluginConfig(models.Model):
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE, related_name="wjs_latest_news_plugin_config")
    title = models.CharField(max_length=500, help_text=_("Section title"))
    count = models.PositiveSmallIntegerField(default=10, help_text=_("Number of items shown in the home page"))

    def __str__(self):
        return f"Configuration for journal {self.journal}"

    def get_absolute_url(self):
        return reverse(MANAGER_URL)
