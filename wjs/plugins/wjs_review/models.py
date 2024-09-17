"""WJS Review and related models."""

import dataclasses
import datetime
from typing import TYPE_CHECKING, Optional, Union

import html2text
from core import models as core_models
from core.model_utils import JanewayBleachCharField, MiniHTMLFormField
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db import models
from django.db.models import BLANK_CHOICE_DASH, QuerySet
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django_fsm import GET_STATE, FSMField, transition
from identifiers.models import Identifier
from journal.models import Journal
from model_utils.models import TimeStampedModel
from plugins.typesetting.models import TypesettingAssignment, TypesettingRound
from review.const import EditorialDecisions
from review.models import (
    EditorAssignment,
    ReviewAssignment,
    ReviewRound,
    RevisionRequest,
)
from submission.models import Article, Section
from tinymce.widgets import TinyMCE
from utils import setting_handler
from utils.logger import get_logger

from wjs.jcom_profile.constants import EO_GROUP
from wjs.jcom_profile.models import Correspondence
from wjs.jcom_profile.utils import render_template

from . import permissions
from .managers import (
    ArticleWorkflowQuerySet,
    WjsEditorAssignmentQuerySet,
    WorkflowReviewAssignmentQuerySet,
)

if TYPE_CHECKING:
    from .custom_types import (
        ReviewAssignmentActionConfiguration,
        ReviewAssignmentAttentionCondition,
        ReviewAssignmentStatus,
    )

logger = get_logger(__name__)

Account = get_user_model()

# The first piece of the DOIs or our journal's papers identifies the journal.
# The first of our self-published systems to acquire a DOI was PoS, so it gets "1"
MEDIALAB_DOI_JOURNAL_NUMBER = {
    "PoS": "1",
    "JCOM": "2",
    "JCOMAL": "3",
}


