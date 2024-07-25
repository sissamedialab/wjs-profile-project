import glob
import io
import os
import random
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Callable
from unittest import mock

import pytest
from core import files
from core import models as core_models
from core.models import (
    Account,
    File,
    Galley,
    SupplementaryFile,
    Workflow,
    WorkflowElement,
)
from django.core import mail
from django.core.files import File as DjangoFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.http import HttpRequest
from events import logic as events_logic
from journal.models import Issue
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from review import models as review_models
from submission.models import Article
from utils import setting_handler

from wjs.jcom_profile.models import Genealogy
from wjs.jcom_profile.tests.conftest import *  # noqa

from ..events import ReviewEvent
from ..logic import (
    AssignToEditor,
    AssignTypesetter,
    AuthorSendsCorrections,
    HandleDecision,
    ReadyForPublication,
    RequestProofs,
    UploadFile,
    VerifyProductionRequirements,
)
from ..models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    LatexPreamble,
    Message,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from ..plugin_settings import (
    HANDSHAKE_URL,
    SHORT_NAME,
    STAGE,
    set_default_plugin_settings,
)
from .test_helpers import (
    ThreadedHTTPServer,
    _create_review_assignment,
    create_mock_tar_gz,
)

TEST_FILES_EXTENSION = ".santaveronica"


def pytest_addoption(parser):
    """Allow for marking tests as "academic" and run them on demand only.

    Tests marked with:
    @pytest.mark.skipif("not config.getoption('--run-academic')")
    will be run only if pytest is invoked as
    pytest --run-academic ...
    """
    # see also https://jwodder.github.io/kbits/posts/pytest-mark-off/#option-2-use-pytest-mark-skipif
    parser.addoption(
        "--run-academic",
        action="store_true",
        default=False,
        help="Run academic tests",
    )


def cleanup_notifications_side_effects():
    """Clean up messages and notifications."""
    mail.outbox = []
    Message.objects.all().delete()


@pytest.fixture
def review_settings(journal, eo_user):
    """
    Initialize plugin settings and install wjs_review as part of the workflow.

    It must be declared as first fixture in the test function to ensure it's called before the other fixtures.
    """
    set_default_plugin_settings(force=True)
    # TODO: use plugin_settings.ensure_workflow_elements ?
    workflow = Workflow.objects.get(journal=journal)
    workflow.elements.filter(element_name="review").delete()
    workflow.elements.add(
        WorkflowElement.objects.create(
            element_name=SHORT_NAME,
            journal=journal,
            order=0,
            stage=STAGE,
            handshake_url=HANDSHAKE_URL,
        ),
    )


def _assign_article(fake_request, article, section_editor) -> Article:
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()
    assignment = AssignToEditor(
        article=article, editor=section_editor, request=fake_request, first_assignment=True
    ).run()
    workflow = assignment.article.articleworkflow
    workflow.refresh_from_db()
    assert workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    cleanup_notifications_side_effects()
    return workflow.article


@pytest.fixture
def assigned_article(fake_request, article, section_editor, review_settings) -> Article:
    """
    Assign an editor to an article.

    By default the assignment creates notifications (one mail and one message), and this can give problems
    in the tests using this fixture, because they have to distinguish between these notifications and the
    ones that are to be checked during the test itself.

    Calling the cleanup_notifications_side_effects() function here will remove the AssignToEditor() mail and
    message, so that the test using this fixture can check the notifications created *during* the test without
    interferences and without knowing the side effects of the fixture or of AssignToEditor().
    """
    return _assign_article(fake_request, article, section_editor)


def _accept_article(
    fake_request: HttpRequest,
    article: Article,
) -> Article:
    form_data = {
        "decision": ArticleWorkflow.Decisions.ACCEPT,
        "decision_editor_report": "Some editor report",
        "decision_internal_note": "Some internal note",
        "withdraw_notice": "Some withdraw notice",
    }
    assert fake_request.user is not None
    editor_decision = HandleDecision(
        workflow=article.articleworkflow,
        form_data=form_data,
        user=fake_request.user,
        request=fake_request,
    ).run()
    workflow = editor_decision.workflow
    # An accepted article can be moved to READY_FOR_TYPESETTER (most common case) or be left in ACCEPTED state if there
    # are issues that must be resolved before the paper is ready for tyepsetters.
    assert workflow.state in (
        ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
        ArticleWorkflow.ReviewStates.ACCEPTED,
    )
    cleanup_notifications_side_effects()
    return workflow.article


