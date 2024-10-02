"""Generate documentation of all settings used in notifications."""

import csv
import dataclasses
from typing import List

from core.models import Setting, SettingValue
from django.core.management import call_command
from django.core.management.base import BaseCommand
from journal.models import Journal
from utils.logger import get_logger

from wjs.jcom_profile.custom_settings_utils import SettingsCSVWrapper

logger = get_logger(__name__)
jcom = Journal.objects.get(code="JCOM")


class Command(BaseCommand):
    """Generate documentation of all settings used in notifications."""

    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument(
            "--out-md",
            default="/tmp/wjs-settings.md",
            help="Output file name (markdown). Defaults to %(default)s.",
        )
        parser.add_argument(
            "--out-names-csv",
            default="/tmp/wjs-settings-names.csv",
            help="Output file name (csv). Defaults to %(default)s.",
        )

    def handle(self, *args, **options):
        self._open_names_csv(**options)
        self._open_md(**options)

        self._process_comment("# Settings related to notifications\n")
        self._process_comment(
            """Here follows a list of settings involved in users notifications. They usually come in pairs
(subject/body) or triplets (subject/body/default) for messages that have a part that is included and
that is not modifieable by the operator.

Some Janeway and WJS settings overlap. They are marked with a 🟡.
At the end of the page a section is dedicated to Janeway-only settings.

Ⓦ marks settings defined in WJS, Ⓙ if for Janeway settings.

🔵 marks settings worth knowing about.

🟠 marks discussion is needed.

◯ marks a placeholder, meaning that the setting is alredy displayed elsewhere

Please note that even if this list should include all settings that exists in Janeway, jcom-profile and wjs-review
plugin, it does not ensure that each process (e.g. ed-assigns-rev, au-submits-proofs, etc.) do use the correct
settings. A per-process vertical verification is needed.

""",
        )

        self._process_comment("\n## Submission\n")

        # conferma submission (any v.) al corr auth
        self._process("submission_acknowledgement")
        self._process("submission_coauthors_acknowledgement", extra="🟠 Wrong group from jcom-profile setting.")

        self._process_comment(
            """🟠 TBD! C’è una sola notifica per l’editor quando l’autore sottomette una revision. Può andare
bene per major e minor ma per la tech rev ce ne vorrebbe una diversa (per MT: suggerendo all’editor di
informare i reviewer, che non ricevono una notifica specifica)

Also, the same setting/notifica goes to both editor (for any revision submission) and to reviewers (only for technical
revision submissions). The "context" contains `revision` (which has `revision.type`), so the message can be different
per-type (NB cannot test manually because UI is unstable: form initial for tech_revisions seems wrong (and field is
hidden); see also #131 (comment 26561))
"""
        )
        self._process("revision_submission", extra="🟠 Messy! See above.")

        # notify submission after appeal to editor:
        self._process("author_submits_appeal")

        self._process("requeue_article")
        self._process("review_decision_requires_resubmission")

        self._process("eo_assignment")

        self._process_comment("\n## Assignment to editor\n")

        # notifica assegnazione automatica di new submission all’editor
        #     editor_assignment ⇨ wjs_editor_assignment_body
        self._process("editor_assignment", extra="🟡 Janeway - to be merged with `wjs_editor_assignment`")
        self._process("wjs_editor_assignment", extra="🟡 WJS - to be merged with `editor_assignment`")

        #     notifica di assegnazione all’editor da parte del guest editor
        # TODO!

        self._process("editor_decline_assignment")
        self._process("unassign_editor", extra="🟡 Janeway only - not used")

        self._process_comment("\n## Review\n")

        self._process("review_invitation_message")
        self._process("editor_deassign_reviewer")
        self._process("review_withdraw")
        self._process("review_withdrawl", extra="🟡 Janeway only - not used")
        self._process("do_review_message")
        self._process("due_date_postpone")
        self._process("due_date_far_future")
        self._process("wjs_editor_i_will_review_message")

        # notifica di reviewer declines assignment all’editor
        # notifica di reviewer accepts assignment, possibilmente una per l’editor/EO/dir
        self._process("reviewer_acknowledgement", extra="🟠 both for accept and decline")

        # e una per il reviewer stesso (Janeway ne ha una: review assignment acknowledgement)
        self._process("review_accept_acknowledgement")

        # notifica per editor: reviewer XY has uploaded review
        self._process("review_complete_acknowledgement")

        # notifica di “editor as reviewer” declines assignment
        self._process("editor-as-reviewer declines assignment")

        self._process_comment("\n## Editor decision\n")

        #     notifica di rejection all’autore
        self._process("review_decision_decline")
        #     notifica di acceptance all’autore
        self._process("review_decision_accept")

        self._process("review_decision_revision_request")
        self._process("technical_revision")
        self._process("review_decision_not_suitable")

        self._process("revision_request_date_due_postponed")
        self._process("revision_request_date_due_far_future")

        self._process_comment("\n## Production\n")

        self._process("typesetting_assignment")
        self._process("proofreading_request")
        self._process("author_sends_corrections")

        self._process("typesetting_generated_galleys")

        # eo sends back to typ (see also !511 (comment 24412))
        self._process("eo_send_back_to_typesetting")

        self._process_comment("\n## Withdraw (by EO/author)\n")

        self._process("author_withdraws_preprint")
        self._process("preprint_withdrawn")

        self._process_comment("\n## Appeals\n")
        # Appelli (apertura, assegnazione all’editor, withdraw)

        self._process("eo_opens_appeal")
        self._process("author_submits_appeal")

        # Solo per timeline:
        #     ready for pub by …
        #     published

        self._process_comment("\n## Other\n")

        self._process("hijack_notification")

        self._process_comment("\n## Publication alerts (newsletter)\n")

        self._process("publication_alert_subscription_email")
        self._process("publication_alert_reminder_email")
        self._process("publication_alert_email_intro_message")
        self._process("publication_alert_email")

        self._process_comment("\n## Janeway only 🟡 probably to be merged with ours\n")

        self._process_comment("\n### Review (loosely)\n")

        self._process("editor_assignment", placeholder=True)
        self._process("submission_acknowledgement", placeholder=True)
        self._process("reviewer_acknowledgement", placeholder=True)
        self._process("review_request_sent")
        self._process("review_accept_acknowledgement", placeholder=True)
        self._process("review_complete_reviewer_acknowledgement")
        self._process("review_complete_acknowledgement", placeholder=True)
        self._process("review_decline_acknowledgement")
        self._process("review_assignment")
        self._process("default_review_reminder")
        self._process("accepted_review_reminder")
        self._process("review_decision_accept", placeholder=True)
        self._process("review_decision_decline", placeholder=True)
        self._process("review_decision_undecline")
        self._process("request_revisions")
        self._process("unassign_editor")
        self._process("review_withdrawl")
        self._process("notify_se_draft_declined")
        self._process("revisions_complete_receipt", extra="🔵")
        self._process("submission_access_request_notification")
        self._process("submission_access_request_complete")
        self._process("share_reviews_notification")
        self._process("revisions_complete_editor_notification")
        self._process("draft_message")
        self._process("draft_editor_message")
        self._process("editor_new_submission", extra="🔵")

        self._process_comment("\n### Other\n")

        self._process("password_reset", extra="🔵")
        self._process("new_user_registration", extra="🔵")
        self._process("user_email_change", extra="🔵")

        self._process(
            "reader_publication_notification",
            extra="🔵 Similar to our newsletter. Only one of these two systems must be kept active.",
        )

        self._process("bounced_email_notification", extra="🔵")

        self._process_comment("\n#### Digests\n")
        self._process("peer_reviewer_pub_notification")
        self._process("editor_digest")
        self._process("reviewer_digest")
        self._process("revision_digest")
        self._process("production_assign_article")
        self._process("notification_submission")
        self._process("notification_acceptance")

        self._process_comment("\n### Production - to be ignore\n")
        self._process_comment(
            """
        Janeway's default production is so different from ours that these settings can probably be
        ignored, even if there is still a change to confuse some of them with ours.
        """
        )

        self._process("copyeditor_assignment_notification")
        self._process("copyeditor_notify_editor")
        self._process("copyeditor_notify_author")
        self._process("copyeditor_reopen_task")
        self._process("author_copyedit_complete")
        self._process("production_manager_notification")
        self._process("typesetter_notification")
        self._process("typesetter_complete_notification")
        self._process("typeset_ack")
        self._process("production_complete")
        self._process("typeset_reopened")
        self._process("notify_proofing_manager")
        self._process("notify_proofreader_complete")
        self._process("notify_proofreader_assignment")
        self._process("notify_typesetter_proofing_changes")
        self._process("thank_proofreaders_and_typesetters")
        self._process("notify_editor_proofing_complete")
        self._process("notify_proofreader_cancelled")
        self._process("typesetter_corrections_complete")
        self._process("author_publication")
        self._process("copyedit_updated")
        self._process("copyedit_deleted")
        self._process("typeset_deleted")
        self._process("notify_proofreader_edited")
        self._process("notify_correction_cancelled")
        self._process("author_copyedit_deleted")

        self._process_comment("")

        self._close(**options)

    def _close(self, **options):
        self.out_md.close()
        self._close_names_csv(**options)

    def _close_names_csv(self, **options):
        self.out_names_csv.close()
        application = "wjs"
        call_command("export_settings", f"--settings_list_csv={options['out_names_csv']}", application, "JCOM")
        self.stdout.write(f"Settings names exported into {options['out_names_csv']}")
        self.stdout.write(f"Settings (csv) exported into settings_{application}.csv")

    def _open_md(self, **options):
        self.out_md = open(options["out_md"], "w")
        self.stdout.write(f"Settings (markdown) exported into {options['out_md']}")

    def _open_names_csv(self, **options):
        """Open a file where we write all names of settings.

        It will later be used as source for `export_settings`.
        """
        self.out_names_csv = open(options["out_names_csv"], "w")
        self.out_names_csvwriter = csv.DictWriter(self.out_names_csv, fieldnames=SettingsCSVWrapper.names_fields)
        self.out_names_csvwriter.writeheader()

    def _process(self, stem: str, extra: str = None, placeholder: bool = False):
        """Output the given setting."""
        settings_descs = setting_desc(stem)
        if not settings_descs:
            self.out_md.write(f"- 🔴 `{stem}` not found")
        else:
            for sd in settings_descs:
                self.out_md.write(sd.to_md(extra, placeholder))
                self.out_names_csvwriter.writerow(
                    {
                        "name": sd.name,
                        "group": sd.group,
                    },
                )

    def _process_comment(self, comment: str):
        """Output the given comment."""
        self.out_md.write(comment)


