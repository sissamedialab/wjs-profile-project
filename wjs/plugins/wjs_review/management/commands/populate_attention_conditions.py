"""
One-shot management command to populate the AttentionCondition table.

Run once at deploy time to seed the materialized AC table from existing data.
Iterates all ArticleWorkflow records and evaluates all relevant ACs for each.

This command is safe to run multiple times (idempotent via upsert_ac).

After initial population, ongoing maintenance is handled by:
  - Event-driven: ac_service calls in logic classes
  - Time-driven: rebuild_attention_conditions daily task

References:
  - 260318-SISSA-Specifications-for-attention-conditions.md, New Issue 1
  - 260401-SISSA-Optimize-attention-conditions-DRAFT.md
"""

from django.core.management.base import BaseCommand
from plugins.wjs_review.ac_service import ACStateEvaluator
from plugins.wjs_review.models import ArticleWorkflow


class Command(BaseCommand):
    help = "Populate the AttentionCondition table from existing data."  # noqa: A003

    def handle(self, **options):
        # Built-in option: run with `-v 0` to emit no output.
        verbosity = options["verbosity"]
        if verbosity:
            self.stdout.write("Populating attention conditions...")

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
            self.stdout.write(self.style.SUCCESS(f"Done. Populated ACs for {total_articles} articles."))