@pytest.fixture
def accepted_article(fake_request, assigned_article) -> Article:
    """Create and return an accepted article.

    See notes about notifications in `assigned_article`.

    Remember that accepted != ready-for-typesetter
    """
    if fake_request.user is None:
        # This can happen when this fixture is called by other fixtures
        # In this case it should be safe to assume that the editor assigned to the article is performing the acceptance
        # (which is the most common case)
        fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    return _accept_article(fake_request, assigned_article)


def _ready_for_typesetter_article(article: Article) -> Article:
    workflow = article.articleworkflow
    if workflow.state == ArticleWorkflow.ReviewStates.ACCEPTED:
        workflow = VerifyProductionRequirements(articleworkflow=workflow).run()
    assert workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    cleanup_notifications_side_effects()
    return workflow.article


@pytest.fixture
def ready_for_typesetter_article(accepted_article: Article) -> Article:
    """Create and return an ready_for_typed article.

    See notes about notifications in `assigned_article`.
    """
    return _ready_for_typesetter_article(accepted_article)


def _assigned_to_typesetter_article(
    article: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> Article:
    typesetting_assignment = AssignTypesetter(article, typesetter, fake_request).run()
    workflow = typesetting_assignment.round.article.articleworkflow
    assert workflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
    assert workflow.production_flag_galleys_ok == ArticleWorkflow.GalleysStatus.NOT_TESTED
    cleanup_notifications_side_effects()
    return workflow.article


@pytest.fixture
def assigned_to_typesetter_article(
    ready_for_typesetter_article: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> Article:
    """Create and return an article assigned to a typesetter.

    See notes about notifications in `assigned_article`.
    """
    return _assigned_to_typesetter_article(ready_for_typesetter_article, typesetter, fake_request)


def _assigned_to_typesetter_article_with_parent(
    article: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> Article:
    parent_article = Article.objects.create(
        title="Parent article",
        journal=article.journal,
    )
    Genealogy.objects.create(parent=parent_article)
    parent_article.genealogy.children.add(article)

    parent_article.articleworkflow.latex_desc = "parent article desc"
    parent_article.articleworkflow.save()
    article.articleworkflow.latex_desc = "child article desc"
    article.articleworkflow.save()
    AssignTypesetter(article, typesetter, fake_request).run()
    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
    cleanup_notifications_side_effects()
    return article


@pytest.fixture
def assigned_to_typesetter_article_with_parent(
    ready_for_typesetter_article: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> Article:
    """Create and return an article assigned to a typesetter with a parent article, both with a latex_desc."""
    return _assigned_to_typesetter_article_with_parent(ready_for_typesetter_article, typesetter, fake_request)


def _assigned_to_typesetter_article_with_files_to_typeset(
    assigned_to_typesetter_article: Article, typesetter: Account, fake_request: HttpRequest, zip_with_tex_with_query
) -> Article:
    assignment = assigned_to_typesetter_article.typesettinground_set.first().typesettingassignment
    fake_request.user = typesetter
    article_with_file = UploadFile(
        typesetter=typesetter,
        request=fake_request,
        assignment=assignment,
        file_to_upload=zip_with_tex_with_query(assigned_to_typesetter_article),
    ).run()
    article_with_file.articleworkflow.save()
    return article_with_file


@pytest.fixture
def assigned_to_typesetter_article_with_files_to_typeset(
    assigned_to_typesetter_article: Article,
    fake_request: HttpRequest,
    typesetter: Account,
    zip_with_tex_with_query,
) -> Article:
    """Return an assigned to typesetter article with files to typeset."""
    return _assigned_to_typesetter_article_with_files_to_typeset(
        assigned_to_typesetter_article, typesetter, fake_request, zip_with_tex_with_query
    )


def _stage_proofing_article(
    article: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> Article:
    typesetting_assignment = TypesettingAssignment.objects.get(
        round=article.typesettinground_set.first(),
        typesetter=typesetter,
    )
    RequestProofs(
        assignment=typesetting_assignment,
        typesetter=typesetter,
        request=fake_request,
        workflow=article.articleworkflow,
    ).run()
    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.PROOFREADING
    cleanup_notifications_side_effects()
    return article


@pytest.fixture
def stage_proofing_article(
    assigned_to_typesetter_article_with_files_to_typeset: Article,
    typesetter: Account,
    fake_request: HttpRequest,
) -> Article:
    """Create and return an article in proofreading."""
    return _stage_proofing_article(assigned_to_typesetter_article_with_files_to_typeset, typesetter, fake_request)


def _assigned_to_typesetter_proofs_done_article(
    article: Article,
    fake_request: HttpRequest,
) -> Article:
    """Let the author proof-read an article.

    Assume the given article is in the PROOFREADING state.
    """
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.PROOFREADING
    old_typesetting_assignment = article.typesettinground_set.last().typesettingassignment
    author = article.correspondence_author
    # A GalleyProofing object is created when the paper is sent from the typ to the author.
    # The author can upload files and add notes.
    # Then he sends the paper back to the typ.
    author_proofs: GalleyProofing = old_typesetting_assignment.round.galleyproofing_set.first()
    author_proofs.notes = "Dear typ, please correct this and that... 🙂"
    author_proofs.save()
    AuthorSendsCorrections(
        user=author,
        old_assignment=old_typesetting_assignment,
        request=fake_request,
    ).run()
    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
    cleanup_notifications_side_effects()
    return article


@pytest.fixture
def assigned_to_typesetter_proofs_done_article(
    stage_proofing_article: Article,
    fake_request: HttpRequest,
) -> Article:
    """Create and return an article assigned to a typesetter after the author has done some proofs.

    See notes about notifications in `assigned_article`.
    """
    return _assigned_to_typesetter_proofs_done_article(ready_for_typesetter_article, fake_request)


def _create_generic_galleys(article: Article) -> [Galley, Galley, Galley]:
    """Create a PDF, an epub and an HTML "files" wrapped into a Galley object."""
    pdf_corefile = File.objects.create(
        mime_type="application/pdf",
        original_filename="of.pdf",
        uuid_filename="uf.pdf",
        is_galley=True,
    )
    pdf_galley = Galley.objects.create(file=pdf_corefile, label="PDF", type="pdf", article=article)

    epub_corefile = File.objects.create(
        mime_type="application/epub+zip",
        original_filename="of.epub",
        uuid_filename="uf.epub",
        is_galley=True,
    )
    epub_galley = Galley.objects.create(file=epub_corefile, label="EPUB", type="epub", article=article)

    html_corefile = File.objects.create(
        mime_type="text/html",
        original_filename="of.html",
        uuid_filename="uf.html",
        is_galley=True,
    )
    html_galley = Galley.objects.create(file=html_corefile, label="HTML", type="html", article=article)

    return (pdf_galley, epub_galley, html_galley)


def _create_rfp_article(
    article: Article,
    issue: Issue,
    user: Account,
    request: HttpRequest,
) -> Article:
    """Create an article ready for publication."""
    article.primary_issue = issue
    article.save()

    # Force the article to have a section named "article" to ease pubid/doi generation
    article.section.name = "Article"
    article.section.save()
    article.section.wjssection.doi_sectioncode = "02"
    article.section.wjssection.pubid_and_tex_sectioncode = "A"
    article.section.wjssection.save()

    # At this point of their life, articles should already have one (and only one) source file
    if article.source_files.count() == 0:
        # the upstream fixtures did not oblige: let's fix the problem;
        # create a dummy file, with nothing on the filesystem
        # a janeway file can apparently exists without django file (e.g. DjangoFile(file=BytesIO(b"hi"), name="x.zip"))
        janeway_file = File.objects.create(
            mime_type="application/zip",
            original_filename="original_filename.zip",
            uuid_filename="uf.zip",
            label="label",
            description="description",
            owner=article.correspondence_author,
            is_galley=False,
            article_id=article.pk,
        )
        article.source_files.set((janeway_file,))

    article.articleworkflow.production_flag_no_queries = True
    article.articleworkflow.production_flag_no_checks_needed = True
    article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
    article.galley_set.set(_create_generic_galleys(article=article))
    article.articleworkflow.save()
    ReadyForPublication(workflow=article.articleworkflow, user=user).run()
    article.refresh_from_db()
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION
    article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
    return article


@pytest.fixture
def rfp_article(
    assigned_to_typesetter_article_with_files_to_typeset: Article,
    fb_issue: Issue,
    fake_request: HttpRequest,
) -> Article:
    """Create an article in ready-for-publication."""
    typesetter = (
        assigned_to_typesetter_article_with_files_to_typeset.articleworkflow.latest_typesetting_assignment().typesetter
    )
    article = _create_rfp_article(
        article=assigned_to_typesetter_article_with_files_to_typeset,
        issue=fb_issue,
        user=typesetter,
        request=fake_request,
    )
    return article


@pytest.fixture(scope="session")
def http_server():
    # we need a random port to avoid concurrency issues
    random_port = random.randint(2702, 12702)  # ⇠ my birthday 🎂
    server = ThreadedHTTPServer("localhost", random_port)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def submitted_workflow(
    journal: journal_models.Journal,  # noqa
    create_submitted_articles: Callable,  # noqa
) -> ArticleWorkflow:
    article = create_submitted_articles(journal, count=1)[0]
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
    article.articleworkflow.save()
    return article.articleworkflow


@pytest.fixture
def review_form(journal) -> review_models.ReviewForm:
    current_setting = setting_handler.get_setting(
        "general",
        "default_review_form",
        journal,
    ).value
    if current_setting:
        return review_models.ReviewForm.objects.get(pk=current_setting)
    else:
        review_form = review_models.ReviewForm.objects.create(
            name="A Form",
            intro="i",
            thanks="t",
            journal=journal,
        )

        review_form_element, __ = review_models.ReviewFormElement.objects.get_or_create(
            name="Review",
            kind="text",
            order=1,
            width="full",
            required=True,
        )
        review_form.elements.add(review_form_element)
        setting_handler.save_setting(
            "general",
            "default_review_form",
            journal,
            review_form_element.pk,
        )
        return review_form


@pytest.fixture
def review_assignment_invited_user(
    fake_request: HttpRequest,
    invited_user: JCOMProfile,  # noqa: F405
    assigned_article: submission_models.Article,  # noqa: F405
    review_form: review_models.ReviewForm,
) -> WorkflowReviewAssignment:
    """Create an review assignment for an invited (non active / confirmed) user."""
    return _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=invited_user,
        assigned_article=assigned_article,
    )


@pytest.fixture
def review_assignment(
    fake_request: HttpRequest,
    reviewer: JCOMProfile,  # noqa: F405
    assigned_article: submission_models.Article,  # noqa: F405
    review_form: review_models.ReviewForm,
) -> WorkflowReviewAssignment:
    """Create an review assignment for reviewer users."""
    return _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=reviewer,
        assigned_article=assigned_article,
    )


@pytest.fixture
def with_no_hooks_for_on_article_workflow_submitted():
    """Disable ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED hook to skip chained events."""
    old_setting = events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED]
    events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED] = []
    yield
    events_logic.Events._hooks[ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED] = old_setting


@pytest.fixture
def cleanup_test_files_from_folder_files(settings):
    """Remove all files with extension .santaveronica from src/files."""
    yield
    for f in glob.glob(f"{settings.BASE_DIR}/files/**/*{TEST_FILES_EXTENSION}", recursive=True):
        os.unlink(f)


@pytest.fixture
def create_set_of_articles_with_assignments(
    fake_request: HttpRequest,
    eo_user: Account,
    journal: journal_models.Journal,  # noqa
    director: Account,
    review_settings,
):
    """
    Create a set of articles with assignments using scenario_review command.

    It's a bit heavy in terms of time of execution but it's the most reliable way to have a significant data set
    for testing the managers and the queries.
    """
    # TODO: Using scenario_review has two drawbacks:
    #  - it's slow
    #  - it ties the tests to a command meant more for local develoment than test purposes
    #  In the future we must evaluate if it's possible to replace this fixture with a more targeted one.
    #  For now it's too much work for little benefit and we must handle other tasks.
    call_command("scenario_review")


@pytest.fixture
def editor_revision(assigned_article: Article, fake_request: HttpRequest) -> EditorRevisionRequest:
    """Return the revision of the article that is in the editor's hands."""
    decision = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data={
            "decision": ArticleWorkflow.Decisions.MAJOR_REVISION,
            "decision_editor_report": "skip",
            "decision_internal_note": "skip",
            "date_due": "2024-01-01",
            "withdraw_notice": "automatic",
        },
        user=WjsEditorAssignment.objects.get_current(assigned_article).editor,
        request=fake_request,
    ).run()
    revision_request = decision.review_round.editorrevisionrequest_set.first()
    file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{assigned_article.pk}_new.pdf")
    assigned_article.manuscript_files.set([file_obj])
    file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{assigned_article.pk}_new.png")
    assigned_article.data_figure_files.set([file_obj])
    file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{assigned_article.pk}_new.txt")
    assigned_article.supplementary_files.set([SupplementaryFile.objects.create(file=file_obj)])
    return revision_request


