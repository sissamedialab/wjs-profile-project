from django.contrib import admin
from submission.admin import ArticleAdmin

from .models import (
    ArticleWorkflow,
    EditorDecision,
    LatexPreamble,
    ProphyAccount,
    ProphyCandidate,
    WjsSection,
    WorkflowReviewAssignment,
)


@admin.register(ProphyCandidate)
class ProphyCandidateAdmin(admin.ModelAdmin):
    """Helper class to "admin" ProphyCandidate."""

    list_display = ["prophy_account_id", "score", "article_id"]


@admin.register(ProphyAccount)
class ProphyAccountAdmin(admin.ModelAdmin):
    """Helper class to "admin" ProphyAccount."""

    list_display = ["id", "author_id", "email", "name"]


@admin.register(ArticleWorkflow)
class ArticleWorkflowAdmin(admin.ModelAdmin):
    """Helper class to "admin" ArticleWorkflow."""

    list_display = ["id", "article", "state"]
    list_filter = ["state"]
    search_fields = ["article__title"]


@admin.register(LatexPreamble)
class LatexPreambleAdmin(admin.ModelAdmin):
    """Helper class to "admin" LatexPreamble."""

    list_display = ["journal", "preamble"]
    list_filter = ["journal"]
    search_fields = ["journal__code"]


@admin.register(WjsSection)
class WjsSectionAdmin(admin.ModelAdmin):
    """Helper class to "admin" WjsSection."""

    list_display = ["section", "pubid_and_tex_sectioncode", "doi_sectioncode"]
    list_filter = ["section"]
    search_fields = ["section__name"]


@admin.register(EditorDecision)
class EditorDecisionAdmin(admin.ModelAdmin):
    """Helper class to "admin" EditorDecision."""

    list_display = ["workflow", "decision", "decision_editor_report"]
    list_filter = ["decision"]


@admin.register(WorkflowReviewAssignment)
class WorkflowReviewAssignmentAdmin(admin.ModelAdmin):
    """Helper class to "admin" WorkflowReviewAssignment."""

    list_display = ["article_id", "review_round_id", "report_form_answers"]
    list_filter = ["article"]
    search_fields = ["article"]


class ArticleWorkflowInline(admin.StackedInline):
    """Helper class to "inline" AW in Article."""

    model = ArticleWorkflow
    fields = [
        "state",
        "eo_in_charge",
        # "supplementary_files_at_acceptance", can't really choose anything...
        "publication_galleys_source_file",
        "production_flag_no_queries",
        "production_flag_galleys_ok",
        "production_flag_no_checks_needed",
        "latest_state_change",
        "latex_desc",
        "social_media_short_description",
    ]


ArticleAdmin.inlines.append(ArticleWorkflowInline)
