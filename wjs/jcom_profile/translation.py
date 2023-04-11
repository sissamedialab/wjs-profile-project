"""Register models for translation."""
from journal.models import Issue
from modeltranslation.translator import TranslationOptions, register
from submission.models import Keyword


@register(Keyword)
class KeywordTranslationOptions(TranslationOptions):
    fields = ("word",)


@register(Issue)
class IssueTranslationOptions(TranslationOptions):
    fields = ("issue_title", "issue_description")
