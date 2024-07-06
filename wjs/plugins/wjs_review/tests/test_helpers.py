from core.models import Workflow, WorkflowElement
from django.http import HttpRequest
from journal.models import Journal
from review.models import ReviewAssignment, ReviewForm
from submission import models as submission_models

from wjs.jcom_profile.models import JCOMProfile

from ..forms import ReportForm
from ..logic import AssignToReviewer, SubmitReview
from ..models import WjsEditorAssignment, WorkflowReviewAssignment
from ..plugin_settings import SHORT_NAME


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
):
    """Run SubmitReview service."""
    form = ReportForm(
        data={str(item.pk): "Fake data" for item in review_form.elements.all()},
        review_assignment=review_assignment,
        fields_required=True,
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