class WjsMiniHTMLFormField(MiniHTMLFormField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bleach_options["tags"] = ["span", "em", "i", "b", "strong", "sup", "sub", "u", "br", "a"]
        self.bleach_options["attributes"] = {"a": ["href", "title", "target"]}
        if isinstance(self.widget, TinyMCE):
            self.widget.mce_attrs.update(
                {
                    "plugins": "link lists charmap",
                    "menubar": "",
                    "forced_root_block": "div",
                    "toolbar": "bold italic link numlist charmap",
                    "height": "30rem",
                    "resize": True,
                    "elementpath": False,
                }
            )


class WjsBleachCharField(JanewayBleachCharField):
    """
    An override of JanewayBleachCharField to use our custom form field.
    """

    def formfield(self, *args, **kwargs):
        defaults = {"form_class": WjsMiniHTMLFormField}
        defaults.update(kwargs)
        return super().formfield(*args, **defaults)


def can_be_set_rfp_wrapper(workflow: "ArticleWorkflow", **kwargs) -> bool:
    """Only wraps the method that tests if a article can transition to READY_FOR_PUBLICATION."""
    return workflow.can_be_set_rfp()


def create_director_reminders(workflow):
    """Create reminders for the director."""
    from .reminders.settings import DirectorShouldAssignEditorReminderManager

    DirectorShouldAssignEditorReminderManager(
        article=workflow.article,
    ).create()


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
        create_director_reminders(workflow)
        return workflow.ReviewStates.EDITOR_TO_BE_SELECTED
    else:
        return workflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES


@dataclasses.dataclass
class Version:
    """
    Collects all the elements related to a version of an article.

    Mostly a convenience class to easily access all the elements related to a version of an article and to clarify
    all the involved elements.
    """

    label = _("Version")

    review_round: ReviewRound
    """
    review_round is the defining attribute of a version.
    """
    decisions: list["EditorDecision"]
    """
    A version can contain multiple decisions, because we can have multiple technical review for each round, plus the
    decision at the end of the round.
    """
    revision_requests: list["EditorRevisionRequest"]
    """
    A version can contain multiple revision requests, because we can have multiple technical review for each round,
    plus the decision at the end of the round.
    """
    editor_assignment: "WjsEditorAssignment"
    """
    Each version has an editor assignment, which is the editor in charge of the round.

    In case of editor changes, we can retrieve past assignments by using :py:attr:`review_round` attribute, as it's not
    likely, we dont' have to store them in the version from the beginning.
    """
    review_assignments: list["WorkflowReviewAssignment"]
    """
    All review assignment for the round.

    While they can be retrieved from the round, it's useful to have them here for quick access.
    """
    latest: bool = False
    """
    Flag to mark the latest version of the article.
    """

    @property
    def round(self) -> ReviewRound:  # noqa: A003
        return self.review_round

    @property
    def started_on(self) -> Optional[timezone.datetime]:
        return self.review_round.date_started

    @property
    def number(self) -> int:
        return self.review_round.round_number

    def assignments_by_stage(self) -> dict["ReviewAssignmentStatus", list["WorkflowReviewAssignment"]]:
        """Group the review assignment of the version by status."""

        from .custom_types import ReviewAssignmentStatus

        stack = {ReviewAssignmentStatus(code=status): [] for status in WorkflowReviewAssignment.statuses.values()}
        for assignment in self.review_assignments:
            stack[assignment.status].append(assignment)
        return stack

    @property
    def main_revision(self) -> Optional["EditorRevisionRequest"]:
        """
        Return the main revision request for the version.

        As the list of revision requests is ordered by reveres date, picks the latest one if it's a major or minor
        revision request, if not return None. As Major and Minor revision requests are the "final" ones, there cannot
        be technical reviews after one of those.
        """
        if self.revision_requests:
            latest = self.revision_requests[0]
            if latest.type in (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.Decisions.MINOR_REVISION):
                return latest
        return None


@dataclasses.dataclass
class TypesettingVersion:
    """
    Collects all the elements related to a typesetting version of an article.

    Mostly a convenience class to easily access all the elements related to a version of an article and to clarify
    all the involved elements.
    """

    label = _("Production version")

    typesetting_round: TypesettingRound
    """
    typesetting_round is the defining attribute of a version.
    """
    assignment: TypesettingAssignment
    """
    Assignment linked to the version
    """
    latest: bool = False
    """
    Flag to mark the latest version of the article.
    """

    @property
    def round(self) -> TypesettingRound:  # noqa: A003
        return self.typesetting_round

    @property
    def started_on(self) -> Optional[timezone.datetime]:
        return self.typesetting_round.date_created

    @property
    def number(self) -> int:
        return self.typesetting_round.round_number

    @property
    def galleyproofing(self):
        return self.typesetting_round.galleyproofing_set.first()

    @property
    def has_proofing_files(self) -> bool:
        if self.galleyproofing:
            return self.galleyproofing.proofed_files.exists() or self.galleyproofing.annotated_files.exists()
        return False

    @property
    def has_typesetter_files(self) -> bool:
        return self.assignment.files_to_typeset.exists()


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
        TYPESETTER_SELECTED = "TypesetterSelected", _("Typesetter selected")
        PAPER_MIGHT_HAVE_ISSUES = "PaperMightHaveIssues", _("Paper might have issues")
        PROOFREADING = "Proofreading", _("Proofreading")
        READY_FOR_TYPESETTER = "ReadyForTypesetter", _("Ready for typesetter")
        PUBLISHED = "Published", _("Published")
        READY_FOR_PUBLICATION = "ReadyForPublication", _("Ready for publication")
        SEND_TO_EDITOR_FOR_CHECK = "SendToEditorForCheck", _("Send to editor for check")
        PUBLICATION_IN_PROGRESS = "PublicationInProgress", _("Publication in progress")
        UNDER_APPEAL = "UnderAppeal", _("Under appeal")

    class Decisions(models.TextChoices):
        """Decisions that can be made by the editor."""

        __empty__ = BLANK_CHOICE_DASH[0][0]

        ACCEPT = "accept", _("Accept")
        REJECT = "reject", _("Reject")
        MINOR_REVISION = EditorialDecisions.MINOR_REVISIONS.value, _("Minor revision")
        MAJOR_REVISION = EditorialDecisions.MAJOR_REVISIONS.value, _("Major revision")
        TECHNICAL_REVISION = EditorialDecisions.TECHNICAL_REVISIONS.value, _("Change Metadata")
        NOT_SUITABLE = "not_suitable", _("Not suitable")
        REQUIRES_RESUBMISSION = "requires_resubmission", _("Requires resubmission")
        OPEN_APPEAL = EditorialDecisions.OPEN_APPEAL.value, _("Open appeal")

        @classmethod
        @property
        def decision_choices(cls):
            return [
                choice
                for choice in cls.choices
                if choice[0]
                not in [cls.REQUIRES_RESUBMISSION.value, cls.TECHNICAL_REVISION.value, cls.OPEN_APPEAL.value]
            ]

    class GalleysStatus(models.IntegerChoices):
        NOT_TESTED = 1, _("Galleys not tested")
        TEST_FAILED = 2, _("Galleys generation failed")
        TEST_SUCCEEDED = 3, _("Galleys generation succeeded")

    class ReviewComputedStates(models.TextChoices):
        REQUESTED_MINOR_REVISION = "requested_minor_revisions", _("Requested Minor revision")
        REQUESTED_MAJOR_REVISION = "requested_major_revisions", _("Requested Major revision")
        REQUESTED_OPEN_APPEAL = "requested_open_appeal", _("Requested Appeal")
        REQUESTED_TECHNICAL_REVISION = "requested_tech_revisions", _("Requested Metadata change")
        REQUESTED_REQUIRES_RESUBMISSION = "requested_requires_resubmission", _("Requested resubmission")
        WAITING_FOR_DECISION = "waiting_for_decision", _("Waiting for decision")
        IN_REVIEW = "in_review", _("In review")
        RESUBMITTED_MINOR_REVISION = "resubmitted_minor_revisions", _("Resubmitted Minor revision")
        RESUBMITTED_MAJOR_REVISION = "resubmitted_major_revisions", _("Resubmitted Major revision")
        RESUBMITTED_OPEN_APPEAL = "resubmitted_open_appeal", _("Resubmitted Appeal")
        RESUBMITTED_TECHNICAL_REVISION = "resubmitted_tech_revisions", _("Resubmitted Metadata change")
        RESUBMITTED_REQUIRES_RESUBMITTED = "resubmitted_requires_resubmission", _("Resubmitted resubmitted")
        ASSIGNED_TO_EDITOR = "assigned_to_editor", _("Assigned to Editor")

    article = models.OneToOneField("submission.Article", verbose_name=_("Article"), on_delete=models.CASCADE)
    # author start submission of paper
    state = FSMField(default=ReviewStates.INCOMPLETE_SUBMISSION, choices=ReviewStates.choices, verbose_name=_("State"))
    eo_in_charge = models.ForeignKey(
        Account,
        verbose_name=_("EO in charge"),
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        limit_choices_to={"groups__name": EO_GROUP},
    )
    # Here we store ESM files when then first typesetting assignment is created
    # This allows us to keep an history of the ESM between acceptance and production
    supplementary_files_at_acceptance = models.ManyToManyField(
        "core.SupplementaryFile",
        null=True,
        blank=True,
        related_name="+",
    )
    # production flags
    production_flag_no_queries = models.BooleanField(
        default=False, verbose_name=_("The latest typesetted files contain no queries for the author")
    )
    production_flag_galleys_ok = models.IntegerField(
        choices=GalleysStatus.choices,
        default=GalleysStatus.NOT_TESTED,
        null=False,
        blank=True,
        verbose_name=_("The status of the latest galleys"),
    )
    production_flag_no_checks_needed = models.BooleanField(
        default=True, verbose_name=_("No special check is required on the latest typesetted files")
    )

    latest_state_change = models.DateTimeField(default=timezone.now, null=True, blank=True)
    latex_desc = models.TextField(null=True, blank=True)

    social_media_short_description = models.TextField(_("Short description for social media"), null=True, blank=True)

    objects = ArticleWorkflowQuerySet.as_manager()

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
        return self.preprint_id

    @property
    def preprint_id(self):
        return f"{self.article.journal.code}_{self.article_id}"

    @property
    def permission_label(self) -> str:
        return _("Author notes")

    @property
    def url(self):
        """Return the full URL, including domain and scheme."""
        return self.article.journal.site_url(path=self.get_absolute_url())

    def get_absolute_url(self):
        """Return canonical url of the object as per Django convention."""
        return reverse("wjs_article_details", args=[self.pk])

    @cached_property
    def state_value(self) -> Union["ReviewStates", "ReviewComputedStates"]:
        """
        Return the code for the article state.

        The value can be either one of :py:attr:`ReviewComputedStates` or :py:attr:`ReviewStates` depending
         on the states or other factors like the presence of accepted review requests, active reviews, etc.

        :return: The verbose state label of the article.
        :rtype: Union["ReviewStates", "ReviewComputedStates"
        """
        from .logic import (
            states_when_article_is_considered_archived,
            states_when_article_is_considered_in_production,
        )

        article = self.article
        if (
            self.state in states_when_article_is_considered_in_production
            or self.state in states_when_article_is_considered_archived
        ):
            return self.state

        waiting_for_revision = article.active_revision_requests().filter(
            editorrevisionrequest__review_round=article.current_review_round_object(),
        )
        completed_revision = article.completed_revision_requests().filter(
            editorrevisionrequest__review_round__round_number=article.current_review_round() - 1,
        )
        submitted_reviews = article.completed_reviews.exclude(decision="withdrawn").filter(
            review_round=article.current_review_round_object(),
        )
        outstanding_reviews = article.active_reviews.filter(
            review_round=article.current_review_round_object(),
        )

        # The order of the following checks is important because the first one that matches will be returned.
        if waiting_for_revision.exists():
            revision = waiting_for_revision.first()
            return self.ReviewComputedStates("requested_" + revision.type).value

        # No pending reviews with at least one submitted review -> Editor must make a decision
        elif not outstanding_reviews.exists() and submitted_reviews.exists():
            return self.ReviewComputedStates.WAITING_FOR_DECISION.value

        # At least one submitted review and one pending reviews (implicit because of the previous clause) ->
        # Article is in review
        elif submitted_reviews.exists():
            return self.ReviewComputedStates.IN_REVIEW.value

        # At least one pending review (and none submitted) -> Article is in review
        elif outstanding_reviews.exists():
            return self.ReviewComputedStates.IN_REVIEW.value

        elif completed_revision.exists():
            revision = completed_revision.first()
            return self.ReviewComputedStates("resubmitted_" + revision.type).value

        else:
            return self.ReviewComputedStates.ASSIGNED_TO_EDITOR.value

    @cached_property
    def state_label(self) -> str:
        """
        Return the verbose state label of the article.

        Label is calculated based on the value of :py:attr:`state_value`.

        :return: label of the state
        :rtype: str
        """
        value = self.state_value
        if value in self.ReviewComputedStates.values:
            return self.ReviewComputedStates(value).label
        if value in self.ReviewStates.values:
            return self.ReviewStates(value).label
        return value

    def pending_revision_request(self):
        try:
            return EditorRevisionRequest.objects.get(
                article=self.article,
                date_completed__isnull=True,
            )
        except EditorRevisionRequest.DoesNotExist:
            return None

    def latest_typesetting_assignment(self):
        """Return the last (or "current") TA.

        During production, the last TA contains references to the latest sources and galleys.
        """
        try:
            return (
                TypesettingAssignment.objects.filter(
                    round__article_id=self.article.id,
                    completed__isnull=True,
                )
                .order_by("-id")
                .first()
            )
        # TODO: ensure that there is always at most one TA with completed == NULL
        except TypesettingAssignment.DoesNotExist:
            return None

    def can_be_set_rfp(self) -> bool:
        """Test if the article can transition to READY_FOR_PUBLICATION."""
        return (
            self.production_flag_galleys_ok == ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
            and self.production_flag_no_checks_needed
            and self.production_flag_no_queries
        )

    def compute_pubid(self, save_eid: bool = False) -> str:
        """Compute and return the pubid that the Article would get now.

        Pass along `save_eid` to compute_eid, so that the computed eid is stored as page number.
        This is useful during publication, but let the computation be free of side-effects otherwise.
        """
        # This function would probably be better placed in the Journal model,
        # but since we don't yet have a o2o/wrapper on that model I'm leaving it here.
        if self.article.journal.code != "JCOM":
            raise NotImplementedError(f"Don't know how to compute pubid for {self.article.journal.code}")

        # Feel free to fail badly.
        # Exceptions should be dealt with upstream.
        volume = f"{self.article.issue.volume:02d}"
        issue = f"{int(self.article.issue.issue):02d}"
        eid = self.compute_eid(save_as_pagenumber=save_eid)
        pubid = f"{self.article.journal.code}_{volume}{issue}_{timezone.now().year}_{eid}"
        return pubid

    def compute_doi(self) -> str:
        # Same considerations about where to place the function as compute_pubid() above.
        #
        # Please also note that Janeway has its way of generating DOIs,
        # i.e. by rendering the journal setting "doi_pattern"
        article = self.article
        if article.journal.code != "JCOM":
            raise NotImplementedError(f"Don't know how to compute DOI for {article.journal.code}")

        # See specs#208 for specs on JCOM DOI
        # Adapting utils.generate_doi()
        # Feel free to fail badly.
        # Exceptions should be dealt with upstream.
        doi_prefix = setting_handler.get_setting("Identifiers", "crossref_prefix", article.journal).value
        system_number = MEDIALAB_DOI_JOURNAL_NUMBER[article.journal.code]
        volume = f"{article.issue.volume:02d}"
        issue = f"{int(article.issue.issue):02d}"
        # TODO: refactor eid into cached property?
        counter = self._count_published_papers_in_same_issue_and_section() + 1
        counter = f"{counter:02d}"
        type_code = article.section.wjssection.doi_sectioncode
        if not type_code:
            logger.error(
                f'Section "{article.section}" is missing DOI code. DOI will be wrong!'
                "Please correct from the admin interface.",
            )
        doi = f"{doi_prefix}/{system_number}.{volume}{issue}{type_code}{counter}"
        return doi

    def compute_eid(self, save_as_pagenumber: bool = False) -> str:
        """Return the Electronic IDentifier as intended by biblatex, which is similar to the concept of page number.

        Eid has the form "A01", "C03", ... and it includes info about the paper section and the number of papers
        published in the same section/issue.

        If page_numbers has already been set (manually or otherwise), then just use it (see submission.Article).
        """
        if self.article.page_numbers:
            return self.article.page_numbers
        counter = self._count_published_papers_in_same_issue_and_section() + 1
        type_code = self.article.section.wjssection.pubid_and_tex_sectioncode
        if not type_code:
            logger.error(
                f'Section "{self.article.section}" is missing PUBID code. PUBID will be wrong!'
                "Please correct from the admin interface.",
            )
        # Editorials are special:
        # there always is at most one, so they are not E01 E02,
        # but just "E" (without any number)
        if type_code == "E":
            assert counter == 1, f"Impossible number of editorials ({counter}) for issue of article {self.id}"
            eid = type_code
        else:
            eid = f"{type_code}{counter:02d}"

        if save_as_pagenumber:
            self.page_numbers = eid
            self.save()

        return eid

    def _count_published_papers_in_same_issue_and_section(self) -> int:
        """Nomen omen, but reviews are special 😢 (see the code)."""
        if not self.article.primary_issue:
            # Manually raising error to give explanatory message
            raise ValueError(f"Trying to count similar papers but no primary issue set for article {self.article.id}")

        base_qs = Article.objects.filter(
            primary_issue_id=self.article.primary_issue.pk,
            # remember that, during publication, the article has already been given a publication date
            # when we reach this point
            date_published__isnull=False,
            articleworkflow__state__in=[
                ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS,
                ArticleWorkflow.ReviewStates.PUBLISHED,
            ],
        )
        pesky_sections = ("book review", "conference review")
        if self.article.section.name in pesky_sections:
            return base_qs.filter(section__name__in=pesky_sections).count()
        else:
            return base_qs.filter(section_id=self.article.section.pk).count()

    def set_pubid(self):
        return Identifier.objects.create(
            id_type="pubid",
            # pubid depends on eid/page_numbers
            # if we have to compute it now, we also save it to maintain coherence
            identifier=self.compute_pubid(save_eid=True),
            article=self.article,
        )

    def set_doi(self):
        return Identifier.objects.create(
            id_type="doi",
            identifier=self.compute_doi(),
            article=self.article,
        )

    # director selects editor
    @transition(
        field=state,
        source=ReviewStates.EDITOR_TO_BE_SELECTED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.has_editor_role_by_article,
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
        permission=permissions.has_author_role_by_article,
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
        permission=permissions.has_admin_role_by_article,
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
        source=[ReviewStates.TO_BE_REVISED, ReviewStates.EDITOR_SELECTED],
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
        target=ReviewStates.UNDER_APPEAL,
        permission=permissions.has_admin_role_by_article,
        # TODO: conditions=[],
    )
    def admin_opens_an_appeal(self):
        pass

    # author submits again
    @transition(
        field=state,
        source=ReviewStates.TO_BE_REVISED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.has_author_role_by_article,
        # TODO: conditions=[],
    )
    def author_submits_again(self):
        pass

    @transition(
        field=state,
        source=ReviewStates.UNDER_APPEAL,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_article_author,
        # TODO: conditions=[],
    )
    def author_submits_appeal(self):
        pass

    # admin deems paper not suitable
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.NOT_SUITABLE,
        permission=permissions.has_admin_role_by_article,
        # TODO: conditions=[],
    )
    def admin_deems_paper_not_suitable(self):
        pass

    # admin or system requires revision
    @transition(
        field=state,
        source=ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
        target=ReviewStates.INCOMPLETE_SUBMISSION,
        permission=permissions.has_admin_role_by_article,
        # TODO: conditions=[],
    )
    def admin_or_system_requires_revision(self):
        pass

    # editor Select editor
    @transition(
        field=state,
        source=ReviewStates.EDITOR_SELECTED,
        target=ReviewStates.EDITOR_SELECTED,
        permission=permissions.is_article_supervisor,
        # TODO: conditions=[],
    )
    def editor_assign_different_editor(self):
        pass

    # typesetter takes in charge
    @transition(
        field=state,
        source=ReviewStates.READY_FOR_TYPESETTER,
        target=ReviewStates.TYPESETTER_SELECTED,
        permission=permissions.has_typesetter_role_by_article,
        # TODO: conditions=[],
    )
    def typesetter_takes_in_charge(self):
        pass

    # system assigns typesetter
    @transition(
        field=state,
        source=ReviewStates.READY_FOR_TYPESETTER,
        target=ReviewStates.TYPESETTER_SELECTED,
        permission=permissions.is_system,
        # TODO: conditions=[],
    )
    def system_assigns_typesetter(self):
        pass

    # typesetter submits
    @transition(
        field=state,
        source=ReviewStates.TYPESETTER_SELECTED,
        target=ReviewStates.PROOFREADING,
        permission=permissions.has_typesetter_role_by_article,
        # TODO: conditions=[],
    )
    def typesetter_submits(self):
        pass

    # author sends corrections
    @transition(
        field=state,
        source=ReviewStates.PROOFREADING,
        target=ReviewStates.TYPESETTER_SELECTED,
        permission=permissions.has_author_role_by_article,
        # TODO: conditions=[],
    )
    def author_sends_corrections(self):
        pass

    # EO sends back to typ
    @transition(
        field=state,
        source=ReviewStates.READY_FOR_PUBLICATION,
        target=ReviewStates.TYPESETTER_SELECTED,
        permission=permissions.has_eo_role_by_article,
        # TODO: conditions=[],
    )
    def admin_sends_back_to_typ(self):
        pass

    # typesetter deems paper ready for publication
    @transition(
        field=state,
        source=ReviewStates.TYPESETTER_SELECTED,
        target=ReviewStates.READY_FOR_PUBLICATION,
        # TODO: permission=,
        conditions=[can_be_set_rfp_wrapper],
    )
    def typesetter_deems_paper_ready_for_publication(self):
        pass

    # author deems paper ready for publication
    @transition(
        field=state,
        source=ReviewStates.PROOFREADING,
        target=ReviewStates.READY_FOR_PUBLICATION,
        # TODO: permission=,
        conditions=[can_be_set_rfp_wrapper],
    )
    def author_deems_paper_ready_for_publication(self):
        pass

    # typesetter sends to editor for check
    @transition(
        field=state,
        source=ReviewStates.TYPESETTER_SELECTED,
        target=ReviewStates.SEND_TO_EDITOR_FOR_CHECK,
        # TODO: permission=,
        # TODO: conditions=[],
    )
    def typesetter_sends_to_editor_for_check(self):
        pass

    # system verifies production requirements
    @transition(
        field=state,
        source=ReviewStates.ACCEPTED,
        target=ReviewStates.READY_FOR_TYPESETTER,
        # TODO: permission=,
        # TODO: conditions=[],
    )
    def system_verifies_production_requirements(self):
        pass

    @transition(
        field=state,
        source=(
            ReviewStates.EDITOR_TO_BE_SELECTED,
            ReviewStates.EDITOR_SELECTED,
            ReviewStates.SUBMITTED,
            ReviewStates.TO_BE_REVISED,
            ReviewStates.INCOMPLETE_SUBMISSION,
            ReviewStates.PAPER_HAS_EDITOR_REPORT,
            ReviewStates.ACCEPTED,
            ReviewStates.TYPESETTER_SELECTED,
            ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ReviewStates.PROOFREADING,
            ReviewStates.READY_FOR_TYPESETTER,
            ReviewStates.READY_FOR_PUBLICATION,
            ReviewStates.SEND_TO_EDITOR_FOR_CHECK,
            ReviewStates.PUBLICATION_IN_PROGRESS,
        ),
        target=ReviewStates.WITHDRAWN,
        permission=permissions.is_article_author,
        # TODO: conditions=[],
    )
    def author_withdraws_preprint(self):
        pass

    @transition(
        field=state,
        source=(
            ReviewStates.EDITOR_TO_BE_SELECTED,
            ReviewStates.EDITOR_SELECTED,
            ReviewStates.SUBMITTED,
            ReviewStates.TO_BE_REVISED,
            ReviewStates.PAPER_HAS_EDITOR_REPORT,
            ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ReviewStates.UNDER_APPEAL,
        ),
        target=ReviewStates.REJECTED,
        permission=permissions.is_article_author,
        # TODO: conditions=[],
    )
    def author_withdraws_preprint_after_a_rejection(self):
        pass

    # EO initiates publication
    @transition(
        field=state,
        source=ReviewStates.READY_FOR_PUBLICATION,
        target=ReviewStates.PUBLICATION_IN_PROGRESS,
    )
    def begin_publication(self):
        pass

    # system concludes publication
    @transition(
        field=state,
        source=ReviewStates.PUBLICATION_IN_PROGRESS,
        target=ReviewStates.PUBLISHED,
        # TODO: permission=,
        # TODO: conditions=[],
    )
    def finish_publication(self):
        pass

    def rename_manuscript_files(self):
        # TODO: WRITEME!
        pass

    def rename_source_files(self):
        # TODO: WRITEME!
        pass

    def get_review_versions(self, user: Account) -> list[Version]:
        """
        Generates the list of version for the current article.

        Versions are checked against the user's permissions to ensure that only the versions the user has rights on are
        returned.

        :param user: The user for which the versions are generated.
        :type user: Account
        :return: The list of versions the user has rights on.
        :rtype: list[Version]
        """
        from .logic__visibility import PermissionChecker

        versions = []
        for index, review_round in enumerate(self.article.reviewround_set.all().order_by("-round_number")):
            has_permission = PermissionChecker()(
                self,
                user,
                self,
                review_round=review_round.round_number,
                permission_type=PermissionAssignment.PermissionType.NO_NAMES,
            )
            if has_permission:
                version = Version(
                    review_round=review_round,
                    latest=index == 0,
                    decisions=list(EditorDecision.objects.filter(workflow=self, review_round=review_round)),
                    revision_requests=list(
                        EditorRevisionRequest.objects.filter(article=self.article, review_round=review_round)
                    ),
                    editor_assignment=WjsEditorAssignment.objects.filter(
                        article=self.article, review_rounds=review_round
                    ).first(),
                    review_assignments=list(
                        WorkflowReviewAssignment.objects.filter(article=self.article, review_round=review_round)
                    ),
                )
                versions.append(version)
        return versions

    def get_production_versions(self, user: Account) -> list[TypesettingVersion]:
        """
        Generates the list of typesetting version for the current article.

        Versions are checked against the user's roles to ensure that only the versions the user has rights on are
        returned.

        :param user: The user for which the versions are generated.
        :type user: Account
        :return: The list of versions the user has rights on.
        :rtype: list[TypesettingVersion]
        """
        rounds = []
        versions = []
        if permissions.is_article_author(self.article.articleworkflow, user):
            rounds = self.article.typesettinground_set.all().order_by("-round_number")
        elif permissions.is_article_supervisor(self.article.articleworkflow, user):
            rounds = self.article.typesettinground_set.all().order_by("-round_number")
        elif permissions.is_article_typesetter(self.article.articleworkflow, user):
            rounds = self.article.typesettinground_set.filter(typesettingassignment__typesetter=user).order_by(
                "-round_number"
            )
        for index, typesetting_round in enumerate(rounds):
            version = TypesettingVersion(
                typesetting_round=typesetting_round,
                latest=index == 0,
                assignment=typesetting_round.typesettingassignment,
            )
            versions.append(version)
        return versions


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

    class Meta:
        verbose_name = _("Editor decision")
        verbose_name_plural = _("Editor decisions")
        unique_together = ("workflow", "review_round", "decision")

    def __str__(self):
        return f"{self.decision} (Article {self.workflow.article.id}-{self.review_round.round_number})"

    def get_revision_request(self):
        return EditorRevisionRequest.objects.get(
            article=self.workflow.article,
            review_round=self.review_round,
        )