def _create_supplementary_files(
    article: Article,
    author: Account,
    n: int = 1,
):
    """Nomen Omen."""
    for i in range(n):
        # TODO: conftest fixture
        supplementary_dj = DjangoFile(BytesIO(b"ciao"), f"ESM_file_{i}.txt")
        supplementary_file = files.save_file_to_article(
            supplementary_dj,
            article,
            author,
        )
        supplementary_file.label = "ESM LABEL"
        supplementary_file.description = "Supplementary file description"
        supplementary_file.save()
        supp_file = core_models.SupplementaryFile.objects.create(file=supplementary_file)
        article.supplementary_files.add(supp_file)
    return supp_file


# Could have added this in the method above but supplementary files are handled slightly different. Could RFC this.
def _create_article_files(
    article: Article,
    author: Account,
    n: int = 1,
):
    """Nomen Omen."""
    file_types = {
        "manuscript_files": b"manuscript content",
        "data_figure_files": b"data figure content",
        "source_files": b"source content",
    }

    for file_category, file_content_bytes in file_types.items():
        for i in range(n):
            file_name = f"{file_category[:-1]}_{i}.txt"
            file_data = BytesIO(file_content_bytes)
            django_file = DjangoFile(file_data, file_name)

            file_instance = files.save_file_to_article(
                django_file,
                article,
                author,
            )
            getattr(article, file_category).add(file_instance)


