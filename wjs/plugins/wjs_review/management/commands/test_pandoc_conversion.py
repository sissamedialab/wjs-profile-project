from pathlib import Path

import pypandoc
from django.conf import settings
from django.core.management.base import BaseCommand

from ...models import WorkflowReviewAssignment


class Command(BaseCommand):
    help = "Convert last N WorkflowReviewAssignment author_review fields from HTML to LaTeX."  # noqa

    def add_arguments(self, parser):
        parser.add_argument("-n", type=int, help="Number of latest WorkflowReviewAssignment to process")
        parser.add_argument("--out", type=str, default="latex_exports", help="Output folder name")

    def handle(self, *args, **options):
        n = options["n"]
        out_dir = Path(settings.BASE_DIR) / options["out"]
        out_dir.mkdir(exist_ok=True)

        assignments = WorkflowReviewAssignment.objects.exclude(
            report_form_answers__author_review__isnull=True
        ).order_by("-id")[:n]

        for assignment in assignments:
            author_review = assignment.report_form_answers.get("author_review")
            if not author_review:
                continue
            latex_text = pypandoc.convert_text(author_review, "latex", format="html")
            file_path = out_dir / f"assignment_{assignment.id}.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(author_review.strip())
                f.write("\n\n" + ("=" * 80) + "\n\n")
                f.write(latex_text.strip())