class Message(TimeStampedModel):
    """A generic message.

    Could be:
    - a workflow action (paper submitted, revision requested,...)
    - a communication (editor assigns paper, author inquires,...)
    - a note (an EO note, an editor note,...)

    This is very similar to utils.LogEntry, but a list of recipients of the message is added, so that messages can be
    filtered by recipient.

    """

    SPLIT_MARKER = "[...]"

    class MessageTypes(models.TextChoices):
        SYSTEM = "System log message", _("A system message")
        HIJACK = "User hijacked action log message", _("A hijacking notification message")
        NOTE = "User note", _("Notes to self")
        USER = "User message", _("User direct message")

    class MessageVerbosity(models.TextChoices):
        # generic system actions (STD & SILENT)
        FULL = "Full", _("Full message content is sent by email")
        TIMELINE = "Timeline", _("Timeline only, no email sent")
        EMAIL = "Email", _("Email only, not recorded in timeline")
        REDUCED = "Reduced", _("Reduced message sent my email")

    actor = models.ForeignKey(
        Account,
        verbose_name=_("from"),
        on_delete=models.DO_NOTHING,
        related_name="authored_messages",
        help_text="The author of the message (for system message, use wjs-support account)",
        null=False,
    )
    hijacking_actor = models.ForeignKey(
        Account,
        verbose_name=_("hijacker"),
        on_delete=models.DO_NOTHING,
        related_name="authored_messages_as_hijacker",
        help_text="The real author of the message (if actor has been hijacked)",
        null=True,
        blank=True,
    )
    recipients = models.ManyToManyField(
        verbose_name=_("recipients"),
        to=Account,
        through="MessageRecipients",
        related_name="received_messages",
    )
    to_be_forwarded_to = models.ForeignKey(
        Account,
        verbose_name=_("final recipient"),
        on_delete=models.DO_NOTHING,
        related_name="pre_moderation_messages",
        help_text="The final recipient that this message was intended for",
        null=True,
        blank=True,
    )
    subject = models.TextField(
        verbose_name=_("subject"),
        blank=True,
        default="",
        max_length=111,
        help_text="A short description of the message or the subject of the email.",
    )
    body = WjsBleachCharField(
        verbose_name=_("body"),
        blank=True,
        default="",
        max_length=1111,
        help_text="The content of the message.",
    )
    message_type = models.TextField(
        verbose_name=_("Type"),
        choices=MessageTypes.choices,
        default=MessageTypes.SYSTEM,
        help_text=_("Define the message source / scope"),
    )
    verbosity = models.TextField(
        verbose_name=_("Verbosity"),
        choices=MessageVerbosity.choices,
        default=MessageVerbosity.FULL,
        help_text=_("Define the message verbosity: ie: the amount of content sent my email / set in the timeline"),
    )
    # Do we want to manage very detailed ACLs?
    # :START:
    # nope   acl = models.TextField(
    # nope       default="111",
    # nope       verbose_name="Access Control List",
    # nope       help_text="1 means visible, 0 means not-visible. The position indicates editor, reviewer, author",
    # nope   )
    #        :OR:
    # nope   visible = models.BooleanField(default=True)
    # nope   by_who = models.ForeignKey(Account, on_delete=models.CASCADE)
    #        :OR:
    # with the "through" model (see below)
    # :END:

    # A message should have a "target", i.e. it should be related either to an Article (e.g. communications between
    # editor and reviewer, EO and editor,...) or to a Journal (e.g. communications between editor and director).
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=False,
    )
    object_id = models.PositiveIntegerField(
        blank=False,
        null=False,
    )
    target = GenericForeignKey(
        "content_type",
        "object_id",
    )

    # A message could be related to other messages
    # (mainly used for forwarded messages - e.g. typ-to-au)
    related_messages = models.ManyToManyField(
        verbose_name=_("Related messages"),
        to="Message",
        through="MessageThread",
        related_name="children_messages",
    )

    # Attachments
    attachments = models.ManyToManyField(
        verbose_name=_("Attachments"),
        to=core_models.File,
        null=True,
        blank=True,
    )
    read_by_eo = models.BooleanField(
        verbose_name=_("Read by EO"),
        default=False,
        help_text=_("True when a member of the EO marks as read a message exchanged by other two actors"),
    )
    # number of chars to show in a "VERBINE" message
    verbine_lenght = 111

    # TODO: do we need these indexes?
    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        # Including recipients here may give max-recursion error if recipients.add is called before self is saved in DB
        # Was `return ... '; '.join([str(x) for x in self.recipients.all()])`
        return f"{self.actor} {self.notification_line}"

    @property
    def date(self) -> datetime.date:
        """Return the date object of the message creation datetime."""
        return self.created.date()

    @property
    def notification_line(self):
        """Return a string suitable to be shown in a notification."""
        return self.subject if self.subject else self.body[: Message.verbine_lenght]

    def get_url(self, recipient: Account) -> str:
        """Return the URL to be embedded in the notification email for the given recipient."""

        if self.content_type.model_class() == Journal:
            return reverse("wjs_my_messages")

        assert self.content_type.model_class() == Article
        # TODO: hmmm... recipient not used at the moment...
        return self.target.url

    def get_absolute_url(self):
        """Return the URL to the message."""
        if self.content_type.model_class() == Article:
            return f'{reverse("wjs_article_messages", args=[self.target.articleworkflow.pk])}#message-{self.pk}'

    @property
    def journal(self) -> Journal:
        if isinstance(self.target, Article):
            return self.target.journal
        return self.target

    @property
    def subject_prefix(self) -> str:
        """Get a prefix string for the notification subject (e.g. [JCOM])."""
        if isinstance(self.target, Article):
            return f"[{self.journal.code}] {self.target.section} {self.target.pk}"
        else:
            return f"[{self.journal.code}]"

    def render_subject(self, recipient: Account) -> str:
        notification_subject = self.subject if self.subject else self.body[:20].replace("\n", " ")
        notification_subject = f"{self.subject_prefix} {notification_subject}"
        return notification_subject

    def _get_header_context(self, recipient: Account) -> dict:
        """Return the context for the header template."""
        from .communication_utils import get_eo_user

        message_url = self.get_absolute_url()
        message_url = self.journal.site_url(message_url)
        context = {
            "message": self,
            "recipient": recipient,
            "system": self.message_type == Message.MessageTypes.SYSTEM,
            "message_url": message_url,
            "code": self.journal.code,
            "eo_user": get_eo_user(self.journal),
        }
        if isinstance(self.target, Article):
            workflow = self.target.articleworkflow
            status_url = self.journal.site_url(workflow.get_absolute_url())
            show_authors = permissions.is_article_manager(workflow, recipient) or permissions.is_one_of_the_authors(
                workflow, recipient
            )
            context["article"] = self.target
            context["show_authors"] = show_authors
            context["status_url"] = status_url
        return context

    def _render_read_more(self, recipient: Account) -> str:
        """Render the "read more" link for the given recipient."""
        context = self._get_header_context(recipient)
        return render_to_string("wjs_review/write_message/elements/read_more.html", context)

    def _render_header(self, recipient: Account) -> str:
        """Render the header for the given recipient."""
        if isinstance(self.target, Article):
            context = self._get_header_context(recipient)
            return render_to_string("wjs_review/write_message/elements/header.html", context)
        return ""

    def _render_footer(self, recipient: Account) -> str:
        """Render the header for the given recipient."""
        context = self._get_header_context(recipient)
        return render_to_string("wjs_review/write_message/elements/footer.html", context)

    def render_message(self, recipient: Account) -> dict[str, str]:
        """Render the message content for the given recipient."""
        notification_body = self.body

        header = self._render_header(recipient)
        footer = self._render_footer(recipient)

        notification_body = f"{header}{notification_body}{footer}"

        if self.message_type == Message.MessageTypes.SYSTEM and self.SPLIT_MARKER in notification_body:
            notification_body, __ = notification_body.split(self.SPLIT_MARKER)
            notification_body = f"{notification_body} {self._render_read_more(recipient)}"

        notification_body_text = html2text.html2text(notification_body)

        return {
            "html": notification_body,
            "text": notification_body_text,
        }

    def emit_notification(self, from_email=None):
        """Send a notification.

        :param from_email is passed directly to django.core.mail.send_mail (therefore, if it's None, the
        DEFAULT_FROM_EMAIL is used).

        """
        if getattr(settings, "NO_NOTIFICATION", None):
            return

        if self.verbosity == Message.MessageVerbosity.TIMELINE:
            return
        if self.message_type == Message.MessageTypes.NOTE:
            return

        for recipient in self.recipients.all():
            body = self.render_message(recipient)
            subject = self.render_subject(recipient)
            send_mail(
                subject,
                body["text"],
                # TODO: use fake "no-reply": the mailbox should be real, but with an autoresponder
                from_email,
                [recipient.email],
                fail_silently=False,
                html_message=body["html"],
            )


