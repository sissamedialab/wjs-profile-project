"""Register models for translation."""

from modeltranslation.translator import TranslationOptions, register

from .models import PluginConfig


@register(PluginConfig)
class PluginConfigOptions(TranslationOptions):
    fields = (
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