def _create_galleyproofing_proofed_files(
    article: Article,
    author: Account,
    proofing_assignment: GalleyProofing,
    n: int = 1,
):
    """Nomen Omen."""

    for i in range(n):
        for file_type in ["PDF", "epub", "html"]:
            galley_dj = DjangoFile(BytesIO(b"ciao"), f"Galley_{i}.{file_type}")
            galley_file = files.save_file_to_article(
                galley_dj,
                article,
                author,
            )
            galley_file.label = f"{file_type}"
            galley_file.description = f"{file_type} galley description"
            galley_file.save()
            galley = core_models.Galley.objects.create(
                file=galley_file,
                article=article,
            )
            proofing_assignment.proofed_files.add(galley)

            if file_type == "html":
                article.render_galley = galley
                article.save()


@pytest.fixture
def mock_jcomassistant_post():
    """Fixture to mock requests.post for JcomAssistantClient."""
    import requests

    response = requests.models.Response()
    response.status_code = 200
    response._content = create_mock_tar_gz().getvalue()

    with mock.patch.object(requests, "post", return_value=response) as mocked_requests__post:
        yield mocked_requests__post


@pytest.fixture
def jcom_automatic_preamble(journal: journal_models.Journal):  # noqa
    """Create an automatic preamble for JCOM."""
    preamble_text = """
    {% load wjs_tex %}
    {% with article.title as title %}
    {% with article.date_accepted|date:"Y-m-d" as date_accepted %}
    {% with journal.code as journal %}
    {% with article.section.wjssection.pubid_and_tex_sectioncode as type_code %}
    {% with article.articleworkflow.latex_desc as latex_desc %}
    {% with article.ancestors.first.parent.articleworkflow.latex_desc as latex_desc_parent %}
    {% angular_variables %}
    \\article{<title>}
    \\accepted{<date_accepted>}
    \\journal{<journal>}
    \\doc_type{<type_code>}
    \\latex_desc{<latex_desc>}
    \\latex_desc_parent{<latex_desc_parent>}
    {% endangular_variables %}
    {% endwith %}
    {% endwith %}
    {% endwith %}
    {% endwith %}
    {% endwith %}
    {% endwith %}

    %% Filled-in during publication:
    \\published{???}"
    \\publicationyear{xxxx}"
    \\publicationvolume{xx}"
    \\publicationissue{xx}"
    \\publicationnum{xx}"
    \\doiInfo{https://doi.org/}{doi}"

    """
    automatic_preamble = LatexPreamble.objects.create(
        journal=journal,
        preamble=preamble_text,
    )
    yield automatic_preamble.preamble


