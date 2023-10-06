from typing import Optional

from django.conf import settings
from django.utils.module_loading import import_string
from events import logic as events_logic
from review import models as review_models
from submission import models as submission_models
from wjs_review.events.assignment import dispatch_assignment

from ..models import ArticleWorkflow
from . import ReviewEvent


def on_article_submitted(**kwargs) -> None:
    """Sync ArticleWorkflow state with article on submission."""
    article = kwargs["article"]
    if (
        article.stage == submission_models.STAGE_UNASSIGNED
        and article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION
    ):
        article.articleworkflow.author_submits_paper()
        article.articleworkflow.save()
        kwargs = {"workflow": article.articleworkflow}
        review_models.ReviewRound.objects.create(article=article, round_number=1)
        events_logic.Events.raise_event(ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED, task_object=article, **kwargs)


def on_workflow_submitted(**kwargs) -> None:
    """When ArticleWorkflow is marked as submitted, the process filtering tasks are run."""
    workflow = kwargs["workflow"]
    workflow.system_process_submission()
    workflow.save()


def dispatch_checks(article: submission_models.Article) -> Optional[bool]:
    """
    Run sanity checks on article.

    If checks are successful, dispatch assignment to editor and return True is assignment is created, False otherwise.

    If checks are unsuccessful, return None.

    :py:function:`wjs_review.events.handlers.dispatch_checks` run functions registered per journal in
    `settings.WJS_REVIEW_CHECK_FUNCTIONS`: if any fails, the whole check is considered failed.
    """
    journal = article.journal.code
    checks_functions = settings.WJS_REVIEW_CHECK_FUNCTIONS.get(
        journal,
        settings.WJS_REVIEW_CHECK_FUNCTIONS.get(None, []),
    )
    for check_function in checks_functions:
        status = import_string(check_function)(article)
        if not status:
            return None

    assignment = dispatch_assignment(article=article)
    return bool(assignment)


def on_revision_complete(**kwargs) -> None:
    """When a new article revision is submitted, start the revision process again."""
    article = kwargs["revision"].article
    article.articleworkflow.author_submits_again()
    new_round_number = article.current_review_round() + 1
    review_models.ReviewRound.objects.create(article=article, round_number=new_round_number)
    article.articleworkflow.save()
    article.stage = submission_models.STAGE_ASSIGNED
    article.save()