class MessageRecipients(models.Model):
    """The m2m relation between a message and its recipients."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE)
    recipient = models.ForeignKey(Account, on_delete=models.CASCADE)

    read = models.BooleanField(
        default=False,
        help_text="True only if the message has been read by this recipient.",
    )
    # Hmmmm... the following won't work...
    protected = models.BooleanField(
        default=False,
        help_text="When True, the name of this recipient will not be shown.",
    )


class MessageThread(models.Model):
    """Relate two messages."""

    class MessageRelation(models.TextChoices):
        """Describe the relation between two messages."""

        FORWARD = "Forward", _("The child message is a forward of the parent message.")
        REPLY = "Reply", _("The child message is a reply to the parent message.")

    parent_message = models.ForeignKey(Message, related_name="children", on_delete=models.CASCADE)
    child_message = models.ForeignKey(Message, related_name="parents", on_delete=models.CASCADE)
    relation_type = models.CharField(max_length=101, choices=MessageRelation.choices)


class WjsEditorAssignment(EditorAssignment):
    review_rounds = models.ManyToManyField("review.ReviewRound", verbose_name=_("Managed review rounds"), blank=True)

    objects = WjsEditorAssignmentQuerySet.as_manager()

    class Meta:
        verbose_name = _("Editor assignment")
        verbose_name_plural = _("Editor assignments")
        get_latest_by = "assigned"


class PastEditorAssignment(models.Model):
    """A record of past editor assignments."""

    class DeclineReasons(models.TextChoices):
        """Reasons that an Editor can select when declining an assignment."""

        JCOM_BUSY = "JCOM_BUSY", _("already too busy with JCOM editorial work")
        BUSY = "BUSY", _("too busy in general")
        OUTSIDE_EXPERTISE = "OUTSIDE_EXPERTISE", _("paper outside my area of expertise")
        NO_REVIEWER = "NO_REVIEWER", _("unable to find an appropriate reviewer")
        OTHER = "OTHER", _("other")

    article = models.ForeignKey(
        Article,
        verbose_name=_("Article"),
        on_delete=models.CASCADE,
        related_name="past_editor_assignments",
    )
    editor = models.ForeignKey(Account, verbose_name=_("Editor"), on_delete=models.CASCADE)
    date_assigned = models.DateTimeField(_("Date assigned"))
    date_unassigned = models.DateTimeField(_("Date unassigned"))
    review_rounds = models.ManyToManyField("review.ReviewRound", verbose_name=_("Managed review rounds"), blank=True)
    decline_reason = models.CharField(
        blank=True, null=True, choices=DeclineReasons.choices, verbose_name=_("Decline reason")
    )
    decline_text = models.TextField(blank=True, null=True, verbose_name=_("Decline optional text"))

    class Meta:
        verbose_name = _("Past editor assignment")
        verbose_name_plural = _("Past editor assignments")


class EditorRevisionRequest(RevisionRequest):
    """Extend Janeway's RevisionRequest model to add review round reference."""

    review_round = models.ForeignKey("review.ReviewRound", verbose_name=_("Review round"), on_delete=models.PROTECT)
    cover_letter_file = models.FileField(blank=True, null=True, verbose_name=_("Cover letter file"))
    article_history = models.JSONField(blank=True, null=True, verbose_name=_("Article history"))
    manuscript_files = models.ManyToManyField("core.File", null=True, blank=True, related_name="+")
    data_figure_files = models.ManyToManyField("core.File", null=True, blank=True, related_name="+")
    supplementary_files = models.ManyToManyField("core.SupplementaryFile", null=True, blank=True, related_name="+")
    source_files = models.ManyToManyField(
        "core.File",
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ("date_requested",)

    @property
    def permission_label(self) -> str:
        return _(f"Editor {self.editor}'s report")


class WorkflowReviewAssignment(ReviewAssignment):
    """
    Extend Janeway's ReviewAssignment model to add author cover letter permissions.

    This model will usually be accessed by using its reference in ReviewAssignment:

    - `review_assignment.workflowreviewassignment.author_note_file`
    - `review_assignment.workflowreviewassignment.author_note_text`

    because in most cases we are going to use janeway's views and templates as a base where the original model is used.

    This is not a big deal as we don't have performance concerns in these templates.
    """

    #  Quando si aggiungono nuovi campi modificare il metodo AssignToReviewer._assign_reviewer per evitare di ottenere
    #  errori nel salvataggio.
    author_note_visible = models.BooleanField(_("Author's cover letter visible (if available)"), default=True)
    report_form_answers = models.JSONField(default=dict, verbose_name=_("Report form answers"))
    editor_invite_message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="editor_invite_messages",
        verbose_name=_("Editor invite message"),
    )
    objects = WorkflowReviewAssignmentQuerySet.as_manager()

    # Map janeway's statuses to an ordered dict to map to our own statuses
    statuses = {
        "wait": "wait",
        "accept": "accept",
        "complete": "complete",
        "declined": "declined",
        "withdrawn": "declined",
    }

    @property
    def permission_label(self) -> str:
        return _(f"Reviewer {self.reviewer}'s report")

    @property
    def previous_review_round(self) -> Optional[ReviewRound]:
        """Return the previous review round."""
        if self.review_round.round_number < 2:
            return None
        return ReviewRound.objects.filter(
            article=self.article,
            round_number=self.review_round.round_number - 1,
        ).first()

    @property
    def status(self) -> "ReviewAssignmentStatus":
        from .custom_types import ReviewAssignmentStatus

        status = super().status
        return ReviewAssignmentStatus(
            self.statuses.get(status["code"], status["code"]),
        )

    def get_actions_for_user(self, user: Account, tag: str) -> list["ReviewAssignmentActionConfiguration"]:
        from .states import BaseState

        state_class = BaseState.get_state_class(self.article.articleworkflow)
        if state_class and state_class.review_assignment_actions:
            return [
                action.as_dict(self, user)
                for action in state_class.review_assignment_actions
                if action.is_available(self, user, tag)
            ]

    def unsent_reminders(self) -> QuerySet["Reminder"]:
        return Reminder.objects.filter(
            object_id=self.pk,
            content_type=ContentType.objects.get_for_model(self),
            date_sent__isnull=True,
        )

    @property
    def attention_condition(self) -> Optional["ReviewAssignmentAttentionCondition"]:
        """Provide details if the review assignment needs attention by the staff."""
        from .custom_types import ReviewAssignmentAttentionCondition

        if self.is_complete:
            return

        if self.date_due < timezone.now().date():
            return ReviewAssignmentAttentionCondition(
                code="late",
                message=_("No feedback after %s day(s).") % (timezone.now().date() - self.date_due).days,
                style="border border-danger",
                icon_value='<span class="ra-condition-icon"><i class="bi bi-clock-fill"></i></span>',
            )
        if self.date_accepted and not self.is_complete:
            return ReviewAssignmentAttentionCondition(
                code="pending",
                message=_("Report deadline: %s") % date_format(self.date_due, settings.DATE_FORMAT),
            )


