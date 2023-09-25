from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from django_fsm import GET_STATE, FSMField, transition
from model_utils.models import TimeStampedModel

from . import permissions

Account = get_user_model()


def process_submission(workflow, **kwargs) -> "ArticleWorkflow.ReviewStates":
    """
    Verify and assign a submitted article to an editor.
    """
    from .events.handlers import dispatch_checks

    article = workflow.article
    success = dispatch_checks(article)
    if success is True:
        return workflow.ReviewStates.EDITOR_SELECTED
    elif success is False:
        return workflow.ReviewStates.EDITOR_TO_BE_SELECTED
    else:
        return workflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES


class ArticleWorkflow(TimeStampedModel):
    class ReviewStates(models.TextChoices):
        EDITOR_TO_BE_SELECTED = "EditorToBeSelected", _("Editor to be selected")
        EDITOR_SELECTED = "EditorSelected", _("Editor selected")
        SUBMITTED = "Submitted", _("Submitted")
        TO_BE_REVISED = "ToBeRevised", _("To be revised")
        WITHDRAWN = "Withdrawn", _("Withdrawn")
        REJECTED = "Rejected", _("Rejected")
        INCOMPLETE_SUBMISSION = "IncompleteSubmission", _("Incomplete submission")
        NOT_SUITABLE = "NotSuitable", _("Not suitable")
        PAPER_HAS_EDITOR_REPORT = "PaperHasEditorReport", _("Paper has editor report")
        ACCEPTED = "Accepted", _("Accepted")
        WRITEME_PRODUCTION = "WritemeProduction", _("Writeme production")
        PAPER_MIGHT_HAVE_ISSUES = "PaperMightHaveIssues", _("Paper might have issues")

    class Decisions(models.TextChoices):
        """Decisions that can be made by the editor."""

        ACCEPT = "accept", _("Accept")
        REJECT = "reject", _("Reject")
        NOT_SUITABLE = "not_suitable", _("Not suitable")

    article = models.OneToOneField("submission.Article", verbose_name=_("Article"), on_delete=models.CASCADE)
    # author start submission of paper
    state = FSMField(default=ReviewStates.INCOMPLETE_SUBMISSION, choices=ReviewStates.choices, verbose_name=_("State"))

    class Meta:
        verbose_name = _("Article workflow")
        verbose_name_plural = _("Article workflows")

    @property
    def article_authors(self) -> QuerySet[Account]:
        authors = self.article.authors.all()
        if self.article.correspondence_author:
            authors |= Account.objects.filter(pk=self.article.correspondence_author.pk)
        return authors

    def __str__(self):
        return f"{self.article.id}-{self.state}"

    # director selects editor
    @transition(
        field=state,
        source=ReviewStates.EDITOR_TO_BE_SELECTED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_editor,
        # TODO: conditions=[],
    )
    def director_selects_editor(self):
        pass

    # ed declines assignment
    @transition(
        field=state,
        source=ReviewStates.EDITOR_SELECTED,
        target=ReviewStates.EDITOR_TO_BE_SELECTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def ed_declines_assignment(self):
        pass

    # author submits paper
    @transition(
        field=state,
        source=ReviewStates.INCOMPLETE_SUBMISSION,
        target=ReviewStates.SUBMITTED,
        permission=permissions.is_author,
        # TODO: conditions=[],
    )
    def author_submits_paper(self):
        pass

    # system verifies forgery
    # system detects issues in paper
    # system selects editor - success
    # system selects editor - fail
    # and assigns editor
    @transition(
        field=state,
        source=ReviewStates.SUBMITTED,
        target=GET_STATE(
            process_submission,
            states=[
                ReviewStates.EDITOR_SELECTED,
                ReviewStates.EDITOR_TO_BE_SELECTED,
                ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ],
        ),
        permission=permissions.is_system,
        # TODO: conditions=[],
    )
    def system_process_submission(self):
        pass

    # admin deems issues not important
    # TODO: in the diagram, the automatic selection of the editor is triggered atuomatically
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.EDITOR_TO_BE_SELECTED,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_deems_issues_not_important(self):
        pass

    # editor rejects paper
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.REJECTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_rejects_paper(self):
        pass

    # editor deems paper not suitable
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.NOT_SUITABLE,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_deems_paper_not_suitable(self):
        pass

    # editor requires a revision
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.TO_BE_REVISED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_requires_a_revision(self):
        pass

    # editor accepts paper
    @transition(
        field=state,
        source=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        target=ReviewStates.ACCEPTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_accepts_paper(self):
        pass

    # editor writes editor report
    @transition(
        field=state,
        source=ReviewStates.EDITOR_SELECTED,
        target=ReviewStates.PAPER_HAS_EDITOR_REPORT,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_writes_editor_report(self):
        pass

    # admin opens an appeal
    @transition(
        field=state,
        source=ReviewStates.REJECTED,
        target=ReviewStates.TO_BE_REVISED,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_opens_an_appeal(self):
        pass

    # author submits again
    @transition(
        field=state,
        source=ReviewStates.TO_BE_REVISED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_author,
        # TODO: conditions=[],
    )
    def author_submits_again(self):
        pass

    # admin deems paper not suitable
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.NOT_SUITABLE,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_deems_paper_not_suitable(self):
        pass

    # admin or system requires revision
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.INCOMPLETE_SUBMISSION,
        permission=permissions.is_admin,
        # TODO: conditions=[],
    )
    def admin_or_system_requires_revision(self):
        pass

    # editor assign different editor
    @transition(
        field=state,
        source=ReviewStates.EDITOR_SELECTED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_article_editor,
        # TODO: conditions=[],
    )
    def editor_assign_different_editor(self):
        pass


class EditorDecision(TimeStampedModel):
    workflow = models.ForeignKey(
        ArticleWorkflow,
        verbose_name=_("Article workflow"),
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    review_round = models.ForeignKey("review.ReviewRound", verbose_name=_("Review round"), on_delete=models.PROTECT)
    decision = models.CharField(max_length=255, choices=ArticleWorkflow.Decisions.choices)
    decision_editor_report = models.TextField(blank=True, null=True)
    decision_internal_note = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = _("Editor decision")
        verbose_name_plural = _("Editor decisions")
        unique_together = ("workflow", "review_round")

    def __str__(self):
        return f"{self.decision} (Article {self.workflow.article.id}-{self.review_round.round_number})"
