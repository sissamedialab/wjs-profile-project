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
from typing import Callable, Optional

from core.models import Account
from journal.models import Journal
from utils.logger import get_logger

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_wjapp.import_keywords import (
    get_or_create_import_keyword,
    keyword_subgroup_sql,
    merge_editor_keywords,
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


def read_editors_data(connection) -> list[dict]:
    """Read every wjapp user with an enabled editor role (role EDT, EB_MEMBER feature)."""
    cursor = connection.cursor(dictionary=True)
    cursor.execute(READ_EDITORS_QUERY)
    editors = cursor.fetchall()
    cursor.close()
    return editors


IS_ACTIVE_EDITOR_QUERY = """
SELECT 1
FROM User_Role ur
LEFT JOIN Role r USING (roleCod)
LEFT JOIN User_Feature uf USING (userCod)
LEFT JOIN Feature f USING (featureCod)
WHERE
    ur.userCod=%(editor_cod)s
    and r.roleId='EDT'
    and ur.disableFlag=0
    and f.featureId='EB_MEMBER'
"""


def is_active_wjapp_editor(connection, editor_cod) -> bool:
    """Whether wjapp still considers this user an enabled, general-pool editor (role EDT, EB_MEMBER).

    Same condition as ``READ_EDITORS_QUERY``, narrowed to one user, used to decide whether a
    historical ``editor_cod`` found in an article's assignment history should also be made a live
    automatic-assignment candidate when its WJS role is granted for the first time: a retired editor,
    or a special-issue guest editor, backfilled from an old paper must not silently re-enter the
    general automatic-assignment pool just because that paper got imported (wjs/specs#3493).
    """
    cursor = connection.cursor(dictionary=True)
    cursor.execute(IS_ACTIVE_EDITOR_QUERY, {"editor_cod": editor_cod})
    row = cursor.fetchone()
    cursor.close()
    return row is not None


def read_editor_keywords(connection, editor_cod, journal_code: str) -> list[dict]:
    """Read one editor's keyword/weight/group associations from wjapp.

    The single source of SQL for Editor_Keyword/Keyword, shared with
    ``EditorAssignmentAction.read_editor_parameters`` in import_logic.py (see wjs/specs#3477) rather
    than duplicated.
    """
    query = f"""
SELECT
ek.editorCod,
ek.keywordCod,
ek.keywordWeight,
kw.keywordName,
kw.keywordAvailable,
{keyword_subgroup_sql(journal_code, "kw.keywordCod")} AS subgroupName
FROM Editor_Keyword ek
LEFT JOIN Keyword kw USING (keywordCod)
WHERE editorCod=%(editor_cod)s
"""
    cursor = connection.cursor(dictionary=True)
    cursor.execute(query, {"editor_cod": editor_cod})
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


def sync_editor_workload_and_keywords(
    journal: Journal,
    editor: Account,
    editor_maxworkload,
    editor_keywords: list[dict],
    clear: bool = False,
    result: Optional[EditorSyncResult] = None,
    newly_created_editor: bool = False,
    editor_active_in_wjapp: bool = True,
) -> EditorSyncResult:
    """Get-or-create the editor's StaffWorkloadParameters, then sync workload and keywords from wjapp.

    Shared by ``sync_editor()`` (standalone editor importer) and
    ``EditorAssignmentAction.set_editor_parameters()`` (article importer, see import_logic.py), so an
    editor's parameters are synced identically regardless of which import path touches them first
    (wjs/specs#3493).

    By default only adds missing data (first-ever workload, a new keyword association) and never
    overwrites anything already there - an EO may have corrected an editor's workload or a keyword's
    weight from the WJS web pages since the last sync, and importing an older/stale wjapp snapshot
    (e.g. while backfilling old papers) must not reset that back to wjapp's own, possibly outdated,
    values. Pass `clear=True` to fully refresh from wjapp instead: overwrite the workload and replace
    the whole keyword set (used for an explicit, deliberate full resync, not plain import replay).

    `newly_created_editor` must be set by the caller when it just granted this editor their
    section-editor role: granting that role creates a default StaffWorkloadParameters row as a side
    effect (``create_staff_parameters_on_role`` in signals.py), so by the time we get here the row
    already exists and ``get_or_create`` below can never report it as newly created - we'd otherwise
    never set the workload from wjapp, even on a brand-new editor (wjs/specs#3493, gamboz note 84073).

    `editor_active_in_wjapp` (only consulted when `newly_created_editor` is true) says whether wjapp
    itself still considers this editor an enabled, general-pool editor right now: the role may have
    been granted purely to satisfy a historical assignment (a retired editor, or a special-issue guest
    editor, found in an old paper's history), which must not silently make them a live
    automatic-assignment candidate. Defaults to True since ``sync_editor()``'s own editors already come
    pre-filtered to wjapp-active ones.
    """
    if result is None:
        result = EditorSyncResult(editor=editor)
    if not editor_keywords:
        return result

    assignment_parameters, _ = wjs_models.StaffWorkloadParameters.objects.get_or_create(
        user=editor,
        journal=journal,
    )

    if newly_created_editor or clear:
        if not editor_maxworkload:
            editor_maxworkload = 1
            logger.warning(f"{journal.code} ST: Missing editor max workload for {editor}, forced to 1")
        if editor_maxworkload == 9999:
            logger.warning(f"Workload of {editor_maxworkload} found for {editor}. Verify WJS implementation!")

        assignment_parameters.workload = editor_maxworkload
        if newly_created_editor:
            assignment_parameters.enabled = editor_active_in_wjapp
        assignment_parameters.save()
        result.workload_set = True

    if clear:
        # delete all existing editor kwds and recreate from wjapp - only for an explicit full resync.
        wjs_models.StaffKeyword.objects.filter(parameters=assignment_parameters).delete()

    merged_keywords = merge_editor_keywords(journal.code, editor_keywords)
    for kwd_word, kwd_data in merged_keywords.items():
        keyword, created = get_or_create_import_keyword(
            journal, kwd_word, kwd_data["available"], kwd_data.get("group_name")
        )
        if created:
            logger.warning(f'Created keyword "{kwd_word}" for editor {editor}. Please check!')

        # An existing association is never overwritten: an EO may have corrected its weight since the last sync.
        _staff_keyword, staff_keyword_created = wjs_models.StaffKeyword.objects.get_or_create(
            parameters=assignment_parameters,
            keyword=keyword,
            defaults={"weight": kwd_data["weight"]},
        )
        if staff_keyword_created:
            result.keywords_added += 1
    logger.debug(
        f"synced {result.keywords_added}/{len(merged_keywords)} keyword(s) for editor {editor} on {journal.code}"
    )
    return result


def sync_editor(
    journal: Journal, connection, row: dict, clear: bool = False, get_account: Optional[Callable] = None
) -> EditorSyncResult:
    """Get-or-create the editor's Account/section-editor role, then sync workload and keywords.

    See ``sync_editor_workload_and_keywords()`` for the additive-by-default/``clear`` semantics.

    ``get_account`` resolves the editor's Account and defaults to
    ``import_logic.account_get_or_create_check_correspondence``. That module is imported only when needed because
    importing it monkeypatches ``wjs_review`` classes process-wide (see ``import_keywords``).
    """
    if get_account is None:
        from wjs.jcom_profile.import_wjapp.import_logic import (
            account_get_or_create_check_correspondence,
        )

        get_account = account_get_or_create_check_correspondence
    editor = get_account(
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

    editor_keywords = read_editor_keywords(connection, row["userCod"], journal.code)
    return sync_editor_workload_and_keywords(
        journal,
        editor,
        row.get("editorWorkload"),
        editor_keywords,
        clear=clear,
        result=result,
        newly_created_editor=result.role_added,
    )