class ProphyAccount(models.Model):
    """PROPHY Management Models"""

    author_id = models.IntegerField(unique=True)

    affiliation = models.CharField(max_length=1000, null=True, blank=True, verbose_name=_("Institution"))
    articles_count = models.IntegerField(blank=True, null=True)
    authors_groups = models.CharField(blank=True, null=True, max_length=1000)
    citations_count = models.IntegerField(blank=True, null=True)
    email = models.EmailField(unique=True, null=True, verbose_name=_("Email"))
    h_index = models.IntegerField(blank=True, null=True)
    name = models.CharField(
        max_length=900,
        null=True,
        blank=False,
        verbose_name=_("Full name"),
    )
    first_name = models.CharField(max_length=300, null=True, blank=False, verbose_name=_("First name"))
    middle_name = models.CharField(max_length=300, null=True, blank=True, verbose_name=_("Middle name"))
    last_name = models.CharField(max_length=300, null=True, blank=False, verbose_name=_("Last name"))
    suffix = models.CharField(
        max_length=300,
        null=True,
        blank=True,
        help_text=_("Name suffix eg. jr"),
    )
    orcid = models.CharField(max_length=40, null=True, blank=True, verbose_name=_("ORCiD"))
    url = models.CharField(max_length=300, null=True, blank=True, verbose_name="Prophy author url")
    correspondence = models.ForeignKey(Correspondence, null=True, blank=True, on_delete=models.CASCADE)


