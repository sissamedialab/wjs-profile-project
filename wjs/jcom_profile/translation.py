"""Register models for translation."""

from journal.translation import IssueTranslationOptions
from modeltranslation.translator import TranslationOptions, register
from submission.models import Keyword


@register(Keyword)
class KeywordTranslationOptions(TranslationOptions):
    fields = ("word",)


IssueTranslationOptions.fields += ("issue_title", "issue_description")
