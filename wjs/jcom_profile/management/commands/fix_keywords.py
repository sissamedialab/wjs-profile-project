from django.conf import settings
from django.core.management.base import BaseCommand
from submission.models import Keyword


class Command(BaseCommand):
    help = "Fix keywords case."  # noqa

    def handle(self, *args, **options):

        for keyword in Keyword.objects.all():
            for lang in settings.MODELTRANSLATION_FALLBACK_LANGUAGES["default"]:
                word = getattr(keyword, f"word_{lang}")
                if word:
                    word = word.lower().capitalize()
                    setattr(keyword, f"word_{lang}", word)
                    keyword.save()
