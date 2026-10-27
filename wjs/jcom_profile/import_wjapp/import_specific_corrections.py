"""Per-preprintid specific corrections to wjapp import history.

Kept separate from import_articles_from_wjapp.py because this single function accumulates one
`if preprintid == "..." and imported_version_num == N:` block per known-bad article, growing with
every journal imported (JCAP, then JINST/JSTAT/JHEP...). Isolating it here keeps the management
command itself focused on the import mechanics.
"""

import datetime

import wjs.jcom_profile.import_wjapp.import_logic as import_logic


def _require_eo_in_charge_identity(eo_in_charge_identity, preprintid, imported_version_num):
    """Fail clearly if the article has no eo_in_charge, instead of a bare TypeError on `**None`."""
    if eo_in_charge_identity is None:
        raise ValueError(
            f"{preprintid}/{imported_version_num}: article has no eo_in_charge, "
            "cannot attribute a synthetic ADMIN_CONFIRMS_TA to anyone"
        )
    return eo_in_charge_identity


def repair_history_specific_article(history, preprintid, imported_version_num, eo_in_charge_identity):
    """Apply correction to the history of a specific article version."""

    for a in history:
        import_logic.logger.warning(
            f"original history specific article correction {preprintid}/{imported_version_num}: {a['actionID']}"
        )

    # JCAP_003P_1104/2 change to ADMIN_ASS_N_ED
    if preprintid == "JCAP_003P_1104" and imported_version_num == 2:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" " to ADMIN_ASS_N_ED"
        )
        history[0]["actionID"] = "ADMIN_ASS_N_ED"

    # ADMIN_RESETS_ED (no handler, silently skipped) right after ADMIN_ACC_APP: ADMIN_ACC_APP's
    # OpenAppeal/HandleDecision moves the workflow to UNDER_APPEAL, which requires an
    # author-driven transition (author_submits_appeal(), events/handlers.py:107-110) before
    # ED_ASS_REF's AssignToReviewer can pass its EDITOR_SELECTED state check. wjapp doesn't
    # log an explicit author action here, but per the message thread the author didn't
    # resubmit - they confirmed the existing version and contested the rejection, which is
    # exactly wjapp's "author confirms version" (appeal) action (AU_CONF_VER ->
    # AuthorSubmitRevisionAction, same handler as AU_SUB_NEW_VER, which *is* present and
    # working for these same articles' other appeal cycles). Relabeling (not deleting) this
    # entry supplies that missing transition.
    if preprintid == "JCAP_001P_0105" and imported_version_num == 3:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" " to AU_CONF_VER"
        )
        history[0]["actionID"] = "AU_CONF_VER"  # changed from ADMIN_RESETS_ED

    if preprintid == "JCAP_015P_0405" and imported_version_num == 2:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" " to AU_CONF_VER"
        )
        history[0]["actionID"] = "AU_CONF_VER"  # changed from ADMIN_RESETS_ED

    # Different scenario from the two cases above: no ADMIN_ACC_APP/appeal here. Editor
    # requests a major revision (ED_REQ_REV, -> state TO_BE_REVISED), then is reset by the EO
    # (ADMIN_RESETS_ED, no handler - pure no-op either way) before the author acts on that
    # revision request, and a completely different editor is assigned next (ADMIN_ASS_N_ED) -
    # confirmed on the wjapp UI: the new editor goes on to issue their own, independent major
    # revision decision later. AssignToEditor only accepts EDITOR_TO_BE_SELECTED/
    # EDITOR_SELECTED/REJECTED, not TO_BE_REVISED, so the real ADMIN_ASS_N_ED fails regardless
    # of ADMIN_RESETS_ED's presence. Drop both: the old editor's revision request was voided by
    # the reset before it had any effect, so state should stay at EDITOR_SELECTED for the new
    # editor's assignment to proceed via editor_assign_different_editor's self-loop.
    if preprintid == "JCAP_019P_0806" and imported_version_num == 1:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: "
            f"{history[7]['actionID']} {history[6]['actionID']}"
        )
        del history[7]  # remove ADMIN_RESETS_ED
        del history[6]  # remove voided ED_REQ_REV

    if preprintid == "JCAP_074P_0621" and imported_version_num == 1:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: "
            f"{history[17]['actionID']} {history[16]['actionID']}"
        )
        del history[17]  # remove ADMIN_RESETS_ED
        del history[16]  # remove voided ED_REQ_REV

    # JCAP_049P_1115/4
    if preprintid == "JCAP_049P_1115" and imported_version_num == 4:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}"
            f" {history[3]['actionID']}  {history[4]['actionID']}"
        )
        del history[4]
        del history[3]
        del history[2]

    # JCAP_047P_0617/2 kept second author sends correction action
    if preprintid == "JCAP_047P_0617" and imported_version_num == 2:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[3]['actionID']}"
            f" {history[4]['actionID']}  {history[5]['actionID']}"
        )
        del history[5]
        del history[4]
        del history[3]

    # JCAP_002P_1021/4  change to PM_SENDS_FOR_PROOF_R
    if preprintid == "JCAP_002P_1021" and imported_version_num == 4:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: {history[1]['actionID']}" " to PM_SENDS_FOR_PROOF_R"
        )
        history[1]["actionID"] = "PM_SENDS_FOR_PROOF_R"

    # JCAP_002P_0207 add a missing action at the end
    if preprintid == "JCAP_002P_0207" and imported_version_num == 1:
        fmt = "%Y%m%d%H%M"
        action_dt = datetime.datetime.strptime("200704301241", fmt)
        history.append(
            {
                "actionID": "ED_ACC_DOC_WMC",
                "agentCod": 266,
                "actionDate": action_dt,
                "userCod": 810,
                "actionCod": 8,
                "versionCod": 3647,
                "actHistCod": 0,
            }
        )
        import_logic.logger.warning(f"append to the history: {history[-1]}")

    # JCAP_010P_1124 add missing actions ED_ADD_REF and REF_ACC for referee 15169
    # (a different person from referee 3671, despite a similarly-spelled name).
    # wjapp Current_Referees has assignDate=2024-12-10 13:03:58 and
    # acceptDate=2024-12-10 14:05:42 for refereeCod=15169/versionCod=71242/editorCod=1184,
    # but Action_History is missing the corresponding rows for 15169.
    if preprintid == "JCAP_010P_1124" and imported_version_num == 1:
        fmt = "%Y%m%d%H%M%S"
        action_ed_add_ref_dt = datetime.datetime.strptime("20241210130358", fmt)
        action_ed_add_ref = {
            "actionID": "ED_ADD_REF",
            "agentCod": 1184,
            "actionDate": action_ed_add_ref_dt,
            "targetCod": 15169,
            "actionCod": 15,
            "versionCod": 71242,
            "actHistCod": 0,
        }
        action_ref_acc_dt = datetime.datetime.strptime("20241210140542", fmt)
        action_ref_acc = {
            "actionID": "REF_ACC",
            "agentCod": 15169,
            "actionDate": action_ref_acc_dt,
            "actionCod": 14,
            "versionCod": 71242,
            "actHistCod": 0,
        }
        # insert right before REF_SENDS_REP (actHistCod=417003, 2024-12-31 05:00:30)
        insert_index = next(i for i, a in enumerate(history) if a["actHistCod"] == 417003)
        history.insert(insert_index, action_ed_add_ref)
        history.insert(insert_index + 1, action_ref_acc)
        import_logic.logger.warning(
            f"insert into history: {preprintid}/{imported_version_num}: "
            "added ED_ADD_REF and REF_ACC for referee 15169 (missing in wjapp source)"
        )

    # JCAP_080P_0620 remove ed declines assihnment without user
    if preprintid == "JCAP_080P_0620" and imported_version_num == 1:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[3]['actionID']}")
        del history[3]  # remove in place

    # JCAP_046P_1025 remove ready for pub
    if preprintid == "JCAP_046P_1025" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove in place

    # JCAP_086P_0725
    if preprintid == "JCAP_086P_0725":
        if imported_version_num == 5:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[0]['actionID']}"
                f" {history[1]['actionID']}"
            )
            del history[1]  # PM_SENDS_TO_TYP
            del history[0]  # PUM_SENDS_TO_EO

        elif imported_version_num == 4:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}"
                f" {history[2]['actionID']}"
            )
            del history[2]  # PM_PUBLISHES
            del history[1]  # PM_SENDS_FOR_PROOF_R

    # JCAP_059P_0820
    if preprintid == "JCAP_059P_0820":
        if imported_version_num == 5:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[3]['actionID']}"
            )
            del history[3]  # ready for publication.

        elif imported_version_num == 6:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[0]['actionID']}"
                f" {history[2]['actionID']}"
            )
            del history[2]  # EO back to typesetter
            del history[0]  # PUBMAN sends back to EO.

    # JCAP_114P_1024 PUM_SENDS_TO_EO PM_SENDS_TO_TYP
    if preprintid == "JCAP_114P_1024":
        if imported_version_num == 5:
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 4:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[3]['actionID']}"
                f" {history[3]['actionID']}"
            )
            del history[3]  # remove in place PM_PUBLISHES

    # JCAP_032P_0326 - PM_REQ_ED_CHECK ED_APPROVES
    # removed 1  action
    if preprintid == "JCAP_032P_0326" and imported_version_num == 4:
        import_logic.logger.warning(
            f"del actions from history: {preprintid}/{imported_version_num}"
            f" {history[4]['actionID']} {history[3]['actionID']}"
        )
        del history[3]  # remove in place PM_REQ_ED_CHECK

    # Same PM_REQ_ED_CHECK ED_APPROVES pair as JCAP_032P_0326 above - ED_APPROVES.run() is a
    # confirmed no-op (import_logic.py), so only PM_REQ_ED_CHECK needs dropping. Not covered
    # by the general remove_ed_check_ed_approves_before_ready_for_pub() correction below,
    # which only matches when PM_PUBLISHES is the version's literal last action - these 7
    # don't have that shape. Kept as individual cases rather than widening that general
    # correction, given the scale of the eventual full ~21000-paper import.
    if preprintid == "JCAP_022P_0306" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_034P_0906" and imported_version_num == 5:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_065P_1112" and imported_version_num == 5:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_071P_0516" and imported_version_num == 5:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_021P_0720" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[3]['actionID']}")
        del history[3]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_124P_0720" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_017P_0920" and imported_version_num == 6:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    # JCAP_069P_1225/7 was originally caught live, mid-cycle (PM_REQ_ED_CHECK as the then-last
    # recorded action, no editor resolution yet) and handled with a temporary del history[-1].
    # A later reimport shows the real resolution: ED_APPROVES (no-op), then PM_SENDS_TO_TYP -
    # the same ED_APPROVES pattern as the 7 cases above. Replaces the old trailing-delete, which
    # would now remove the wrong (real, final) action since more history was appended since.
    if preprintid == "JCAP_069P_1225" and imported_version_num == 7:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    # Same PM_REQ_ED_CHECK ED_APPROVES pair as the 7 cases above, just with one or more
    # ADMIN_REMINDS_ED entries logged in between - still resolves to ED_APPROVES (no-op),
    # never ED_RESTARTS_REV, so the same simple single-delete fix applies
    if preprintid == "JCAP_006P_0713" and imported_version_num == 5:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_027P_0416" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[1]['actionID']}")
        del history[1]  # remove in place PM_REQ_ED_CHECK

    if preprintid == "JCAP_075P_0619" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[3]['actionID']}")
        del history[3]  # remove in place PM_REQ_ED_CHECK

    # JCAP_113P_0224 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # removed all the actions of 6 versions
    if preprintid == "JCAP_113P_0224":
        if imported_version_num == 1:
            import_logic.logger.warning(
                f"del actions from history: {preprintid}/{imported_version_num}"
                f" {history[14]['actionID']} {history[13]['actionID']}"
                f" {history[12]['actionID']} "
                f" change history: {preprintid}/{imported_version_num}: {history[11]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[14]  # remove in place TYP_TAKES_CHARGE
            del history[13]  # remove in place ADMIN_REJECTS_TA
            del history[12]  # remove in place ADM_SETS_PUB_SETINGS
            history[11]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (2, 3, 4, 5, 6, 7, 8):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 9:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_036P_0625 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # removed all the actions of 4 versions
    if preprintid == "JCAP_036P_0625":
        if imported_version_num == 4:
            import_logic.logger.warning(
                f"del actions from history: {preprintid}/{imported_version_num}"
                f" {history[7]['actionID']} {history[6]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[7]  # remove in place TYP_TAKES_CHARGE
            del history[6]  # remove in place ADMIN_CONFIRMS_TA
            history[5]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (5, 6, 7, 8):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 9:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_132P_0625 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    if preprintid == "JCAP_132P_0625":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: "
                f" {history[6]['actionID']} {history[4]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[4]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[6]  # remove in place TYP_TAKES_CHARGE
            del history[5]  # remove in place ADMIN_CONFIRMS_TA
            history[4]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num == 3:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}"
                f" {history[1]['actionID']} {history[0]['actionID']}"
            )
            del history[2]  # remove in place ED_RESTARTS_REV
            del history[1]  # remove in place PM_REQ_ED_CHECK
            del history[0]  # remove in place TYP_UPLOADS_FOR_PM

        elif imported_version_num == 4:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_033P_0307 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    if preprintid == "JCAP_033P_0307":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[4]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[5]  # remove in place TYP_TAKES_CHARGE
            history[4]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num == 3:
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 4:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_003P_1008 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    if preprintid == "JCAP_003P_1008":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[6]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[6]  # remove in place TYP_TAKES_CHARGE
            history[5]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num == 3:
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 4:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_026P_0110 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    if preprintid == "JCAP_026P_0110":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[6]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[6]  # remove in place TYP_TAKES_CHARGE
            history[5]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num == 3:
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 4:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_064P_1112 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # here the erroneous production phase spans 3 whole versions (v2-v4), not just 1
    if preprintid == "JCAP_064P_1112":
        if imported_version_num == 1:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[6]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[6]  # remove in place TYP_TAKES_CHARGE
            history[5]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (2, 3, 4):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 5:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_070P_0514 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    if preprintid == "JCAP_070P_0514":
        if imported_version_num == 1:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[13]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[12]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[13]  # remove in place TYP_TAKES_CHARGE
            history[12]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num == 2:
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 3:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_014P_0714 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # v1 already ends ED_ACC_DOC_WMC (legit minor revision, no fix needed there); the
    # erroneous production phase spans 5 whole versions (v3-v7), reaching PM_PUBLISHES
    # in v6 before being sent back to the EO and reopened in v7
    if preprintid == "JCAP_014P_0714":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[4]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[5]  # remove in place TYP_TAKES_CHARGE
            history[4]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (3, 4, 5, 6, 7):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 8:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_017P_1114 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    if preprintid == "JCAP_017P_1114":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[6]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[6]  # remove in place TYP_TAKES_CHARGE
            history[5]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (3, 4, 5, 6):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 7:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_036P_0523 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # v5 legitimately continues to a new ED_REQ_REV (not accept); v6 continues to
    # ED_REJ_DOC (paper ultimately rejected) - both fine as-is, no further correction
    if preprintid == "JCAP_036P_0523":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[5]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[4]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[5]  # remove in place TYP_TAKES_CHARGE
            history[4]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (3, 4):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 5:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_041P_0217 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # v1,v2,v3 are 3 genuine minor-revision (ED_ACC_DOC_WMC) rounds before the real accept;
    # v8 legitimately continues to a new referee round, ending ED_REJ_DOC (rejected)
    if preprintid == "JCAP_041P_0217":
        if imported_version_num == 4:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[3]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[2]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[3]  # remove in place TYP_TAKES_CHARGE
            history[2]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (5, 6, 7):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 8:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_053P_0723 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # same ADMIN_CONFIRMS_TA-before-take-charge variant as the JCAP_113P_0224/etc precedents;
    # v6 legitimately continues to a new referee round + real 2nd accept+TA+take-charge
    if preprintid == "JCAP_053P_0723":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[12]['actionID']}"
                f" {history[11]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[10]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[12]  # remove in place TYP_TAKES_CHARGE
            del history[11]  # remove in place ADMIN_CONFIRMS_TA
            history[10]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (3, 4, 5):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 6:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_053P_1223 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # v1,v2 are 2 genuine major-revision (ED_REQ_REV) rounds before the real accept;
    # only v4 is erroneous this time (reopens directly, no intervening production versions)
    if preprintid == "JCAP_053P_1223":
        if imported_version_num == 3:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[9]['actionID']}"
                f" {history[8]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[7]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[9]  # remove in place TYP_TAKES_CHARGE
            del history[8]  # remove in place ADMIN_CONFIRMS_TA
            history[7]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num == 4:
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 5:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_082P_0720 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # same ADM_SETS_PUB_SETINGS-before-take-charge variant as the JCAP_113P_0224 precedent
    if preprintid == "JCAP_082P_0720":
        if imported_version_num == 2:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[13]['actionID']}"
                f" {history[12]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[11]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[13]  # remove in place TYP_TAKES_CHARGE
            del history[12]  # remove in place ADM_SETS_PUB_SETINGS
            history[11]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (3, 4):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 5:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_117P_0720 - PM_REQ_ED_CHECK ED_RESTARTS_REV
    # v3 reaches PM_PUBLISHES before v4 reopens via PUM_SENDS_TO_EO
    if preprintid == "JCAP_117P_0720":
        if imported_version_num == 1:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: {history[13]['actionID']}"
                f" change history: {preprintid}/{imported_version_num}: {history[12]['actionID']}"
                " to ED_ACC_DOC_WMC"
            )
            del history[13]  # remove in place TYP_TAKES_CHARGE
            history[12]["actionID"] = "ED_ACC_DOC_WMC"

        elif imported_version_num in (2, 3, 4):
            import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
            del history[:]

        elif imported_version_num == 5:
            import_logic.logger.warning(
                f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}" "to AU_SUB_REV"
            )
            history[0]["actionID"] = "AU_SUB_REV"  # changed from AU_SUB_REV_ED_CH

    # JCAP_063P_0925- PM_REQ_ED_CHECK last action
    if preprintid == "JCAP_063P_0925":
        if imported_version_num == 12:
            import_logic.logger.warning(
                f"del from history: {preprintid}/{imported_version_num}: " f" {history[1]['actionID']}"
            )
            del history[1]  # remove in place PM_REQ_ED_CHECK

    # JCAP_029P_0219 JCAP_040P_0623
    if (preprintid == "JCAP_029P_0219" and imported_version_num == 7) or (
        preprintid == "JCAP_040P_0623" and imported_version_num == 5
    ):
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[-3]['actionID']}"
        )
        del history[2:]  # remove in place all the elements but the first 2

    # TBV: the logic does not allow ADMIN_WITHR_APP without confirm version
    # removed open appeal and withdrawn appeal
    if (
        preprintid
        in (
            "JCAP_119P_0925",
            "JCAP_142P_0625",
            "JCAP_143P_0624",
            "JCAP_025P_0909",
            "JCAP_001P_0217",
            "JCAP_021P_0923",
            "JCAP_023P_1116",
            "JCAP_038P_1010",
            "JCAP_042P_0611",
            "JCAP_051P_0222",
            "JCAP_054P_0311",
            "JCAP_058P_0615",
            "JCAP_060P_0413",
            "JCAP_067P_0817",
            "JCAP_071P_0115",
            "JCAP_077P_0520",
            "JCAP_079P_0622",
            "JCAP_087P_0321",
            "JCAP_087P_0415",
        )
        and imported_version_num == 1
    ):
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[-1]['actionID']} "
            f"{history[-3]['actionID']}"
        )
        del history[-1]  # remove last action ADMIN_WITHR_APP
        # action SYS_REMINDS_AUT - system reminds author not removed
        del history[-2]  # remove last action ADMIN_ACC_APP

    # TBV: the logic does not allow ADMIN_WITHR_APP without confirm version
    # removed open appeal and withdrawn appeal
    if (
        preprintid
        in (
            "JCAP_012P_0311",
            "JCAP_013P_0117",
            "JCAP_014P_1016",
            "JCAP_021P_0616",
            "JCAP_023P_0415",
            "JCAP_045P_0114",
        )
        and imported_version_num == 2
    ):
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[-1]['actionID']} "
            f"{history[-3]['actionID']}"
        )
        del history[-1]  # remove last action ADMIN_WITHR_APP
        # action SYS_REMINDS_AUT - system reminds author not removed
        del history[-2]  # remove last action ADMIN_ACC_APP

    # TBV: the logic does not allow ADMIN_WITHR_APP without confirm version
    # removed open appeal and withdrawn appeal
    if (
        preprintid
        in (
            "JCAP_051P_1124",
            "JCAP_014P_1206",
            "JCAP_030P_1206",
            "JCAP_035P_1108",
            "JCAP_016P_0509",
            "JCAP_015P_0916",
            "JCAP_017P_1017",
            "JCAP_018P_1017",
            "JCAP_030P_0815",
            "JCAP_049P_0317",
            "JCAP_078P_0318",
        )
        and imported_version_num == 1
    ):
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[-1]['actionID']} "
            f"{history[-2]['actionID']}"
        )
        del history[-1]  # remove last action ADMIN_WITHR_APP
        del history[-1]  # remove last action ADMIN_ACC_APP

    # TBV: the logic does not allow ADMIN_WITHR_APP without confirm version
    # removed open appeal and withdrawn appeal
    if preprintid == "JCAP_024P_0207" and imported_version_num == 2:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[-1]['actionID']} "
            f"{history[-2]['actionID']}"
        )
        del history[-1]  # remove last action ADMIN_WITHR_APP
        del history[-1]  # remove last action ADMIN_ACC_APP

    # TBV: the logic does not allow ADMIN_WITHR_APP without confirm version
    # removed open appeal and withdrawn appeal
    if preprintid == "JCAP_079P_1216" and imported_version_num == 5:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[-1]['actionID']} "
            f"{history[-2]['actionID']}"
        )
        del history[-1]  # remove last action ADMIN_WITHR_APP
        del history[-1]  # remove last action ADMIN_ACC_APP

    # TBV: the logic does not allow ADMIN_WITHR_APP without confirm version
    # removed open appeal and withdrawn appeal - here the appeal is reopened again right
    # after being withdrawn (ADMIN_ACC_APP, ADMIN_WITHR_APP, ADMIN_ACC_APP_NEW_ED), so only
    # the abandoned open+withdraw pair is removed, keeping the real second reopening
    if preprintid == "JCAP_012P_0611" and imported_version_num == 1:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: {history[-2]['actionID']} "
            f"{history[-3]['actionID']}"
        )
        del history[-2]  # remove action ADMIN_WITHR_APP
        del history[-2]  # remove action ADMIN_ACC_APP

    # Dirty wjapp data (missing controls + a cached browser page, confirmed on the wjapp UI,
    # not a real editorial action): ED_ADD_REF (editor adds a referee) is logged after
    # ED_REQ_REV (editor already requested a revision), which the real wjapp workflow does
    # not permit. Drop the stray ED_ADD_REF.
    if preprintid == "JCAP_079P_0523" and imported_version_num == 1:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[9]['actionID']}")
        del history[9]  # remove action ED_ADD_REF

    # Author declared "ready for publication" (AU_PUBLISHES) then, 9 minutes later, sent
    # actual corrections (AU_SENDS_CORRECT) - confirmed on the wjapp UI: the "ready"
    # declaration was immediately superseded by the correction, not a real final state. Drop
    # the premature AU_PUBLISHES so AU_SENDS_CORRECT can proceed from the prior state.
    if preprintid == "JCAP_003P_0305" and imported_version_num == 3:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    # Same premature-AU_PUBLISHES pattern as JCAP_003P_0305/3 above (verified fix), confirmed
    # via errors_details_20260923123230.csv + the raw wjapp history dump for each version:
    # AU_PUBLISHES at index 2, immediately followed by AU_SENDS_CORRECT at index 3.
    if preprintid == "JCAP_052P_1210" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    if preprintid == "JCAP_021P_0713" and imported_version_num == 3:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    if preprintid == "JCAP_001P_0314" and imported_version_num == 3:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    if preprintid == "JCAP_006P_0314" and imported_version_num == 3:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    if preprintid == "JCAP_027P_0314" and imported_version_num == 3:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    if preprintid == "JCAP_009P_1114" and imported_version_num == 2:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    if preprintid == "JCAP_003P_1117" and imported_version_num == 3:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    if preprintid == "JCAP_077P_1019" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    # NB: this version's history also has AU_SENDS_CORRECT twice in a row (indices 3 and 4,
    # before PM_SENDS_TO_TYP) - unverified whether a second immediate author_sends_corrections()
    # is accepted by the FSM once the first one goes through; watch this one on re-run.
    if preprintid == "JCAP_020P_0221" and imported_version_num == 7:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: {history[2]['actionID']}")
        del history[2]  # remove premature AU_PUBLISHES

    # Typesetter already took charge in the previous version (/3); PM then
    # requested more info (PM_REQ_INFO) but the author never sent corrections (no
    # AU_SENDS_CORRECT) before the same typesetter "took charge" again here - sent for proof
    # without author send changes, not possible in wjs (no new round to take charge of).
    # Originally fixed by only dropping the duplicate TYP_TAKES_CHARGE (same pattern as
    # JCAP_113P_0224/1, JCAP_036P_0625/4, JCAP_132P_0625/2 above), but that left an incomplete
    # TYP_UPLOADS_FOR_PM + PM_REQ_INFO pair dangling in this version (author never responds, so
    # state never hands back to TYPESETTER_SELECTED) - v5 then starts its own fresh, complete
    # cycle: TYP_UPLOADS_FOR_PM, PM_REQ_INFO, AU_SENDS_CORRECT, PM_SENDS_TO_TYP. Its
    # TYP_UPLOADS_FOR_PM fails "Invalid article state" because v4 never restored
    # TYPESETTER_SELECTED. Same shape as the JCAP_038P_0409/etc "empty the whole spurious
    # version" group: v4 is a whole abandoned attempt superseded by v5, not just one duplicate
    # action - empty it entirely rather than only the duplicate TYP_TAKES_CHARGE.
    if preprintid == "JCAP_041P_1014" and imported_version_num == 4:
        import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
        del history[:]

    # Same wjapp missing-state-control bug as above, different action: the author confirmed
    # their appealed version twice, 23 minutes apart (v2, then again as v3's first action) -
    # AU_CONF_VER maps to AuthorSubmitRevisionAction, which needs an EditorRevisionRequest for
    # the *current* review round. Dropping v2 (rather than v3's duplicate) so v3's AU_CONF_VER
    # is the first one actually processed, against the round ADMIN_ACC_APP already created a
    # request for - v3 then continues cleanly (ED_ACT_AS_REF, ED_REJ_DOC) in the fresh round
    # that this confirmation itself creates.
    if preprintid == "JCAP_003P_0506" and imported_version_num == 2:
        import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
        del history[:]

    # JCAP_038P_0306/3: admin reassigns editor (ADMIN_ASS_N_ED, actHistCod 12371) for an
    # already-rejected paper (2006 data - unclear if wjapp's own state control permitted this
    # even back then), with no formal appeal ever reopened for this second rejection (the
    # first appeal already fully resolved in v1/v2). AssignToEditor's own _check_conditions()
    # explicitly allows a REJECTED source state for this scenario ("When EO opens appeals (for
    # rejected papers), she chooses an editor"), but _update_state() has no transition for it
    # unless appeal=True (handled by OpenAppeal elsewhere), which this import path doesn't set.
    # Rather than bypass the FSM with a direct state override (would skip real side effects -
    # EditorRevisionRequest + new review round creation - leaving the workflow in a state no
    # legal sequence of transitions could have produced), relabel the no-op ADMIN_RESETS_ED to
    # ADMIN_ACC_APP (reopens the appeal properly) and insert a synthetic AU_CONF_VER right
    # after it (author confirms, closing the appeal) - the real legal transition path, just
    # with the missing wjapp actions synthesized. AuthorSubmitRevisionAction's cover-letter
    # reader already degrades gracefully to an empty message when no real CVLETT data exists
    # (confirmed via JCAP_003P_0506 above), so this doesn't fabricate false content, only the
    # bare confirmation event. Reused the real author identity (agentCod 1123, Thomas
    # Sotiriou) from this same preprintid's own genuine AU_CONF_VER in v2.
    if preprintid == "JCAP_038P_0306" and imported_version_num == 3:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: {history[0]['actionID']}"
            " to ADMIN_ACC_APP, insert synthetic AU_CONF_VER after it"
        )
        history[0]["actionID"] = "ADMIN_ACC_APP"  # changed from ADMIN_RESETS_ED
        synthetic_au_conf_ver = {
            "actionID": "AU_CONF_VER",
            "agentCod": 1123,
            "actionDate": datetime.datetime.strptime("20060509150100", "%Y%m%d%H%M%S"),
            "actionCod": 25,
            "versionCod": 2564,
            "actHistCod": 0,
        }
        history.insert(1, synthetic_au_conf_ver)

    # JCAP_019P_0906/2: reviewer 263 (David Langlois) sends a second report (REF_SENDS_REP,
    # actHistCod 16282) with no ED_ASS_REF in v2's Action_History to (re)create the review
    # assignment for the current round - wjapp's Current_Referees confirms the reassignment
    # is real (versionCod=3180, refereeCod=263, editorCod=311, assignDate=2006-10-30 16:36:30,
    # acceptDate=2006-11-15 15:27:07, matching REF_ACC actHistCod 15745's actionDate exactly),
    # so this is a dropped Action_History row, not a fabricated event. Insert a synthetic
    # ED_ASS_REF right before the existing REF_ACC, using the real assignDate/editorCod/
    # refereeCod from Current_Referees - ReviewAssignmentAction.read_reviewer_data() then
    # finds the genuine Current_Referees row via targetCod, so no reviewer name/email needs
    # to be fabricated either.
    if preprintid == "JCAP_019P_0906" and imported_version_num == 2:
        synthetic_ed_ass_ref = {
            "actionID": "ED_ASS_REF",
            "agentCod": 311,
            "targetCod": 263,
            "actionDate": datetime.datetime.strptime("20061030163630", "%Y%m%d%H%M%S"),
            "actionCod": 1,
            "versionCod": 3180,
            "actHistCod": 0,
        }
        insert_index = next(i for i, a in enumerate(history) if a["actHistCod"] == 15745)
        history.insert(insert_index, synthetic_ed_ass_ref)
        import_logic.logger.warning(
            f"insert into history: {preprintid}/{imported_version_num}: "
            "added ED_ASS_REF for referee 263 (missing in wjapp source)"
        )

    # JCAP_006P_1013/1: ED_ADD_REF (actHistCod 115067) and ED_REM_REF (115079), 2 hours apart,
    # both record their target (userCod) as 2761 - the article's own editor - not a real
    # reviewer. Confirmed dirty on the wjapp UI itself: the editor selects themselves as
    # referee, then removes themselves as referee (the second action logged under a different
    # real agent than the first) - likely a duplicated/merged account for the editor, not a
    # genuine self-review. No report was ever submitted for this bogus assignment and it's
    # immediately self-cancelled, so drop both actions rather than importing a fake reviewer.
    if preprintid == "JCAP_006P_1013" and imported_version_num == 1:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: ED_ADD_REF/ED_REM_REF"
            " (editor added/removed themselves as referee - dirty wjapp data)"
        )
        history[:] = [a for a in history if a["actHistCod"] not in (115067, 115079)]

    # JCAP_067P_0417/4: PM_REQ_INFO (actHistCod 199729, "available for proofreading with
    # queries" on the wjapp UI, 2017-06-30 16:15) and PM_SENDS_FOR_PROOF_R (199730, "available
    # for proofreading", 16:16) fire back to back, both against the same TypesettingRound with
    # no new TYP_UPLOADS_FOR_PM in between - a duplicated wjapp notification for a single real
    # event, confirmed on the wjapp UI. Both map to the same Requestproofs.run() logic, so the
    # first (PM_REQ_INFO) legally transitions Proofreading and creates the round's
    # GalleyProofing; the second is the pure duplicate that then finds a TransitionNotAllowed
    # ("Proofreading" isn't a valid source for typesetter_submits a second time). Drop only the
    # duplicate, keeping PM_REQ_INFO so the article still legally reaches Proofreading before
    # AU_PUBLISHES ("author declares ready for publication") right after, which requires it.
    if preprintid == "JCAP_067P_0417" and imported_version_num == 4:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: PM_SENDS_FOR_PROOF_R"
            " (duplicate of PM_REQ_INFO - dirty wjapp data)"
        )
        history[:] = [a for a in history if a["actHistCod"] != 199730]

    # JCAP_060P_0519/2: the trailing ADMIN_ACC_APP (actHistCod 259307, 2019-12-24, "admin
    # accepts appeal") crashes because HandleDecision's admin-mode gate requires state
    # REJECTED/PAPER_MIGHT_HAVE_ISSUES, but the real state is TO_BE_REVISED - editor Licia
    # Verde's ED_REQ_REV (251087, 2019-09-09, "requires major revision") was followed by 3
    # months of silence (only SYS_REMINDS_AUT reminders, no author submission), which only
    # makes sense if that Sept decision was actually a rejection, mislabeled by wjapp as a
    # revision request. Same relabel-a-mislabeled-editor-decision pattern as the ED_ACC_DOC_WMC
    # cases elsewhere in this file: relabel ED_REQ_REV to ED_REJ_DOC so the state is REJECTED
    # by the time ADMIN_ACC_APP runs, letting the real appeal-reopening (already legal for the
    # admin-mode gate) proceed unmodified - no separate fix needed for ADMIN_ACC_APP itself.
    if preprintid == "JCAP_060P_0519" and imported_version_num == 2:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: ED_REQ_REV (251087)"
            " to ED_REJ_DOC (mislabeled rejection, not a real revision request)"
        )
        for a in history:
            if a["actHistCod"] == 251087:
                a["actionID"] = "ED_REJ_DOC"

    # JCAP_060P_0519/4: same mislabeled-rejection pattern recurs at the final version - editor
    # Sabino Matarrese's trailing ED_REQ_REV (actHistCod 267391, 2020-04-26) is the very last
    # action in the whole preprintid's history, with nothing after it (no reminders, no author
    # submission). wjapp's own Version.stateID for this final version is REJECTED (confirmed:
    # article_expected_final_state check fails otherwise), so this "requires major revision"
    # was never a real revision request either - relabel it to ED_REJ_DOC so the article's
    # final persisted state matches wjapp's own authoritative final status.
    if preprintid == "JCAP_060P_0519" and imported_version_num == 4:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: ED_REQ_REV (267391)"
            " to ED_REJ_DOC (mislabeled rejection, not a real revision request)"
        )
        for a in history:
            if a["actHistCod"] == 267391:
                a["actionID"] = "ED_REJ_DOC"

    # JCAP_081P_0121/1: author withdrew (AU_WITHD_DOC, 289653) before the editor's decline
    # (ED_REF_DOC, 289654, 2021-01-30) arrived - a moot bureaucratic action against an
    # already-Withdrawn article, not a real state change. Dropped here rather than added to
    # ED_REF_DOC's shared noop_cases allowlist (that set is JCOM/JCOMAL legacy, kept untouched
    # for the upcoming JINST/JSTAT/JHEP imports - JCAP-specific corrections stay scoped here).
    if preprintid == "JCAP_081P_0121" and imported_version_num == 1:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: ED_REF_DOC" " (moot - author already withdrew)"
        )
        history[:] = [a for a in history if a["actHistCod"] != 289654]

    # JCAP_030P_0622: an EO churns the editor A -> editor B -> editor A again across
    # v2(end)/v3/v4(start), before editor A reviews the paper themselves (ED_ACT_AS_REF, real -
    # kept) and is finally replaced by editor B for good in v5. Each ADMIN_ASS_N_ED runs
    # SupervisorChangeEditorAssignment, which grants the outgoing editor a PermissionAssignment
    # on the round's already-complete review assignment (reviewer 6518, completed back in
    # v1/v2 - no new round is ever created across this whole churn) via a plain .create() - not
    # idempotent. v3's swap (A->B) grants editor A that permission; v4's swap (B->A) undoes it
    # back; v5's swap (A->B, the real final handoff) tries to grant editor A the same
    # permission again and crashes on the unique constraint. Confirmed on the wjapp UI: v2's
    # trailing ADMIN_RESETS_ED and v3's SYS_ASS_ED/ADMIN_RESETS_ED are no-ops in this import (no
    # handler class, or a null target), so dropping the whole churn (v2's last action, all of
    # v3, and v4's ADMIN_ASS_N_ED) changes nothing else - editor A stays continuously the
    # editor from v1 through their self-review in v4, then v5's swap to editor B is the first
    # (and only) real migration of that permission, so it succeeds.
    if preprintid == "JCAP_030P_0622" and imported_version_num == 2:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: ADMIN_RESETS_ED")
        history[:] = [a for a in history if a["actHistCod"] != 340821]

    if preprintid == "JCAP_030P_0622" and imported_version_num == 3:
        import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
        del history[:]

    if preprintid == "JCAP_030P_0622" and imported_version_num == 4:
        import_logic.logger.warning(f"del from history: {preprintid}/{imported_version_num}: ADMIN_ASS_N_ED")
        history[:] = [a for a in history if a["actHistCod"] != 340839]

    # JCAP_036P_1222/2: ADMIN_CONFIRMS_TA fires twice, 4 seconds apart (actHistCod 349413,
    # 349414), same admin, same "Confirm production readiness" message -
    # a wjapp double-submission duplicate, not two real confirmations. The first already
    # transitions ReadyForTypesetter -> the next state; the second then finds
    # system_verifies_production_requirements() illegal from wherever that landed. Drop the
    # duplicate, keep the first.
    if preprintid == "JCAP_036P_1222" and imported_version_num == 2:
        import_logic.logger.warning(
            f"del from history: {preprintid}/{imported_version_num}: ADMIN_CONFIRMS_TA (duplicate)"
        )
        history[:] = [a for a in history if a["actHistCod"] != 349414]

    # JCAP_042P_1223/3: PM_PUBLISHES (actHistCod 382006, maps to DeclareReadyForPublication)
    # is followed by an author correction (AU_SENDS_CORRECT, 382234) and then PM_SENDS_TO_TYP
    # (back to typesetter) - the same shape as v2's real proofreading cycle (PM_REQ_INFO ->
    # AU_SENDS_CORRECT -> PM_SENDS_TO_TYP), not a real publication (the article isn't actually
    # published until v4/v5's PM_PUBLISHES -> PM_PRE_PUBLISHES -> PUB_PUBLISHES, much later).
    # Same relabel-a-mislabeled-decision pattern as the ED_ACC_DOC_WMC/ED_REJ_DOC cases
    # elsewhere in this file: relabel to PM_SENDS_FOR_PROOF_R so AU_SENDS_CORRECT finds the
    # GalleyProofing it expects.
    if preprintid == "JCAP_042P_1223" and imported_version_num == 3:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: PM_PUBLISHES (382006)"
            " to PM_SENDS_FOR_PROOF_R (mislabeled, not a real publication)"
        )
        for a in history:
            if a["actHistCod"] == 382006:
                a["actionID"] = "PM_SENDS_FOR_PROOF_R"

    # Dirty wjapp data (confirmed on the wjapp UI): the referee's report is followed by an
    # ED_REQ_REV ("editor requires major revision"), but the typesetter takes the article in
    # charge right after with no author resubmission or re-review in between - what actually
    # happened was an acceptance, mislabeled as a major-revision request. Same relabel-a-
    # mislabeled-editor-decision pattern as the ED_ACC_DOC_WMC cases above (JCAP_113P_0224/1,
    # JCAP_036P_0625/4, JCAP_132P_0625/2), but here the real decision was a plain accept, not
    # a minor revision, so relabel to ED_ACC_DOC instead of ED_ACC_DOC_WMC.
    if preprintid == "JCAP008A0104" and imported_version_num == 1:
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: {history[4]['actionID']}" " to ED_ACC_DOC"
        )
        history[4]["actionID"] = "ED_ACC_DOC"

    # Confirmed on the wjapp UI: version 2's typesetter-upload-then-available-for-proofreading
    # cycle (15:38-15:39) is a near-duplicate of version 3's (15:40-16:01), ~2 minutes apart,
    # with no real new typesetter work in between. Version 1 already ends with TYP_TAKES_CHARGE
    # (which sets state TYPESETTER_SELECTED), so dropping version 2 entirely lets version 3's
    # TYP_UPLOADS_FOR_PM proceed from that same state instead of from whatever version 2 left
    # it in.
    if preprintid == "JCAP_038P_0409" and imported_version_num == 2:
        import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
        del history[:]

    # Same duplicate-typesetting-cycle pattern as JCAP_038P_0409/2 above, confirmed on the
    # wjapp UI: version 4 is a near-duplicate of version 5, with no real new typesetter work.
    if preprintid == "JCAP_028P_0913" and imported_version_num == 4:
        import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
        del history[:]

    if preprintid == "JCAP_073P_0414" and imported_version_num == 4:
        import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
        del history[:]

    if preprintid == "JCAP_050P_0623" and imported_version_num == 4:
        import_logic.logger.warning(f"del all actions from history: {preprintid}/{imported_version_num}")
        del history[:]

    # PM_REQ_ED_CHECK not managed (see PM_REQ_ED_CHECK.run() in import_logic.py) - a wjapp
    # action with no wjs equivalent. All 23 originally-catalogued cases (errors_details_
    # 20260923123230.csv) are now fixed above via repair_history_specific_article: 13
    # ED_RESTARTS_REV (JCAP_113P_0224/JCAP_036P_0625/JCAP_132P_0625 plus 10 more), 7 ED_APPROVES
    # (next to JCAP_032P_0326), 3 ADMIN_REMINDS_ED (same fix as ED_APPROVES - it also resolves
    # to ED_APPROVES, just with a reminder logged in between). JCAP_069P_1225/7, caught live in
    # a later reimport, now also resolved via ED_APPROVES - see next to JCAP_017P_0920 above.

    # JCAP_080P_1026/1: editor 1 (Henneaux) decides ED_CON_NOT_SUIT (NOT_SUITABLE - a terminal
    # decision wjs defines no appeal for, confirmed with the EO). Admin then overrides that by
    # resetting the editor and assigning a new one (Verde) in version 2 - wjapp's standard,
    # if informal, way of handling this: reject instead, then open an appeal. wjapp's own
    # history never logged a formal appeal action here, so repair it to go through the real,
    # already-supported reject -> appeal path: relabel the NOT_SUITABLE decision as a plain
    # rejection, insert a synthetic ADMIN_ACC_APP_NEW_ED (wjs's OpenAppeal keeps Henneaux as
    # editor for this step; Verde is assigned normally afterwards, in version 2's real
    # ADMIN_ASS_N_ED), then relabel the following ADMIN_RESETS_ED as AU_CONF_VER - the same
    # precedent as JCAP_001P_0105/JCAP_015P_0405 above: supplies the author-driven
    # UNDER_APPEAL -> EDITOR_SELECTED transition that OpenAppeal's UNDER_APPEAL alone doesn't
    # give, so that ADMIN_ASS_N_ED's editor_assign_different_editor (source=EDITOR_SELECTED)
    # can proceed in version 2 instead of raising "Invalid state transition".
    if preprintid == "JCAP_080P_1026" and imported_version_num == 1:
        not_suitable_idx = next(i for i, a in enumerate(history) if a["actHistCod"] == 501515)
        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: "
            f"{history[not_suitable_idx]['actionID']} to ED_REJ_DOC"
        )
        history[not_suitable_idx]["actionID"] = "ED_REJ_DOC"  # was ED_CON_NOT_SUIT

        reset_idx = next(i for i, a in enumerate(history) if a["actHistCod"] == 501607)
        appeal_identity = _require_eo_in_charge_identity(eo_in_charge_identity, preprintid, imported_version_num)
        synthetic_appeal = {
            "actionID": "ADMIN_ACC_APP_NEW_ED",
            "actionDate": history[reset_idx]["actionDate"] - datetime.timedelta(seconds=1),
            "actionCod": 0,
            "versionCod": history[reset_idx]["versionCod"],
            "actHistCod": 0,
            **appeal_identity,
        }
        history.insert(reset_idx, synthetic_appeal)
        import_logic.logger.warning(
            f"insert into history: {preprintid}/{imported_version_num}: synthetic ADMIN_ACC_APP_NEW_ED "
            "(wjapp logged no formal appeal for this not-suitable override - EO confirmed the intent)"
        )

        import_logic.logger.warning(
            f"change history: {preprintid}/{imported_version_num}: "
            f"{history[reset_idx + 1]['actionID']} to AU_CONF_VER"
        )
        history[reset_idx + 1]["actionID"] = "AU_CONF_VER"  # was ADMIN_RESETS_ED

    for a in history:
        import_logic.logger.warning(
            f"repaired history specific article correction {preprintid}/{imported_version_num}: {a['actionID']}"
        )
