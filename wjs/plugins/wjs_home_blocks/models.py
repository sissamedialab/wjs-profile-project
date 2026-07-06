from core.model_utils import JanewayBleachField
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from journal.models import Journal

from .plugin_settings import MANAGER_URL


class PluginConfig(models.Model):
    journal = models.ForeignKey(
        Journal,
        on_delete=models.CASCADE,
        related_name="wjs_home_blocks_plugin_config",
    )
    main_column_items = models.PositiveSmallIntegerField(default=1, verbose_name=_("Number of items in main column"))
    title_1 = models.CharField(max_length=500, default="", verbose_name=_("Title block 1"))
    content_1 = JanewayBleachField(default="", verbose_name=_("Content block 1"))
    title_2 = models.CharField(max_length=500, default="", verbose_name=_("Title block 2"))
    content_2 = JanewayBleachField(default="", verbose_name=_("Content block 2"))
    title_3 = models.CharField(max_length=500, default="", verbose_name=_("Title block 3"))
    content_3 = JanewayBleachField(default="", verbose_name=_("Content block 3"))
    title_4 = models.CharField(max_length=500, default="", verbose_name=_("Title block 4"))
    content_4 = JanewayBleachField(default="", verbose_name=_("Content block 4"))
    title_login_box_auth = models.CharField(
        max_length=500, default="", verbose_name=_("Title Login Box - Logged in users")
    )
    content_login_box_auth = JanewayBleachField(
        max_length=500, default="", verbose_name=_("Content Login Box - Logged in users")
    )
    title_login_box_unauth = models.CharField(
        max_length=500, default="", verbose_name=_("Title Login Box - Non logged in users")
    )
    content_login_box_unauth = JanewayBleachField(
        max_length=500, default="", verbose_name=_("Content Login Box - Non logged in users")
    )
    show_login_in_personal_area = models.BooleanField(
        default=False, verbose_name=_("Show login box in personal area block")
    )
    title_personal_area_auth = models.CharField(
        max_length=500, default="", verbose_name=_("Title Personal area - Logged in users")
    )
    content_personal_area_auth = JanewayBleachField(
        max_length=500, default="", verbose_name=_("Content Personal area - Logged in users")
    )
    title_personal_area_unauth = models.CharField(
        max_length=500, default="", verbose_name=_("Title Personal area - Non logged in users")
    )
    content_personal_area_unauth = JanewayBleachField(
        max_length=500, default="", verbose_name=_("Content Personal area - Non logged in users")
    )

    def __str__(self):
        return f"Configuration for journal {self.journal}"

    def get_absolute_url(self):
        return reverse(MANAGER_URL)
