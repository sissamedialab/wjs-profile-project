# Changelog

## [2.0.25] - 2026-08-12

- [specs#3005: Handle warning when changing profile URL](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3005) — fix(js): fix warning altert js to work properly with TinyMCE (!1462)
- [wjs-profile-project#285: When looking reminders, check the sent date, but also the due date for disabled reminders](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/285) — feat: materialized attention condition (!1341)
- [wjs-profile-project#287: Verify that ACs for AUTHOR_PROOFING_LATE for authors are resolved (if they exist)](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/287) — feat: materialized attention condition (!1341)
- [specs#2621: Ensure that JQuant authors don't see the link "send short description and image for social media"](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2621) — feat: materialized attention condition (!1341)
- [specs#2764: test attention conditions performance with import stress test database](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2764) — feat: materialized attention condition (!1341)
- [specs#2823: When looking reminders, check the sent date, but also the due date for disabled reminders](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2823) — feat: materialized attention condition (!1341)
- [specs#2824: Verify if we can move the local imports of ac_service (& co.) at the top of the modules](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2824) — feat: materialized attention condition (!1341)
- [wjs-profile-project#284: Drop article_requires_attention() methods from states module and adapt tests](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/284) — feat: materialized attention condition (!1341)
- [specs#2822: Drop article_requires_attention() methods from states module and adapt tests](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2822) — feat: materialized attention condition (!1341)
- [wjs-profile-project#286: Verify if we can move the local imports of ac_service (& co.) at the top of the modules](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/286) — feat: materialized attention condition (!1341)
- [specs#2825: Remove creation of ACs for AUTHOR_PROOFING_LATE for **authors**](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2825) — feat: materialized attention condition (!1341)
- [wjs-profile-project#288: Verify that AC MISSING_SOCIAL_MEDIA & co. are created early on for the author to act on them](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/288) — feat: materialized attention condition (!1341)
- [specs#2826: Verify that AC MISSING_SOCIAL_MEDIA & co. are created early on for the author to act on them](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2826) — feat: materialized attention condition (!1341)
- No linked issue — feat(a11y): add a11y review fixes (!1375)
- [specs#2877: Generate custom pubid for erratum / addendum](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2877) — Pubid and EID for JQuant and JCAP (!1460)
- [specs#2304: Compute pubid for JQUANT](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2304) — Pubid and EID for JQuant and JCAP (!1460)
- [specs#2756: Ensure that "section code" for JQuant is correctly used/ignored in eid, pubid, how-to-cite](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2756) — Pubid and EID for JQuant and JCAP (!1460)

## [2.0.24] - 2026-08-06

- [wjs-profile-project#204: As developer I want to investigate why pytest 8.4 breaks our tests setup](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/204) — fix: port test suite to pytest 9 and latest pytest-django (!1458)
- [specs#2908: As EO I want to update the reviewer report text and PDF](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2908) — Feature: fix advanced_admin search and labels (!1457)
- [specs#2936: Problem with warning message in user profile](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2936) — fix: fix detecting initial value of tinynce widgets (!1442)
- [wjs-help#195: Most reviewers cannot be manually selected](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/195) — fix: regressions for !1435 (!1452)
- [wjs-profile-project#290: Use activity page / log_message to notify editore disabling themselves](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/290) — fix: regressions for !1435 (!1452)
- [specs#737: As a Editor and reviewer I want to disable myself from receiving new assignments](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/737) — fix: regressions for !1435 (!1452)
- [specs#2917: Update JCAP and other journals'  User profile](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2917) — feature(profile): add career_stage and handle JCAP exceptions (!1446)
- [specs#2932: Refactor article status page metadata section](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2932) — Article status metadata update (!1449)
- [specs#2936: Problem with warning message in user profile](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2936) — fix(js): add exception to toggle btn to prevent warning (!1453)
- [specs#2929: Modify intro text on page "Send corrections/reply to queries"](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2929) — feat: add template tag to hide sentence in all journals but JCOM and JCOMAL (!1448)
- Reapply "Release 2.0.20"
- Reapply "Merge branch 'wjs-develop' into 'wjs-production'"
- [wjs-help#179: 4196 - data submission missing](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/179) — fix: do not fail on not-existing article.date_submitted (!1440)
- [wjs-help#181: JCOM_4205 - Auth cannot link ORCID to their profile](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/181) — fix: remove orcid from WjsPersonalInfoForm (!1445)
- [jcomassistant-project#42: Expose more metadata](https://gitlab.sissamedialab.it/wjs/jcomassistant-project/-/work_items/42) — Sync license, rights, arxiv, etc. (!1432)
- [specs#2880: Sync metadata tex<->DB](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2880) — Sync license, rights, arxiv, etc. (!1432)

## [2.0.20] - 2026-07-30

- [specs#2957: Server error trying to arrange JCAP home page](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2957) — fix: fix home page error when plugin configuration is missing (!1438)
- [specs#2907: Create deployment environment 1-5 and enable deployment from CI](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2907) — Keywords monthly usage (!1429)
- [specs#2908: As EO I want to update the reviewer report text and PDF](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2908) — feat: add new advanced admin for WorkflowReviewAssignmentAdmin and EditorDecisionAdmin (!1436)
- [specs#2909: As EO I want to update the editor report text and PDF](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2909) — feat: add new advanced admin for WorkflowReviewAssignmentAdmin and EditorDecisionAdmin (!1436)
- [specs#737: As a Editor and reviewer I want to disable myself from receiving new assignments](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/737) — feat: block manual editor assignment if not enabled (!1435)
- [wjs-profile-project#290: Use activity page / log_message to notify editore disabling themselves](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/290) — feat: block manual editor assignment if not enabled (!1435)
- [specs#2890: Improve and complete `anonymize_data.py` script](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2890) — Add fields to anonymize_data (!1426)
- [specs#2791: Correct texts for email change](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2791) — Update email change messages and save alternative email (!1409)
- Bump - Release 2.0.20.dev1
