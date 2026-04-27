import subprocess
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from submission.models import Article

from ...logic import BaseConvertLatexReport
from ...models import WjsEditorAssignment


class Command(BaseCommand):
    help = "Render an editor report's LaTeX for the given article, optionally compile it to PDF."  # noqa A003

    def add_arguments(self, parser):
        parser.add_argument("article_id", type=int, help="ID of the article")
        parser.add_argument(
            "--content",
            type=str,
            default=None,
            help="Path to a .tex file whose content is used as the report text",
        )
        parser.add_argument(
            "--pdf",
            action="store_true",
            default=False,
            help="Compile the rendered LaTeX to PDF and open it",
        )

    def handle(self, *args, **options):
        article_id = options["article_id"]
        try:
            article = Article.objects.get(id=article_id)
        except Article.DoesNotExist:
            raise CommandError(f"Article {article_id} does not exist") from Article.DoesNotExist

        assignment = WjsEditorAssignment.objects.get_current(article)

        if options["content"]:
            report_text = Path(options["content"]).read_text(encoding="utf-8")
        else:
            report_text = r"Euler's identify: $e^{i\pi}+1=0$"

        converter = BaseConvertLatexReport(report_text=report_text, instance=assignment)
        report = converter._prepare_tex_file()  # noqa: SLF001

        if options["pdf"]:
            with tempfile.NamedTemporaryFile(suffix=".tex", delete=False) as tex_f:
                tex_f.write(report)
                tex_path = Path(tex_f.name)
            result = subprocess.run(
                ["latexmk", "-pdf", tex_path.name],
                cwd=tex_path.parent,
                check=True,
            )
            if result.returncode == 0:
                pdf_path = tex_path.with_suffix(".pdf")
                subprocess.run(["xdg-open", str(pdf_path)])

        self.stdout.write(report.decode("utf-8"))
