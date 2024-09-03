from django.core.management.base import BaseCommand

from ...models import ArticleWorkflow, Section


class Command(BaseCommand):
    help = "List css classes for articleworkflow states."  # noqa: A003

    def handle(self, *args, **options):
        values = []
        for state in ArticleWorkflow.ReviewComputedStates:
            values.append(f"color-state-{state}")
        for state in ArticleWorkflow.ReviewStates:
            values.append(f"color-state-{state}")
        for section in Section.objects.all():
            values.append(f"color-section-{section.pk}")
        for value in values:
            print(f"{value}")
