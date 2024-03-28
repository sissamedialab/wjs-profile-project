"""Hooks."""
from django.template.loader import render_to_string
from django.utils.translation import gettext as _


def extra_core_fields_hook(request_context):
    """Add hook to render extra profile fields."""
    template_name = "elements/accounts/extra_core_fields.html"
    context = {"form": request_context.get("form"), "journal_settings": request_context.get("journal_settings")}
    rendered = render_to_string(template_name, context)
    return rendered


def extra_edit_profile_parameters_hook(request_context):
    """Add hook to add assignment parameter card."""
    user = request_context.request.user
    journal = request_context.request.journal
    rendered = ""
    if user and journal and user.check_role(journal, "section-editor"):
        template_name = "extra_edit_profile_card_block.html"
        rendered = render_to_string(
            template_name,
            {
                "card_title": _("Edit assignment parameters"),
                "card_paragraph": _("Go to your your assignment parameters by clicking the button below."),
                "url_name": _("assignment_parameters"),
                "button_text": _("Assignment parameters"),
            },
        )
    return rendered


def extra_edit_subscription_hook(request_context):
    """Add hook to add newsletters card."""
    template_name = "extra_edit_profile_card_block.html"
    rendered = render_to_string(
        template_name,
        {
            "card_title": _("Newsletters"),
            "card_paragraph": _("Edit your subscription settings by clicking the button below."),
            "url_name": _("edit_newsletters"),
            "button_text": _("Edit my subscription"),
        },
    )
    return rendered
