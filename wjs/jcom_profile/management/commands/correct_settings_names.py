"""Correct existing settings names.

Una-tantum command needed because of name changed during specs#901.
"""

from core.models import Setting, SettingValue
from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Correct existing settings names."  # noqa A003

    def handle(self, *args, **options):
        self.rename_settings()
        self.drop_obsolete_settings()
        self.set_jcom_defaults_over_janeways()

    def rename_settings(self):
        settings_to_rename = (
            # jcom_profile
            (
                "submission_coauthors_acknowledgment",
                "submission_coauthors_acknowledgement_body",  # Please also note the "e" in "acknowledgEment"
            ),
            (
                "subject_submission_coauthors_acknowledgement",
                "submission_coauthors_acknowledgement_subject",
            ),
            # wjs_review
            (
                "declined_review_message",
                "declined_review_notice",
            ),
            (
                "requeue_article_message",
                "requeue_article_body",
            ),
            (
                "review_decision_requires_resubmission_message",
                "review_decision_requires_resubmission_body",
            ),
            (
                "editor_decline_assignment_body",
                "editor_decline_assignment_default",
            ),
            (
                "editor_deassign_reviewer_body",
                "editor_deassign_reviewer_default",
            ),
            # At a certain point, we had our own version of these settings.
            # in #958 #959 we decided to use Janeway's setting (with our values)
            # - editor_assignment ⬄ wjs_editor_assignment
            # - review_assignment ⬄ review_invitation_message (but we add review_invitation_message_body)
            # - review_withdrawl ⬄ review_withdraw_...
        )

        for old_name, new_name in settings_to_rename:
            # I don't want to use `update` because it is designed for bulk-operations
            # and I want to be sure that I'm operating on a single setting
            logger.debug(f"Correcting {old_name} into {new_name}")
            try:
                setting = Setting.objects.get(name=old_name)
            except Setting.DoesNotExist:
                try:
                    Setting.objects.get(name=new_name)
                except Setting.DoesNotExist:
                    logger.warning(f"   no setting namde either {new_name} or {old_name}. Doing nothing.")
                else:
                    logger.debug(f"   setting {new_name} already in place. Doing nothing.")
            else:
                setting.name = new_name
                setting.save()

    def drop_obsolete_settings(self):
        """Drop settings we had second thoughts about."""
        settings_to_drop = (
            # "revision_submission" first and second incarnation
            # (replaced by revisions_complete_editor_notification)
            "revision_submission_message",
            "revision_submission_body",
            "do_review_message",
            "editor_deassign_reviewer_system_subject",
            "editor_deassign_reviewer_system_body",
            "review_withdraw_notice",  # old name of the following
            "review_withdraw_body",  # old name of the following
            "review_withdraw_default",
            "review_withdraw_subject",
            "review_invitation_message",  # old name of the following
            "review_invitation_message_default",
            "review_invitation_message_subject",
            "review_decision_revision_request_body",  # replaced by request_revision**s**
            "review_decision_revision_request_subject",
            "typesetting_assignment_subject",  # replaced by typesetter_notification
            "typesetting_assignment_body",
            "author_sends_corrections_subject",  # replaced by notify_typesetter_proofing_changes
            "author_sends_corrections_body",
            "typesetting_generated_galleys_subject",  # hardcoded as async-event notification
            "typesetting_generated_galleys_body",
        )
        for setting_name in settings_to_drop:
            logger.debug(f"Dropping {setting_name}")
            group_name = "wjs_review"
            try:
                setting = Setting.objects.get(name=setting_name, group__name=group_name)
            except Setting.DoesNotExist:
                logger.debug(f"   setting {setting_name} in group {group_name} does not exist. Doing nothing.")
            else:
                setting.delete()

    def set_jcom_defaults_over_janeways(self):
        update_setting_default(
            "submission_acknowledgement",
            "email",
            """Dear {{ article.correspondence_author.full_name }}, <br>
<br>
Thank you for submitting [...]
the {{ article.section.name }} "{{ article.title }}" to {{ article.journal.name }}.
<br>
<br>
Please check all data and files from your manuscript
<a href="{{ article.articleworkflow.url }}">web page</a>
and contact the Editorial Office if anything needs correction.
<br>
<br>
Your manuscript has been assigned to the appropriate editor in charge and
the review process will start as soon as possible.
We will be in touch as soon as the peer-review process has been completed.
<br>
<br>
From now on, please make sure any message is sent through the appropriate “write a message”
button from your manuscript web page, so that a record is stored in the system.<br>
<br>
Best regards,<br>
{{ journal.code }} Journal
""",
        )
        update_setting_default("subject_submission_acknowledgement", "email_subject", """Submitted""")

        update_setting_default("revision_digest", "email", "NOT USED IN WJS")
        update_setting_default("subject_revision_digest", "email_subject", "NOT USED IN WJS")
        update_setting_default("editor_digest", "email", "NOT USED IN WJS")
        update_setting_default("subject_editor_digest", "email_subject", "NOT USED IN WJS")
        update_setting_default("reviewer_digest", "email", "NOT USED IN WJS")
        update_setting_default("subject_reviewer_digest", "email_subject", "NOT USED IN WJS")
        update_setting_default("production_assign_article", "email", "NOT USED IN WJS")
        update_setting_default("subject_production_assign_article", "email_subject", "NOT USED IN WJS")
        update_setting_default("notification_submission", "email", "NOT USED IN WJS")
        update_setting_default("subject_notification_submission", "email_subject", "NOT USED IN WJS")
        update_setting_default("copyeditor_assignment_notification", "email", "NOT USED IN WJS")
        update_setting_default("subject_copyeditor_assignment_notification", "email_subject", "NOT USED IN WJS")
        update_setting_default("copyeditor_notify_editor", "email", "NOT USED IN WJS")
        update_setting_default("subject_copyeditor_notify_editor", "email_subject", "NOT USED IN WJS")
        update_setting_default("copyeditor_notify_author", "email", "NOT USED IN WJS")
        update_setting_default("subject_copyeditor_notify_author", "email_subject", "NOT USED IN WJS")
        update_setting_default("copyeditor_reopen_task", "email", "NOT USED IN WJS")
        update_setting_default("subject_copyeditor_reopen_task", "email_subject", "NOT USED IN WJS")
        update_setting_default("author_copyedit_complete", "email", "NOT USED IN WJS")
        update_setting_default("subject_author_copyedit_complete", "email_subject", "NOT USED IN WJS")
        update_setting_default("production_manager_notification", "email", "NOT USED IN WJS")
        update_setting_default("subject_production_manager_notification", "email_subject", "NOT USED IN WJS")

        # Replaces WJS's revision_submission_[subject,body]
        update_setting_default(
            "revisions_complete_editor_notification",
            "email",
            """Dear Dr. {{ editor.full_name }},
<br><br>
{% if revision.type == "tech_revisions" %}
The author has just updated metadata for {{ article.section.name }} "{{ article.title }}". The change(s) is/are visible
on the web pages only.  If either the title and/or the abstract have been changed, the pdf file will be updated either
in a revised version (if requested) or during the stage of proofreading (in case of acceptance for publication).
{% else %}
Please connect to the manuscript web page to download the {{ article.section.name }} resubmitted in reply to your
request for revision.  You are kindly requested [...] to either select reviewers or make a decision by
{{ default_editor_assign_reviewer_days }} days.
{% endif %}
<br><br>
Thank you and best regards,
<br>
{{ journal.code }} Journal
""",
        )
        update_setting_default(
            "subject_revisions_complete_editor_notification",
            "email_subject",
            """{% if revision.type == "tech_revisions" %}Metadata updated{% else %}Resubmitted{% endif %}""",
        )

        update_setting_default(
            "revisions_complete_receipt",
            "email",
            """Dear {{ revision.article.correspondence_author.full_name }},
<br><br>
Thank you for resubmitting "{{ revision.article.safe_title }}".
We will be in touch with further information at the end of the review process
as soon as possible.
<br><br>
Best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default("subject_revisions_complete_receipt", "email_subject", """Resubmitted""")

        # Review invitation / assignment

        update_setting_default(
            "review_assignment",
            "email",
            """Dear {{ reviewer.full_name }},<br>
{% if already_reviewed %}
    I am writing to ask for your help in reviewing the revised version of the {{ article.section.name }}
titled "{{ article.title }}" for which you have been so kind as to review the previous version.
{% else %}
    I am writing to ask for your help in reviewing the {{ article.section.name }}
titled "{{ article.title }}" for {{ journal.code }}.
{% endif %}
Please find the automatically generated instructions for reviewers appended below.
<br>
<br>
In the hope that you will accept my request, I would like to thank you in advance for your cooperation.
<br>
<br>
Kind regards,
<br>
{{ request.user.signature|safe }}
<br>
{{ journal.code }} Editor in charge
""",
        )

        update_setting_default(
            "subject_review_assignment",
            "email_subject",
            "{{ reviewer.full_name }} invited to review",
        )

        update_setting_default("review_request_sent", "email", "NOT USED IN WJS")
        update_setting_default("subject_review_request_sent", "email_subject", "NOT USED IN WJS")
        update_setting_default("default_review_reminder", "email", "NOT USED IN WJS")
        update_setting_default("subject_default_review_reminder", "email_subject", "NOT USED IN WJS")
        update_setting_default("accepted_review_reminder", "email", "NOT USED IN WJS")
        update_setting_default("subject_accepted_review_reminder", "email_subject", "NOT USED IN WJS")
        update_setting_default("review_decision_undecline", "email", "NOT USED IN WJS")
        update_setting_default("subject_review_decision_undecline", "email_subject", "NOT USED IN WJS")
        update_setting_default("share_reviews_notification", "email", "NOT USED IN WJS")
        update_setting_default("subject_share_reviews_notification", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_se_draft_declined", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_se_draft_declined", "email_subject", "NOT USED IN WJS")
        update_setting_default("submission_access_request_notification", "email", "NOT USED IN WJS")
        update_setting_default("subject_submission_access_request_notification", "email_subject", "NOT USED IN WJS")
        update_setting_default("submission_access_request_complete", "email", "NOT USED IN WJS")
        update_setting_default("subject_submission_access_request_complete", "email_subject", "NOT USED IN WJS")
        update_setting_default("draft_message", "email", "NOT USED IN WJS")
        update_setting_default("subject_draft_message", "email_subject", "NOT USED IN WJS")
        update_setting_default("draft_editor_message", "email", "NOT USED IN WJS")
        update_setting_default("subject_draft_editor_message", "email_subject", "NOT USED IN WJS")
        # Don't confuse editor_new_submission with editor_assignment:
        update_setting_default("editor_new_submission", "email", "NOT USED IN WJS")
        update_setting_default("subject_editor_new_submission", "email_subject", "NOT USED IN WJS")

        update_setting_default(
            "review_withdrawl",
            "email",
            """Dear Dr. {{ assignment.reviewer.full_name }},
<br><br>
This is to confirm that you are no longer requested to review this submission.
<br><br>
{{ article.journal.code }} looks forward to soon having another opportunity
of availing itself of your expertise.
<br><br>
Best regards,
<br><br>
{{ request.user.signature|safe }}<br>
{{ article.journal.code }} Editor in charge
""",
            description=_(
                """The default body of the notification sent to the reviewer when editor deassigned him.
This can be modified by the operator."""
            ),
        )
        update_setting_default("subject_review_withdrawl", "email_subject", "Invite to review withdrawn")

        update_setting_default("subject_editor_assignment", "email_subject", "Assignment as Editor in charge")
        update_setting_default(
            "editor_assignment",
            "email",
            """Dear Dr. {{ editor.full_name }},
<br><br>
Please connect to the manuscript web page to handle [...]
this {{ article.section.name }} as editor-in-charge.
<br><br>
Kindly select 2 reviewers within {{ default_editor_assign_reviewer_days }} days.
<br>
Should you be unable to handle it, please decline the assignment as soon as possible.
<br><br>
Thank you in advance for your cooperation and best regards,
<br><br>
{{ article.journal.code }} Journal
""",
        )

        # Warning: do not confuse settings
        # - reviewer_acknowledgement      (from rev to ed: rev accepts/declines assignment)
        # - review_accept_acknowledgement (from ed to rev: ed thanks rev for accepting)
        update_setting_default(
            "reviewer_acknowledgement",
            "email",
            """Dear {{ review_assignment.editor.full_name }},
<br><br>
reviewer {{ review_assignment.reviewer.full_name }} has
{% if review_assignment.date_accepted %}accepted
{% elif review_assignment.date_declined %}declined
{% else %}-configuration error-
{% endif %}your invite to review this {{ article.section.name }}.
For more information, please go to the <a href="{{ article.articleworkflow.url }}">manuscript web page</a>.
<br><br>
Best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default(
            "subject_reviewer_acknowledgement",
            "email_subject",
            """Reviewer {{ review_assignment.reviewer.full_name }} {% if review_assignment.date_accepted %}accepted{% elif review_assignment.date_declined %}declined{% else %}-configuration error-{% endif %} invite""",  # NOQA E501
            # Warning: here we are "casting in stone" the reviewer's name (i.e. we could'nt hide them from the timeline
            # if we wanted to), but these message are not visible by the authors anyway
        )

        update_setting_default(
            "review_accept_acknowledgement",
            "email",
            """{% load fqdn %}Dear {{ review_assignment.reviewer.full_name }},
<br><br>
Thank you for accepting {{ review_assignment.editor.full_name }}’s invite to review
the {{ article.section.name }} "{{ article.safe_title }}" for {{ article.journal.name }}.
<br><br>
Your review is expected by {{ review_assignment.date_due|date:DATE_FORMAT }}.
<br>{% journal_base_url article.journal as base_url %}
Instructions to write your review are available <a href="{{ base_url }}/site/reviewers/">here</a>.
<br>
Please also keep in mind the specificities of this article type ({{ article.section.name }}),
which are explained <a href="{{ base_url }}/site/authors/">here</a>.
<br><br>
Should you need any further information or assistance, please do not hesitate
to contact either the Editor in charge {{ review_assignment.editor.full_name }}
or the Editorial Office by using the "Write a message" button on this
<a href="{{ article.articleworkflow.url }}">manuscript web page</a>.
<br><br>
Best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default(
            "subject_review_accept_acknowledgement", "email_subject", """Thank you for accepting invite"""
        )

        # Warning: do not confuse with reviewer_acknowledgement
        update_setting_default(
            "review_decline_acknowledgement",
            "email",
            """Dear {{ review_assignment.reviewer.full_name }},
<br>
Thank you for letting us know that you are unable to review "{{ article.safe_title }}"
for {{ article.journal.name }}.
<br>
{{ article.journal.code }} looks forward to availing itself of your expertise in the future.
<br><br>
Best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default("subject_review_decline_acknowledgement", "email_subject", "Invite to review declined")

        update_setting_default(
            "review_complete_acknowledgement",
            "email",
            """Dear {{ review_assignment.editor.full_name }},
<br><br>
A review has just come in [...] for "{{ article.safe_title }}".
Please go to this <a href="{{ article.articleworkflow.url }}">{{ article.section.name }}'s web page</a>
to read it and take action, if appropriate.
<br><br>
Best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default(
            "subject_review_complete_acknowledgement",
            "email_subject",
            "Review by {{ review_assignment.reviewer.full_name }} received",
        )

        update_setting_default(
            "review_complete_reviewer_acknowledgement",
            "email",
            """Dear {{ review_assignment.reviewer.full_name }},
<br><br>
We would like to warmly thank you for completing your review of "{{ article.safe_title }}".
<br>
{{ article.journal.code }} looks forward to availing itself again of your expertise in the future.
<br><br>
Thank you again and best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default(
            "subject_review_complete_reviewer_acknowledgement", "email_subject", """Thank you for reviewing"""
        )

        update_setting_default(
            "review_decision_decline",
            "email",
            """Dear {{ article.correspondence_author.full_name }},
<br><br>
We regret to inform you that "{{ article.safe_title }}"
has not been accepted [...] for publication in {{ article.journal.name }}.
<br><br>
To read the review, please go to the
<a href="{{ article.articleworkflow.url }}">{{ article.section.name }}'s web page</a>
<br><br>
Best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default("subject_review_decision_decline", "email_subject", """Rejected""")

        update_setting_default(
            "review_decision_accept",
            "email",
            """Dear {{ article.correspondence_author.full_name }},
<br><br>
We are pleased to inform you that your {{ article.section.name }} "{{ article.safe_title }}"
has been accepted [...] for publication in {{ article.journal.name }}.
<br><br>
To read the review, please go to the
<a href="{{ article.articleworkflow.url }}">{{ article.section.name }}'s web page</a>
<br><br>
You will be recontacted as soon as your manuscript has been typeset and is ready for proofreading.
<br>
If you wish to be notified about new publications, including your own,
please subscribe for alerts on
{{ article.journal.site_url }}
<br><br>
Best regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default("subject_review_decision_accept", "email_subject", """Accepted for publication""")

        update_setting_default(
            "request_revisions",
            "email",
            """{% load fqdn %}Dear Dr. {{ article.correspondence_author.full_name }},
<br><br>
Please connect to <a href="{{ article.articleworkflow.url }}">{{ article.section.name }}'s web page</a>
to read the review and [...]
submit the  requested {% if minor_revision %}minor{% endif %} revision by {{ revision.date_due }}.
<br><br>
In preparing it, please check that your manuscript conforms to the
<a href="{{ base_url }}/site/authors/#heading1">{{ article.journal.code }} style and formatting instructions</a>.
<br><br>
In particular, please check that references are formatted correctly and
that all references cited in the text are included in the reference list (and vice versa).
<br><br>
If you decide not to resubmit your manuscript, please withdraw it from the Journal as soon as possible
by using the appropriate link on your manuscript page.
<br><br>
Thank you and regards,
<br>
{{ journal.code }} Journal
""",
        )
        update_setting_default("subject_request_revisions", "email_subject", "Revision requested")

        update_setting_default(
            "typesetter_notification",
            "email",
            """Dear {{ typesetter.full_name }},
<br><br>
You have been assigned [...] the {{ article.section.name }} {{ article.id }}.

Please visit the <a href="{{ article.articleworkflow.url }}">{{ article.section.name }}'s web page</a>
""",
        )
        update_setting_default("subject_typesetter_notification", "email_subject", "Typesetter assigned")

        update_setting_default(
            "notify_typesetter_proofing_changes",
            "email",
            """Dear {{ typesetter.full_name }},
<br><br>
Author {{ article.correspondence_author.full_name }} has sent corrections [...]
for the {{ article.section.name }} {{ article.id }}.
""",
        )
        update_setting_default(
            "subject_notify_typesetter_proofing_changes",
            "email_subject",
            "Author proofread manuscript",
        )

        update_setting_default("typesetter_complete_notification", "email", "NOT USED IN WJS")
        update_setting_default("subject_typesetter_complete_notification", "email_subject", "NOT USED IN WJS")
        update_setting_default("typeset_ack", "email", "NOT USED IN WJS")
        update_setting_default("subject_typeset_ack", "email_subject", "NOT USED IN WJS")
        update_setting_default("typeset_reopened", "email", "NOT USED IN WJS")
        update_setting_default("subject_typeset_reopened", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_proofing_manager", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_proofing_manager", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_proofreader_complete", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_proofreader_complete", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_proofreader_assignment", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_proofreader_assignment", "email_subject", "NOT USED IN WJS")
        update_setting_default("thank_proofreaders_and_typesetters", "email", "NOT USED IN WJS")
        update_setting_default("subject_thank_proofreaders_and_typesetters", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_editor_proofing_complete", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_editor_proofing_complete", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_proofreader_cancelled", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_proofreader_cancelled", "email_subject", "NOT USED IN WJS")
        update_setting_default("typesetter_corrections_complete", "email", "NOT USED IN WJS")
        update_setting_default("subject_typesetter_corrections_complete", "email_subject", "NOT USED IN WJS")
        update_setting_default("copyedit_updated", "email", "NOT USED IN WJS")
        update_setting_default("subject_copyedit_updated", "email_subject", "NOT USED IN WJS")
        update_setting_default("copyedit_deleted", "email", "NOT USED IN WJS")
        update_setting_default("subject_copyedit_deleted", "email_subject", "NOT USED IN WJS")
        update_setting_default("typeset_deleted", "email", "NOT USED IN WJS")
        update_setting_default("subject_typeset_deleted", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_proofreader_edited", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_proofreader_edited", "email_subject", "NOT USED IN WJS")
        update_setting_default("notify_correction_cancelled", "email", "NOT USED IN WJS")
        update_setting_default("subject_notify_correction_cancelled", "email_subject", "NOT USED IN WJS")
        update_setting_default("author_copyedit_deleted", "email", "NOT USED IN WJS")
        update_setting_default("subject_author_copyedit_deleted", "email_subject", "NOT USED IN WJS")

        update_setting_default("production_complete", "email", "Production Complete")
        update_setting_default("subject_production_complete", "email_subject", "Production Complete")

        update_setting_default(
            "author_publication",
            "email",
            """Dear {{ article.correspondence_author.full_name }}
<br><br>
We are pleased to inform you that your manuscript, "{{ article.title }}",
is set for publication on {{ article.date_published|date:'Y-m-d' }}.
<br><br>
You may want to consider one or more of the following in order to promote your research:
<br>
<ul>
<li>Email a link to your paper to colleagues</li>
<li>Write a blog post about your paper</li>
<li>Upload it to your institutional repository or a subject repository.</li>
<li>Add to Wikipedia
    <a href="https://en.wikipedia.org/wiki/Wikipedia:Research_help/Scholars_and_experts">where appropriate</a></li>
</ul>
<br>
Regards,
<br>
{{ article.journal.code }} Journal
""",
        )
        update_setting_default("subject_author_publication", "email_subject", "Publication")


def update_setting_default(name, group, value, description=None):
    """Patch a setting's default value."""
    # TODO: refactor with wjs.jcom_profile.custom_settings_utils.patch_settin()
    setting = Setting.objects.get(name=name, group__name=group)
    if description:
        setting.description = description
        setting.save()
    setting_value = SettingValue.objects.get(setting=setting, journal__isnull=True)
    if overrides := SettingValue.objects.filter(setting=setting, journal__isnull=False):
        for override in overrides:
            logger.warning(f"Found override for {group}/{name} in {override.journal.code}")

    setting_value.value = value
    setting_value.save()
    logger.debug(f"Updated {group}/{name}")