def _zip_with_tex_with_query(article: Article) -> SimpleUploadedFile:
    """Create a tar.gz archive containing a .tex file with a query."""
    with open(Path(__file__).parent / "source.tex", "rb") as source:
        tex_content = source.read()
    tex_content = tex_content.replace(
        rb"\begin{document}",
        b"\\proofs{This is a sample query in the document}\n\n\\begin{document}",
    )
    file_obj = io.BytesIO()
    with zipfile.ZipFile(file_obj, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(f"JCOM_{article.id}.tex", tex_content)

    file_obj.seek(0)
    return SimpleUploadedFile("source_tex_file.zip", file_obj.getvalue(), content_type="application/zip")


def _zip_with_tex_without_query(article: Article) -> SimpleUploadedFile:
    """Create a tar.gz archive containing a .tex file with a query."""
    with open(Path(__file__).parent / "source.tex", "rb") as source:
        tex_content = source.read()
    tex_content = tex_content.replace(
        rb"\begin{document}",
        b"\\noproofs{This is not a sample query in the document}\n\n\\begin{document}",
    )
    file_obj = io.BytesIO()
    with zipfile.ZipFile(file_obj, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(f"JCOM_{article.id}.tex", tex_content)

    file_obj.seek(0)
    return SimpleUploadedFile("source_tex_file.zip", file_obj.getvalue(), content_type="application/zip")


@pytest.fixture
def zip_with_tex_with_query() -> Callable:
    return _zip_with_tex_with_query


@pytest.fixture
def zip_with_tex_without_query() -> Callable:
    return _zip_with_tex_without_query


def _jump_article_to_rfp(article: Article, typesetter: Account, request: HttpRequest) -> Article:
    """Quickly (?) bring a pristine paper into ready-for-publication state."""
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    ta: TypesettingAssignment = AssignTypesetter(article, typesetter, request).run()
    assert ta.typesetter == typesetter
    article.refresh_from_db()
    request.user = typesetter
    UploadFile(
        typesetter=typesetter,
        request=request,
        assignment=ta,
        file_to_upload=_zip_with_tex_without_query(article),
    ).run()
    article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
    article.articleworkflow.production_flag_no_checks_needed = True
    article.articleworkflow.production_flag_no_queries = True
    article.articleworkflow.save()

    # TBV: the need to manually set "source_files" here probably indicates a bug: neither UpoadFile nor
    # TypesetterTestsGalleyGeneration set this field. This is related to specs#862
    janeway_file = File.objects.create(
        mime_type="application/zip",
        original_filename="original_filename.zip",
        uuid_filename="uf.zip",
        label="label",
        description="description",
        owner=article.correspondence_author,
        is_galley=False,
        article_id=article.pk,
    )
    article.source_files.set((janeway_file,))

    ReadyForPublication(article.articleworkflow, typesetter).run()
    article.refresh_from_db()
    return article