class ProphyCandidate(models.Model):
    prophy_account = models.ForeignKey(ProphyAccount, on_delete=models.CASCADE)
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    score = models.FloatField(
        null=True,
        blank=False,
        verbose_name=_("Prophy score"),
    )
    prophy_manuscript_id = models.IntegerField(blank=True, null=True)


class PermissionAssignment(TimeStampedModel):
    class PermissionType(models.TextChoices):
        """Full set of permissions."""

        ALL = "all", _("visible (editor's and reviewer's identity revealed)")
        NO_NAMES = "no_names", _("visible (editor’s identity NOT revealed)")
        DENY = "deny", _("not visibile")

    class BinaryPermissionType(models.TextChoices):
        """Subset of PermissionType for basic allow / deny check."""

        ALL = "all", _("visible")
        DENY = "deny", _("not visibile")

    user = models.ForeignKey(Account, verbose_name=_("User"), on_delete=models.CASCADE)
    content_type = models.ForeignKey(
        ContentType,
        verbose_name=_("Content type"),
        on_delete=models.CASCADE,
        null=False,
    )
    object_id = models.PositiveIntegerField(
        verbose_name=_("Object ID"),
        blank=False,
        null=False,
    )
    target = GenericForeignKey(
        "content_type",
        "object_id",
    )
    permission = models.CharField(
        _("Permission set"),
        max_length=255,
        blank=False,
        default=PermissionType.NO_NAMES,
        choices=PermissionType.choices,
    )
    permission_secondary = models.CharField(
        _("Extra permission set"),
        help_text=_("Used to assign permissions to parts of the objects (eg: cover letter etc)"),
        max_length=255,
        blank=False,
        default=BinaryPermissionType.DENY,
        choices=BinaryPermissionType.choices,
    )

    class Meta:
        unique_together = ("user", "content_type", "object_id")
        verbose_name = _("Permission assignment")
        verbose_name_plural = _("Permission assignments")

    def __str__(self):
        return f"{self.user} - {self.content_type.model} - {self.permission}"

    def match_permission(self, permission_type: PermissionType) -> bool:
        """
        Check if the current permission matches the requested permission type.

        If the current permission is set to deny, the function will return False bypassing the check of the
        requested permission because the user has no permission anyway.
        If the current permission is set to all, the function will return True bypassing the check of the
        requested permission because the user has all permission.
        If requested permission is empty, the function will return True for any value of the current permission except
        for deny.

        :param permission_type:
        :type permission_type: PermissionAssignment.PermissionType
        :return: requested permission matches the current permission
        :rtype: bool
        """
        if self.permission == PermissionAssignment.PermissionType.DENY.value:
            return False
        if self.permission == PermissionAssignment.PermissionType.ALL.value:
            return True
        return self.permission == permission_type.value or permission_type.value == ""

    def match_secondary_permission(self, permission_type: PermissionType) -> bool:
        """
        Check if the current secondary permission matches the requested permission type.

        Secondary permission is used to assign permissions to parts of the objects:

        - Article: Article.comments_for_editors
        - EditorRevisionRequest: EditorRevisionRequest.author_note,

        :param permission_type:
        :type permission_type: PermissionAssignment.PermissionType
        :return: requested permission matches the current permission
        :rtype: bool
        """
        if self.permission_secondary == PermissionAssignment.PermissionType.DENY.value:
            return False
        if self.permission_secondary == PermissionAssignment.PermissionType.ALL.value:
            return True
        return self.permission_secondary == permission_type.value or permission_type.value == ""


