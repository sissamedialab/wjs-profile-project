"""
Daily management command to recompute attention conditions.

This command is the time-driven leg of the materialized AC architecture.
It complements the event-driven leg (explicit ac_service calls in logic classes).

Design:
  - Scoped by ArticleWorkflow.state: only evaluates ACs relevant to each state.
    See ACStateEvaluator.STATE_AC_MAP in ac_service.py.
  - Idempotent: uses upsert_ac / resolve_ac which are safe to call repeatedly.
  - Provides the correctness floor: re-evaluates ALL ACs (time-based and
    event-based) for articles in active states, so a missed event-driven hook
    is corrected within 24h. NB: ACs left over from a previous state are NOT
    healed (the evaluator only examines codes mapped to the current state).

Should be run via cron or a task scheduler (e.g., Celery, django-cron) once
per day, preferably during low-traffic hours.

References:
  - 260318-SISSA-Specifications-for-attention-conditions.md, New Issue 1
  - 260401-SISSA-Optimize-attention-conditions-DRAFT.md, section
    "Reassessing the centralized-functions requirement"
"""

from django.core.management.base import BaseCommand
from plugins.wjs_review.ac_service import ACStateEvaluator
from plugins.wjs_review.models import ArticleWorkflow


class Command(BaseCommand):
    help = "Rebuild all materialized attention conditions."  # noqa: A003

    def handle(self, **options):
        # Built-in option: run with `-v 0` (e.g. from cron) to emit no output.
        verbosity = options["verbosity"]
        if verbosity:
            self.stdout.write("Rebuilding attention conditions...")

        total_articles = 0
        for state_code in ArticleWorkflow.ReviewStates.values:
            if state_code not in ACStateEvaluator.STATE_AC_MAP:
                # No AC is defined for this state (e.g. archived states such as
                # Published or Rejected): the evaluator would be a no-op, skip
                # the (potentially huge) iteration entirely.
                continue
            workflows = ArticleWorkflow.objects.filter(state=state_code).select_related("article")
            count = workflows.count()
            if count == 0:
                continue

            if verbosity:
                self.stdout.write(f"  State {state_code}: {count} articles")

            for workflow in workflows:
                evaluator = ACStateEvaluator(state=state_code, article=workflow.article)
                evaluator.evaluate_all()
                total_articles += 1

        if verbosity:
            self.stdout.write(self.style.SUCCESS(f"Done. Processed {total_articles} articles."))
