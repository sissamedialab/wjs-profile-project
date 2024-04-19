from django.contrib import admin

from .models import ArticleWorkflow, ProphyAccount, ProphyCandidate


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