class Reminder(models.Model):
    """A message sent to someone to remind him that some due date has elapsed."""

    class ReminderCodes(models.TextChoices):
        # specs#618
        REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1 = "REEA1", _("Reviewer should evaluate assignment")
        REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2 = "REEA2", _("Reviewer should evaluate assignment")
        REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3 = "REEA3", _("Reviewer should evaluate assignment")
        REVIEWER_SHOULD_WRITE_REVIEW_1 = "REWR1", _("Reviewer should write review")
        REVIEWER_SHOULD_WRITE_REVIEW_2 = "REWR2", _("Reviewer should write review")
        # specs#619
        EDITOR_SHOULD_SELECT_REVIEWER_1 = "EDSR1", _("Editor should select reviewer")
        EDITOR_SHOULD_SELECT_REVIEWER_2 = "EDSR2", _("Editor should select reviewer")
        EDITOR_SHOULD_SELECT_REVIEWER_3 = "EDSR3", _("Editor should select reviewer")
        EDITOR_SHOULD_MAKE_DECISION_1 = "EDMD1", _("Editor should make decision")
        EDITOR_SHOULD_MAKE_DECISION_2 = "EDMD2", _("Editor should make decision")
        EDITOR_SHOULD_MAKE_DECISION_3 = "EDMD3", _("Editor should make decision")
        # specs#635
        AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_1 = "AUMJR1", _("Author should submit major revision")
        AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2 = "AUMJR2", _("Author should submit major revision")
        AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_1 = "AUMIR1", _("Author should submit minor revision")
        AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2 = "AUMIR2", _("Author should submit minor revision")
        AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_1 = "AUTCR1", _("Author should submit technical revision")
        AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2 = "AUTCR2", _("Author should submit technical revision")
        DIRECTOR_SHOULD_ASSIGN_EDITOR_1 = "DIRASED1", _("Director should assign editor")
        DIRECTOR_SHOULD_ASSIGN_EDITOR_2 = "DIRASED2", _("Director should assign editor")

    class ReminderClasses(models.TextChoices):
        # specs#618
        REVIEWER_SHOULD_EVALUATE_ASSIGNMENT = "REEA", _("Reviewer should evaluate assignment")
        REVIEWER_SHOULD_WRITE_REVIEW = "REWR", _("Reviewer should write review")
        # specs#619
        EDITOR_SHOULD_SELECT_REVIEWER = "EDSR", _("Editor should select reviewer")
        EDITOR_SHOULD_MAKE_DECISION = "EDMD", _("Editor should make decision")
        # specs#635
        AUTHOR_SHOULD_SUBMIT_REVISION = "AUM", _("Author should submit revision")
        AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION = "AUTCR", _("Author should submit technical revision")
        DIRECTOR_SHOULD_ASSIGN_EDITOR = "DIRASED", _("Director should assign editor")

    code = models.CharField(
        _("Reminder code"),
        max_length=10,
        choices=ReminderCodes.choices,
    )
    date_created = models.DateTimeField(_("Date of creation"), auto_now_add=True)
    date_due = models.DateField(_("Date due"), help_text="The date when the reminder should be sent")
    date_sent = models.DateTimeField(
        _("Date sent"), null=True, blank=True, help_text="The date when the reminder was sent"
    )
    disabled = models.BooleanField(_("Disabled"), default=False)
    clemency_days = models.IntegerField(_("Clemency dats"), default=0)

    # The "target" of a reminder can be something like a ReviewAssigment (for reminders to reviewers), an
    # WjsEditorAssignment (for reminders to editors), but also just an Article (e.g. for reminders to EO related to
    # articles with no editor assigned).
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
    )
    object_id = models.PositiveIntegerField(
        null=True,
    )
    target = GenericForeignKey(
        "content_type",
        "object_id",
    )

    recipient = models.ForeignKey(
        Account, verbose_name=_("Recipient"), on_delete=models.CASCADE, related_name="reminders_that_i_receive"
    )
    # TODO: it's ok to drop a reminder if the recipient disappears, but the actor might be different...
    # Does the business logic prevent this problem?
    # E.g. to delete the editor, one should first re-assign the article and manage the reviewassignments anyway...
    actor = models.ForeignKey(
        Account, verbose_name=_("Actor"), on_delete=models.CASCADE, related_name="reminders_that_i_send"
    )
    hide_actor_name = models.BooleanField(
        verbose_name=_("Hide actor name"),
        default=True,
        help_text=_("Hide the name of the actor in the message body / subject / From-header"),
    )

    # Subject and body are taken from .settings.reminders.
    # That dictionary should contain the template that will be rendered to create the reminder message.
    # The message is rendered when the reminder is created. This should allow for the editing of existing reminders.
    message_subject = models.TextField(_("Message subject"))
    message_body = models.TextField(_("Message body"))

    class Meta:
        verbose_name = _("Reminder")
        verbose_name_plural = _("Reminders")
        app_label = "wjs_review"

    def __str__(self):
        return self.code

    def get_from_email(self) -> str:
        """
        Compute the "From:" header for the actor email.

        Name must be hidden if the user cannot see the actor name.

        The email is always the same, but the name part changes.
        E.g.
        From: Matteo Gamboz <wjs-support@medialab.sissa.it>
        """
        if self.get_related_article():
            if not permissions.can_see_other_user_name(
                instance=self.get_related_article().articleworkflow, sender=self.actor, recipient=self.recipient
            ):
                return settings.DEFAULT_FROM_EMAIL
        name = self.actor.full_name()
        email = settings.DEFAULT_FROM_EMAIL
        from_header = f"{name} <{email}>"
        return from_header

    def get_related_article(self) -> Optional[Article]:
        """Try to find the article that this reminder is related to."""
        if article := getattr(self.target, "article", None):
            return article
        else:
            return None

    @property
    def reminder_level(self):
        """Return the order of the reminder among the same classes."""
        return self.code[-1]

    def render_subject(self) -> str:
        """Render the reminder message subject."""
        return render_template(self.message_subject, {"reminder": self, "article": self.get_related_article()})

    def render_body(self) -> str:
        """Render the reminder message body."""
        return render_template(self.message_body, {"reminder": self, "article": self.get_related_article()})

    def create_message(self) -> Message:
        """Create a message from the reminder."""
        from .communication_utils import log_operation
        from .reminders.settings import ReminderManager

        reminder_article = self.get_related_article()
        if reminder_article is None:
            raise ValueError(f"Unknown article for reminder {self.id} ({self.code})")
        setting = ReminderManager.get_settings(self)

        message = log_operation(
            article=reminder_article,
            actor=self.actor,
            recipients=[self.recipient],
            message_subject=self.message_subject,
            message_body=self.message_body,
            message_type=Message.MessageTypes.SYSTEM,
            flag_as_read_by_eo=setting.flag_as_read_by_eo,
            flag_as_read=setting.flag_as_read,
        )
        return message


class LatexPreamble(models.Model):
    """Templates to generate 'preambolo automatico' to be included in files to typeset"""

    journal = models.ForeignKey(Journal, on_delete=models.CASCADE)
    preamble = models.TextField(null=False, blank=False)

    class Meta:
        verbose_name = _("LaTeX preamble")


class WjsSection(Section):
    """This model contains the section codes to be used in the preamble of the article."""

    section = models.OneToOneField(Section, on_delete=models.CASCADE, primary_key=True, parent_link=True)
    doi_sectioncode = models.CharField(max_length=2, null=True, blank=True)
    pubid_and_tex_sectioncode = models.CharField(max_length=1, null=True, blank=True)
