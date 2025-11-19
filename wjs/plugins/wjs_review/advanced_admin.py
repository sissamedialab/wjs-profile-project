from django.apps import apps
from django.contrib import admin
from wjs.advanced_admin.admin import advanced_admin_site

WjsSection = apps.get_model("wjs_review", "WjsSection")


@admin.register(WjsSection, site=advanced_admin_site)
class WjsSectionAdmin(admin.ModelAdmin):
    fields = ["doi_sectioncode", "pubid_and_tex_sectioncode", "description"]
    list_display = ["name", "journal"]
    list_filter = ["journal"]

    def has_add_permission(self, request):  # noqa: PLR6301
        """
        Prevents adding new sections through this interface.

        New sections must be created in the standard Django admin first,
        then their WjsSection parameters can be edited here.
        """
        return False
