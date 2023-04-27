"""Register models for translation."""
from modeltranslation.translator import TranslationOptions, register

from .models import PluginConfig


@register(PluginConfig)
class PluginConfigOptions(TranslationOptions):
    fields = ("title",)
