"""Strip an old LaTeX preamble from the TeX reports of an article (wjs/specs#3456).

Reports imported from wjapp can be full LaTeX documents (from `\\documentclass` to `\\end{document}`), but WJS
compiles a report by wrapping it in the journal's `LatexPreamble.report_preamble` and appending `\\end{document}`
(see `BaseConvertLatexReport._prepare_tex_file`): a report carrying its own preamble does not compile.
"""

import datetime
import re

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from journal.models import Journal
from submission.models import Article

from ...logic import states_when_article_is_considered_in_review
from ...models import ArticleWorkflow, EditorDecision, WorkflowReviewAssignment

BEGIN_DOCUMENT = re.compile(r"\\begin\s*\{\s*document\s*\}")
END_DOCUMENT = re.compile(r"\s*\\end\s*\{\s*document\s*\}\s*\Z")
# Last two lines of the banner that closes the report preamble ("write your report in LaTeX ... (no need of
# \begin{document} etc.)"): the report starts right after it.
START_OF_REPORT_BANNER = re.compile(
    r"^[ \t]*%+[ \t]*\(no need of \\begin\{document\} etc\.\)[ \t]*%+[ \t]*\r?\n[ \t]*%+[ \t]*$",
    re.MULTILINE,
)
# First two lines of the "end of report" banner: the report ends right before it.
END_OF_REPORT_BANNER = re.compile(r"^[ \t]*%+[ \t]*\r?\n[ \t]*%+[ \t]*end of report[ \t]*%+[ \t]*$", re.MULTILINE)


def _strip_head(text: str) -> str:
    """Strip up to the end of the start-of-report banner or, if missing, up to the first `\\begin{document}`."""
    match = START_OF_REPORT_BANNER.search(text) or BEGIN_DOCUMENT.search(text)
    if not match:
        return text
    start = match.end()
    return text[start:].lstrip("\r\n")


def _strip_tail(text: str) -> str:
    """Strip from the end-of-report banner or, if missing, a trailing `\\end{document}`."""
    match = END_OF_REPORT_BANNER.search(text)
    if match:
        end = match.start()
        return text[:end].rstrip() + "\n"
    return END_DOCUMENT.sub("\n", text)


def strip_preamble(text: str | None) -> str | None:
    """
    Strip the preamble and what follows the report.

    The head is stripped up to the end of the "write your report in LaTeX" banner, or up to the first
    `\\begin{document}` if there is no banner. The tail is stripped from the "end of report" banner, or just a
    trailing `\\end{document}` if there is no banner.

    Return None if there is nothing to strip.
    """
    if not text:
        return None
    stripped = _strip_tail(_strip_head(text))
    if stripped.strip() == text.strip():
        return None
    return stripped


def _format_date(value: datetime.datetime | None) -> str:
    """Return the local date of a datetime as YYYY-MM-DD."""
    if value is None:
        return "no date"
    return timezone.localtime(value).date().isoformat()


class Command(BaseCommand):
    help = (  # noqa A003
        "Strip the LaTeX preamble and the trailing \\end{document} from the TeX reviewer reports and editor reports "
        "of the given article, asking for confirmation before saving each one."
    )

    def add_arguments(self, parser):
        parser.add_argument("target", help="ID of the article (or journal code, with --suggest)")
        parser.add_argument(
            "--suggest",
            action="store_true",
            help=(
                "Do not change anything: consider the positional argument as a journal code and print the IDs of the "
                "articles in EditorSelected state that have at least a completed review in the latest round."
            ),
        )

    def handle(self, *args, **options):
        """Strip the preamble from every TeX report of the article, after confirmation."""
        if options["suggest"]:
            self._suggest(options["target"])
            return
        try:
            article_id = int(options["target"])
        except ValueError:
            raise CommandError(f"Invalid article ID {options['target']!r}")
        self._strip_article(article_id)

    def _suggest(self, journal_code: str):
        """Print the IDs of the articles of the journal that might need their reports stripped."""
        if not Journal.objects.filter(code=journal_code).exists():
            raise CommandError(f"Journal {journal_code} does not exist")
        workflows = (
            ArticleWorkflow.objects.filter(
                article__journal__code=journal_code,
                state=ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
            )
            .with_completed_review_in_latest_round()
            .order_by("article_id")
        )
        for article_id in workflows.values_list("article_id", flat=True):
            self.stdout.write(str(article_id))

    def _strip_article(self, article_id: int):
        """Strip the preamble from every TeX report of the article, after confirmation."""
        try:
            article = Article.objects.get(id=article_id)
        except Article.DoesNotExist:
            raise CommandError(f"Article {article_id} does not exist")
        try:
            state = article.articleworkflow.state
        except ArticleWorkflow.DoesNotExist:
            raise CommandError(f"Article {article_id} has no workflow")
        if state not in states_when_article_is_considered_in_review:
            raise CommandError(
                f"Refusing to process a paper that does not need any change (article {article_id} is {state})"
            )

        for assignment in WorkflowReviewAssignment.objects.filter(article=article).order_by("pk"):
            self._process_review_assignment(assignment)
        for decision in EditorDecision.objects.filter(workflow__article=article).order_by("pk"):
            self._process_editor_decision(decision)

    def _process_review_assignment(self, assignment: WorkflowReviewAssignment):
        date = assignment.date_complete or assignment.date_requested
        label = f"Reviewer {assignment.reviewer.full_name()} - {_format_date(date)}"
        stripped = self._confirm_strip(label, assignment.report_form_answers.get("author_review_tex"))
        if stripped is not None:
            assignment.report_form_answers["author_review_tex"] = stripped
            assignment.save(update_fields=["report_form_answers"])
            self.stdout.write("Saved")

    def _process_editor_decision(self, decision: EditorDecision):
        label = f"Editor {decision.editor.full_name()} - {_format_date(decision.created)}"
        stripped = self._confirm_strip(label, decision.decision_editor_report)
        if stripped is not None:
            decision.decision_editor_report = stripped
            decision.save(update_fields=["decision_editor_report"])
            self.stdout.write("Saved")

    def _confirm_strip(self, label: str, original: str | None) -> str | None:
        """
        Show the original and the stripped report and ask whether to proceed.

        Return the stripped report if the operator confirms, None otherwise (or if there is nothing to strip).
        """
        self.stdout.write(label)
        stripped = strip_preamble(original)
        if stripped is None:
            self.stdout.write("Nothing to strip")
            return None
        self.stdout.write(
            "\n".join(
                [
                    "—" * 80,
                    original,
                    "🙁" * 80,
                    "🙂" * 80,
                    stripped,
                    "—" * 80,
                ]
            )
        )
        answer = input("Proceed? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            self.stdout.write("Skipped")
            return None
        return stripped
