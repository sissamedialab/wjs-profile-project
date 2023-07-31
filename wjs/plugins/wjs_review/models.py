from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from django_fsm import FSMField, transition
from model_utils.models import TimeStampedModel

from . import permissions

Account = get_user_model()


class ArticleWorkflow(TimeStampedModel):
    class ReviewStates(models.TextChoices):
        TO_BE_ASSIGNED = "TBA", _("To be assigned")
        ASSIGNED = "ASS", _("Assigned")
        BEING_REFEREED = "BREF", _("Being refereed")
        WAIT_DECISION = "WAIT", _("Waiting for editors decision")
        EDITOR_REF = "EDREF", _("Editor as referee")
        TBR_MAJOR = "TBR_MAJOR", _("To be revised (major revision)")
        TBR_MINOR = "TBR_MINOR", _("To be revised (minor revision)")
        NOT_SUIT = "NOT_SUIT", _("Not suitable")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")
        REJECTED = "REJECTED", _("Rejected")
        WAITAPP = "WAITAPP", _("Wait app")
        WAITAPP_NEW = "WAITAPP_NEW", _("Wait app new ed app")
        ACCEPTED = "ACCEPTED", _("Accepted")
        COPY_WAIT = "COPY_WAIT", _("Waiting for copyright")
        COPY_REFUSED = "COPY_REFUSED", _("Copyright refused")

    article = models.OneToOneField("submission.Article", verbose_name=_("Article"), on_delete=models.CASCADE)
    state = FSMField(default=ReviewStates.TO_BE_ASSIGNED, choices=ReviewStates.choices, verbose_name=_("State"))

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
        return self.article.title

    # 4. admin assigns new editor
    @transition(
        field=state,
        source=ReviewStates.TO_BE_ASSIGNED,
        target=ReviewStates.ASSIGNED,
        permission=permissions.is_section_editor,
    )
    def assign(self):
        pass

    # 11. ed selects new editor
    @transition(
        field=state,
        source=ReviewStates.ASSIGNED,
        target=ReviewStates.ASSIGNED,
        permission=permissions.is_section_editor,
    )
    def reassign(self):
        pass

    # 3. ed declines assignment
    # 34. admin changes editor
    @transition(
        field=state,
        source=ReviewStates.ASSIGNED,
        target=ReviewStates.TO_BE_ASSIGNED,
        permission=permissions.is_section_editor,
    )
    def deassign(self):
        pass

    # 1. ed assigns referee
    # 54. ed confirms ref(s) of previous versions
    @transition(
        field=state,
        source=ReviewStates.ASSIGNED,
        target=ReviewStates.BEING_REFEREED,
        permission=permissions.is_article_editor,
    )
    def assign_referee(self):
        pass

    # 17. [#ref=1] ed removes ref
    # 12. [#ref=1&ref_acc] ref refuses
    @transition(
        field=state,
        source=ReviewStates.BEING_REFEREED,
        target=ReviewStates.ASSIGNED,
        permission=permissions.is_section_editor_or_reviewer,
    )
    def deassign_referee(self):
        pass

    # 13. [#ref>1&ref_acc] ref refuses
    # 14. [ref_acc] ref accepts
    # 15. ed adds referee
    # 18. [#ref>1] ed removes ref
    # 54. ed confirms ref(s) of previous versions
    @transition(
        field=state,
        source=ReviewStates.BEING_REFEREED,
        target=ReviewStates.BEING_REFEREED,
        permission=permissions.is_section_editor_or_reviewer,
    )
    def reassign_referee(self):
        pass

    # 2. ref sends report
    @transition(
        field=state,
        source=ReviewStates.BEING_REFEREED,
        target=ReviewStates.WAIT_DECISION,
        permission=permissions.is_reviewer,
    )
    def referee_review(self):
        pass

    # 2. ref sends report
    # 14. [ref_acc] ref accepts
    # 15. ed adds referee
    # 16. [ref_acc] ref refuses
    # 19. ed removes referee
    # 54. ed confirms ref(s) of previous versions
    @transition(
        field=state,
        source=ReviewStates.WAIT_DECISION,
        target=ReviewStates.WAIT_DECISION,
        permission=permissions.is_section_editor_or_reviewer,
    )
    def wait_decision(self):
        pass

    # 21. ed acts as referee
    @transition(
        field=state,
        source=ReviewStates.ASSIGNED,
        target=ReviewStates.EDITOR_REF,
        permission=permissions.is_section_editor,
    )
    def self_referee(self):
        pass

    # 22. ed needs referee
    @transition(
        field=state,
        source=ReviewStates.EDITOR_REF,
        target=ReviewStates.ASSIGNED,
        permission=permissions.is_section_editor,
    )
    def unself_referee(self):
        pass

    # 5. ed requires revision
    @transition(
        field=state,
        source=ReviewStates.WAIT_DECISION,
        target=ReviewStates.TBR_MAJOR,
        permission=permissions.is_section_editor,
    )
    def ask_major(self):
        pass

    # 8. eds requires minor revision
    @transition(
        field=state,
        source=ReviewStates.WAIT_DECISION,
        target=ReviewStates.TBR_MINOR,
        permission=permissions.is_section_editor,
    )
    def ask_minor(self):
        pass

    # 24. ed considers not suitable
    # 59. admin considers not suitable
    @transition(
        field=state,
        source=ReviewStates.WAIT_DECISION,
        target=ReviewStates.NOT_SUIT,
        permission=permissions.is_section_editor,
    )
    def not_suitable(self):
        pass

    # 7. ed accepts document
    @transition(
        field=state,
        source=ReviewStates.WAIT_DECISION,
        target=ReviewStates.ACCEPTED,
        permission=permissions.is_section_editor,
    )
    def accept(self):
        pass

    # 6. ed rejects document
    @transition(
        field=state,
        source=ReviewStates.WAIT_DECISION,
        target=ReviewStates.REJECTED,
        permission=permissions.is_section_editor,
    )
    def reject(self):
        pass

    # 34. admin changes editor
    @transition(
        field=state,
        source=ReviewStates.WAIT_DECISION,
        target=ReviewStates.TO_BE_ASSIGNED,
        permission=permissions.is_editor,
    )
    def reassign_after_wait(self):
        pass

    # 9. aut submits revised version
    @transition(
        field=state,
        source=ReviewStates.TBR_MAJOR,
        target=ReviewStates.ASSIGNED,
        permission=permissions.is_author,
    )
    def resubmit_major(self):
        pass

    # 9. aut submits revised version
    @transition(
        field=state,
        source=ReviewStates.TBR_MINOR,
        target=ReviewStates.ASSIGNED,
        permission=permissions.is_author,
    )
    def resubmit_minor(self):
        pass
