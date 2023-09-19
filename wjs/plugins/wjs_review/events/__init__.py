"""
Review submission workflow.

When an article is submitted, this workflow takes over:
- `ON_ARTICLE_SUBMITTED`: :py:func:`wjs_review.events.handlers.on_article_submitted` is called to sync
  :py:class:`wjs_review.models.ArticleWorkflow` state and `ON_ARTICLEWORKFLOW_SUBMITTED` event is raised
- `ON_ARTICLEWORKFLOW_SUBMITTED`: :py:func:`wjs_review.events.handlers.on_workflow_submitted` is called which
  trigger :py:meth:`wjs_review.models.ArticleWorkflow.system_process_submission` which triggers article processing
  via :py:function:`wjs_review.models.process_submission`
- :py:function:`wjs_review.models.process_submission` calls :py:function:`wjs_review.events.handlers.dispatch_checks`
  which runs sanity checks on article and dispatches assignment to editor if checks are successful; it determines the
  target state based on the checks results.
"""


class ReviewEvent:
    ON_ARTICLEWORKFLOW_SUBMITTED = "on_articleworkflow_submitted"
