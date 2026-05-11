"""Rollback a paper from "accepted" to "assigned-to-editor"."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.termcolors import colorize
from submission.models import STAGE_ACCEPTED, STAGE_ASSIGNED, Article

from wjs.jcom_profile.utils import get_eo_user

from ...communication_utils import log_operation
from ...models import ArticleWorkflow, EditorDecision, Message


def rollback_accepted(article: Article) -> None:
    """
    Rollback an accepted article.

    Raises:
      ValueError: on error.

    """
    if not article.stage == STAGE_ACCEPTED:
        raise ValueError(f"Unexpected article stage {article.stage}. Quitting!")
    # At acceptance, a paper passes through three states;
    # most of the times it ends up automaticcally in ready-for-typesetter
    if article.articleworkflow.state not in {
        ArticleWorkflow.ReviewStates.PAPER_HAS_EDITOR_REPORT,
        ArticleWorkflow.ReviewStates.ACCEPTED,
        ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
    }:
        raise ValueError(f"Unexpected article state {article.articleworkflow.state}. Quitting!")

    with transaction.atomic():
        article.stage = STAGE_ASSIGNED
        article.save()
        article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_SELECTED
        article.articleworkflow.save()

        # Delete acceptance-related core.models.Tasks
        # No specific task found while examinig JCOM_3501.
        # Also, we don't use Tasks;
        # IIC, there is nothing to do here.

        # save editor report in timeline before deleting the decision
        decision = EditorDecision.objects.filter(workflow=article.articleworkflow).order_by("-review_round").first()
        now = timezone.now()
        log_operation(
            article,
            message_subject=f"Rolled-back erroneous acceptance of {now}",
            message_body=decision.decision_editor_report,
            actor=get_eo_user(article),
            recipients=[
                decision.editor,
            ],
            message_type=Message.MessageTypes.USER,
            verbosity=Message.MessageVerbosity.TIMELINE,
            flag_as_read=False,
            flag_as_read_by_eo=True,
        )
        decision.delete()


class Command(BaseCommand):
    help = "Rollback acceptance for an article."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument(
            "article",
            type=str,
            help="Id of the article to rollback acceptance for",
        )

    def handle(self, *args, **options):
        article_id = options["article"]

        try:
            article = Article.objects.get(pk=article_id)
        except Article.DoesNotExist as e:
            raise CommandError(f"Article with id '{article_id}' does not exist") from e

        self.stdout.write(self.style.WARNING(f"You are about to rollback acceptance for article: {article}"))
        confirm = input(colorize("Are you sure? Type 'yes' to continue: ", fg="yellow"))
        if confirm.lower() != "yes":
            self.stdout.write(self.style.NOTICE("Operation cancelled."))
            return

        try:
            rollback_accepted(article)
        except ValueError as e:
            raise CommandError(e) from e

        self.stdout.write(self.style.SUCCESS(f"Acceptance rolled back for article {article}"))
