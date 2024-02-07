from django.contrib import admin

from .models import ProphyAccount, ProphyCandidate


@admin.register(ProphyCandidate)
class ProphyCandidateAdmin(admin.ModelAdmin):
    """Helper class to "admin" ProphyCandidate."""

    list_display = ["prophy_account_id", "score", "article_id"]


@admin.register(ProphyAccount)
class ProphyAccountAdmin(admin.ModelAdmin):
    """Helper class to "admin" ProphyAccount."""

    list_display = ["id", "author_id", "email", "name"]
