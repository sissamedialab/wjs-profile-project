import io
import pathlib
import re
import tarfile
import threading
import zipfile
from http.server import HTTPServer, SimpleHTTPRequestHandler

import html2text
from core.models import Workflow, WorkflowElement
from django.http import HttpRequest
from journal.models import Journal
from review.models import ReviewAssignment, ReviewForm
from submission import models as submission_models

from wjs.jcom_profile.models import JCOMProfile

from ..logic import AssignToReviewer, SubmitReview
from ..models import WjsEditorAssignment, WorkflowReviewAssignment
from ..plugin_settings import SHORT_NAME
from ..utils import get_report_form

jcom_report_form_data = {
    "conflict_of_interest": "no",
    "structure_and_writing_style": "Good",
    "originality": "Good",
    "scope_and_methods": "Good",
    "argument_and_discussion": "Good",
    "recommendation": "publish",
    "editor_cover_letter": "This is the cover letter to be sent to editors.",
    "author_review": "This is the review to be sent to authors.",
}


def get_next_workflow(journal: Journal) -> WorkflowElement:
    """Return the workflow stage after wjs_review for the given journal."""
    workflow = Workflow.objects.get(journal=journal)
    return (
        workflow.elements.filter(order__gte=workflow.elements.get(element_name=SHORT_NAME).order)
        .exclude(element_name=SHORT_NAME)
        .order_by("order")
        .first()
    )


def _create_review_assignment(
    fake_request: HttpRequest,
    reviewer_user: JCOMProfile,  # noqa: F405
    assigned_article: submission_models.Article,  # noqa: F405
) -> WorkflowReviewAssignment:
    """Create a review assignment."""
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = editor
    assign_service = AssignToReviewer(
        reviewer=reviewer_user.janeway_account,
        workflow=assigned_article.articleworkflow,
        editor=editor,
        form_data={"message": "Message from fixture"},
        request=fake_request,
    )
    return assign_service.run()


def _submit_review(
    review_assignment: ReviewAssignment,
    review_form: ReviewForm,
    fake_request: HttpRequest,
    submit_final: bool = True,
) -> WorkflowReviewAssignment:
    """Run SubmitReview service."""
    report_form = get_report_form(fake_request.journal.code)
    form = report_form(
        data=jcom_report_form_data,
        review_assignment=review_assignment,
        submit_final=submit_final,
        request=fake_request,
    )
    assert form.is_valid()
    submit = SubmitReview(
        assignment=review_assignment,
        form=form,
        submit_final=submit_final,
        request=fake_request,
    )
    submit.run()
    return review_assignment


def create_mock_tar_gz():
    """Create a tar.gz archive containing dummy galleys."""
    here = pathlib.Path(__file__).parent
    galley_name = "galley-x"
    html_filepath = here / f"{galley_name}.html"
    # TODO: drop binary epub (zip), layout source files for benefito of git,
    # and re-compose epub on demand
    epub_filepath = here / f"{galley_name}.epub"
    pdf_filepath = here / f"{galley_name}.pdf"
    log_filepath = here / f"{galley_name}.srvc_log"
    inmemory_targz = io.BytesIO()
    with tarfile.open(fileobj=inmemory_targz, mode="w:gz") as tar:
        tar.add(html_filepath, arcname=f"{galley_name}.html")
        tar.add(epub_filepath, arcname=f"{galley_name}.epub")
        tar.add(pdf_filepath, arcname=f"{galley_name}.pdf")
        tar.add(log_filepath, arcname=f"{galley_name}.srvc_log")
    inmemory_targz.seek(0)
    return inmemory_targz


def create_mock_zip():
    """Create a zip archive containing dummy galleys."""
    here = pathlib.Path(__file__).parent
    galley_name = "galley-x"
    html_filepath = here / f"{galley_name}.html"
    epub_filepath = here / f"{galley_name}.epub"
    pdf_filepath = here / f"{galley_name}.pdf"
    log_filepath = here / f"{galley_name}.srvc_log"
    inmemory_zip = io.BytesIO()
    with zipfile.ZipFile(inmemory_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr("file1.txt", "This is the content of file 1.")
        zipf.write(filename=html_filepath, arcname=html_filepath.name)
        zipf.write(filename=epub_filepath, arcname=epub_filepath.name)
        zipf.write(filename=pdf_filepath, arcname=pdf_filepath.name)
        zipf.write(filename=log_filepath, arcname=log_filepath.name)
    inmemory_zip.seek(0)
    return inmemory_zip


class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Simple server suitable to simulate jcomassistant."""

    def do_POST(self):  # noqa N802 (case)
        """Return always the same valid galleys."""
        if self.path == "/good_galleys":
            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="galleys.tar.gz"')
            self.end_headers()
            inmemory_targz = create_mock_tar_gz()
            self.wfile.write(inmemory_targz.read())
        elif self.path == "/good_galleys_zip":
            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="galleys.zip"')
            self.end_headers()
            inmemory_zip = create_mock_zip()
            self.wfile.write(inmemory_zip.read())
        elif self.path == "/server_error":
            self.send_response(500)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Internal Server Error")
        else:
            super().do_POST()


class ThreadedHTTPServer:
    """Http server that runs in another thread."""

    def __init__(self, host, port):
        server = HTTPServer((host, port), CustomHTTPRequestHandler)
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever)
        self.thread.daemon = True  # This thread dies when the main thread dies

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def raw(string: str) -> str:
    """Simplify the given string.

    - apply html2text
    - drop whitespace
    """
    return re.sub(r"\s+", "", html2text.html2text(string))