# TODO: inlclude in pandoc's tex the following and use it to get gray background for verbatim text:
# \usepackage{fancyvrb,newverbs,xcolor}

# \definecolor{cverbbg}{gray}{0.93}

# \newenvironment{cverbatim}
#  {\SaveVerbatim{cverb}}
#  {\endSaveVerbatim
#   \flushleft\fboxrule=0pt\fboxsep=.5em\footnotesize
#   \colorbox{cverbbg}{\BUseVerbatim{cverb}}%
#   \endflushleft
# }


@dataclasses.dataclass
class SettingDesc:
    """A description of a setting suitable for output."""

    name: str
    group: str
    doc: str
    value: str

    def to_md(self, extra: str = None, placeholder: bool = False) -> str:
        """Return md reprsentation."""
        if placeholder:
            return f"- ◯ `{self.name}`\n"
        else:
            value_lines = self.value.split("\n")
            indented_lines = "\n".join([f"    {l}" for l in value_lines])
            return f"""- `{self.name}` {"Ⓦ" if self.group=="wjs_review" else "Ⓙ"} {extra or ""}
  - {self.doc}
  - ```
{indented_lines}
    ```
"""


def setting_desc(stem: str) -> List[SettingDesc]:
    """Return a description of the setting.

    "Stem" means the initial part of the setting; "body", "default", "subject" and "notice" will be added at the end and
    tried, and all that is found will be returned.

    """
    results = []
    for group in ("email", "email_subject", "wjs_review"):
        for suffix in (None, "subject", "body", "default", "notice"):
            if group == "email_subject" and suffix == "subject":
                # we place suffix after the stem, but janeway has it (the string "subject") in front
                name = f"{suffix}_{stem}"
            elif suffix is None:
                # allow for janeway settings that are in the form "name/subject_name"
                name = stem
            else:
                name = f"{stem}_{suffix}"

            if sd := get_setting_and_value(name, group):
                results.append(sd)

    return results


def get_setting_and_value(name: str, group: str):
    """Nomen omen."""
    setting = None
    value = None
    try:
        setting = Setting.objects.get(
            name=name,
            group__name=group,
        )
    except Setting.DoesNotExist:
        return None

    # logger.debug(f"{setting=}")

    # We expect no override for JCOM: only default values
    try:
        value = SettingValue.objects.get(setting=setting, journal=jcom)
    except SettingValue.DoesNotExist:
        value = SettingValue.objects.get(setting=setting, journal__isnull=True)
    else:
        logger.warning(f"Found an override of {setting} for {jcom.code}")

    return SettingDesc(
        name=name,
        group=group,
        doc=setting.description,
        value=value.processed_value,
    )
