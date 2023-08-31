from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from django_fsm import GET_STATE, FSMField, transition
from model_utils.models import TimeStampedModel

from . import permissions

Account = get_user_model()


def process_submission(model):
    """Verify and assign a submitted article to an editor."""
    # TODO: add type hints models.ArticleWorkflow.ReviewState.???; check circular import
    return model.ReviewStates.EDITOR_TO_BE_SELECTED


class ArticleWorkflow(TimeStampedModel):
    class ReviewStates(models.TextChoices):
        EDITOR_TO_BE_SELECTED = "ED_TO_BE_SE", _("Editor to be selected")
        EDITOR_SELECTED = "EDITO_SELEC", _("Editor selected")
        SUBMITTED = "_SUBMITTED_", _("Submitted")
        TO_BE_REVISED = "_TO_BE_REV_", _("To be revised")
        WITHDRAWN = "_WITHDRAWN_", _("Withdrawn")
        REJECTED = "_REJECTED__", _("Rejected")
        INCOMPLETE_SUBMISSION = "INCOM_SUBMI", _("Incomplete submission")
        NOT_SUITABLE = "_NOT_SUITA_", _("Not suitable")
        PAPER_HAS_EDITOR_REPORT = "PA_HA_ED_RE", _("Paper has editor report")
        ACCEPTED = "_ACCEPTED__", _("Accepted")
        WRITEME_PRODUCTION = "WRITE_PRODU", _("Writeme production")
        PAPER_MIGHT_HAVE_ISSUES = "PA_MI_HA_IS", _("Paper might have issues")

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
