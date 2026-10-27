"""Shared logic to import wjapp editors' role/workload/keyword associations into WJS.

Reused by the import_editor_keywords_from_wjapp management command. Kept here, alongside
import_logic.py/import_file_manager.py, rather than in management/commands/, following this
package's existing split between shared import logic and the thin command entry points that
invoke it.

This is deliberately independent of the article importer (import_articles_from_wjapp.py), which
only sets StaffWorkloadParameters/StaffKeyword as a side effect of replaying editor-assignment
actions (SYS_ASS_ED/ED_SEL_N_ED/ADMIN_ASS_N_ED) found in an article's history - see
EditorAssignmentAction.read_editor_parameters()/set_editor_parameters() in import_logic.py. That
means an editor whose only assignments are on articles that haven't been (successfully) imported
yet never gets their keyword interests synced. Running this standalone - before, after or
independently of the article importer - covers every editor wjapp knows about instead.
"""

from dataclasses import dataclass

from core.models import Account
from journal.models import Journal
from submission import models as submission_models
from utils.logger import get_logger

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_wjapp.import_logic import (
    account_get_or_create_check_correspondence,
    normalize_keyword,
)

logger = get_logger(__name__)

READ_EDITORS_QUERY = """
SELECT
u.userCod,
u.lastname,
u.firstname,
u.email,
u.privacy,
u.editorWorkload
FROM User u
LEFT JOIN User_Role ur USING (userCod)
LEFT JOIN Role r USING (roleCod)
LEFT JOIN User_Feature uf USING (userCod)
LEFT JOIN Feature f USING (featureCod)
WHERE
    r.roleId='EDT'
    and ur.disableFlag=0
    and f.featureId='EB_MEMBER'
"""

READ_EDITOR_KEYWORDS_QUERY = """
SELECT
ek.editorCod,
ek.keywordCod,
ek.keywordWeight,
kw.keywordName
FROM Editor_Keyword ek
LEFT JOIN Keyword kw USING (keywordCod)
WHERE editorCod=%(editor_cod)s
"""


def read_editors_data(connection) -> list[dict]:
    """Read every wjapp user with an enabled editor role (role EDT, EB_MEMBER feature)."""
    cursor = connection.cursor(dictionary=True)
    cursor.execute(READ_EDITORS_QUERY)
    editors = cursor.fetchall()
    cursor.close()
    return editors


def read_editor_keywords(connection, editor_cod) -> list[dict]:
    """Read one editor's keyword/weight associations from wjapp."""
    cursor = connection.cursor(dictionary=True)
    cursor.execute(READ_EDITOR_KEYWORDS_QUERY, {"editor_cod": editor_cod})
    keywords = cursor.fetchall()
    cursor.close()
    return keywords


@dataclass
class EditorSyncResult:
    """What actually changed for one editor, for the command to report back."""

    editor: Account
    role_added: bool = False
    workload_set: bool = False
    keywords_added: int = 0

    @property
    def changed(self) -> bool:
        """Whether anything at all was added for this editor."""
        return self.role_added or self.workload_set or self.keywords_added > 0


def sync_editor(journal: Journal, connection, row: dict, clear: bool = False) -> EditorSyncResult:
    """Get-or-create the editor's Account/section-editor role, then sync workload and keywords.

    By default only adds missing data (a new editor, a new keyword association) and never
    overwrites anything already there - an EO may have corrected an editor's workload from the
    WJS web pages since the last sync. Pass `clear=True` to fully refresh from wjapp instead:
    overwrite the workload and replace the whole keyword set.
    """
    editor = account_get_or_create_check_correspondence(
        journal.code.lower(),
        row["userCod"],
        row["lastname"],
        row["firstname"],
        row["email"],
        row["privacy"],
        connection,
    )
    result = EditorSyncResult(editor=editor)
    if not editor.check_role(journal, "section-editor", staff_override=False):
        editor.add_account_role("section-editor", journal)
        result.role_added = True
        logger.debug(f"added missing section-editor role for {editor}")

    editor_keywords = read_editor_keywords(connection, row["userCod"])
    if not editor_keywords:
        return result

    assignment_parameters, parameters_created = wjs_models.StaffWorkloadParameters.objects.get_or_create(
        user=editor,
        journal=journal,
    )

    if parameters_created or clear:
        editor_maxworkload = row.get("editorWorkload")
        if not editor_maxworkload:
            editor_maxworkload = 1
            logger.warning(f"{journal.code} ST: Missing editor max workload for {editor}, forced to 1")
        if editor_maxworkload == 9999:
            logger.warning(f"Workload of {editor_maxworkload} found for {editor}. Verify WJS implementation!")

        assignment_parameters.workload = editor_maxworkload
        assignment_parameters.save()
        result.workload_set = True

    if clear:
        # delete all existing editor kwds and recreate from wjapp - same idempotent replace
        # pattern as EditorAssignmentAction.set_editor_parameters() in import_logic.py
        wjs_models.StaffKeyword.objects.filter(parameters=assignment_parameters).delete()

    for ek in editor_keywords:
        kwd_word = normalize_keyword(journal.code, ek["keywordName"].strip())
        keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_word)
        if created:
            logger.warning(f'Created keyword "{kwd_word}" for editor {editor}. Please check!')

        # Not a plain get_or_create(): pre-existing duplicate StaffKeyword rows for the same
        # (parameters, keyword) are a known, real possibility from before any dedup logic
        # existed (the old delete-then-recreate path never cared). get_or_create()'s internal
        # get() would raise MultipleObjectsReturned on those - count/create instead, and only
        # flag duplicates for manual review rather than silently deduplicating them here.
        existing = wjs_models.StaffKeyword.objects.filter(parameters=assignment_parameters, keyword=keyword)
        existing_count = existing.count()
        if existing_count == 0:
            wjs_models.StaffKeyword.objects.create(
                parameters=assignment_parameters,
                keyword=keyword,
                weight=ek["keywordWeight"],
            )
            result.keywords_added += 1
        elif existing_count > 1:
            logger.warning(
                f'Found {existing_count} duplicate StaffKeyword rows for keyword "{kwd_word}" and '
                f"editor {editor} on {journal.code} - left as-is, not deduplicated automatically"
            )
    logger.debug(
        f"synced {result.keywords_added}/{len(editor_keywords)} keyword(s) for editor {editor} on {journal.code}"
    )
    return result
