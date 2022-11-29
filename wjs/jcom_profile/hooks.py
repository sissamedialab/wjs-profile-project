"""Hooks."""
from django.template.loader import render_to_string


def prova_hook(request_context):
    """Test hooks."""
    # TODO: drop me and use django blocks
    template_name = "field.html"
    context = {"form": request_context.get("form"), "journal_settings": request_context.get("journal_settings")}
    rendered = render_to_string(template_name, context)
    return rendered


def extra_edit_profile_parameters_hook(request_context):
    """Add hook to add assignment parameter button."""
    user = request_context.request.user
    journal = request_context.request.journal
    rendered = ""
    if user and journal and user.check_role(journal, "editor"):
        template_name = "extra_edit_profile_parameters.html"
        rendered = render_to_string(template_name, {})
    return rendered
