# Changelog

## [2.1.2] - 2026-09-23

- [specs#3097: Test Django 5.2](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3097) — Re-generate WJS venvs (!1507)
- [specs#3165: Regen venvs of all Ts](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3165) — Re-generate WJS venvs (!1507)
- [specs#2595: Migrate to Django 5.2](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2595) — Re-generate WJS venvs (!1507)
- [specs#3155: Deploy to production](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3155) — Re-generate WJS venvs (!1507)
- [specs#2288: Failed connection to DB - connection already closed](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2288) — Do not upgrade postgresql "unattended" (!1470)
- [specs#2910: Hold postres upgrades (was "link postgrest to wjs")](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2910) — Do not upgrade postgresql "unattended" (!1470)
- [specs#3168: Upgrade "auditor" on wjs-test](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3168) — Do not upgrade postgresql "unattended" (!1470)
- [specs#3024: G7 GET list of production papers (all journals)](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3024) — feat: Add JournalProductionListView for G7 (!1509)
- [specs#3137: error saving changes to Update editor parameters](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3137) — Fix: 500 error saving editor parameters — missing hidden pk field in keyword formset (!1511)
- [specs#2983: Quality of life admin improvementrs](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2983) — Feat: advanced admin (!1500)
- [specs#2984: As EO I want to create new files](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2984) — Feat: advanced admin (!1500)
- [specs#2964: Fix search in "search preprints"](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2964) — fix: fix author filter in StaffArticleWorkflowFilter (!1505)

## [2.1.1] - 2026-09-23

- [specs#3196: Upgrade autobahn to 26.7](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3196) — Upgrade autobahn to 26.7 (!1526)
- [specs#3082: Labels should have a cohoerent colour across pages](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3082) — feat: add typesetter exception for badge waiting for flow refactoring (!1504)
- [specs#3098: Live test JP integration test](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3098) — add components of jp sgp bridge (!1444)
- [wjs-profile-project#297: JP bridge cookie should have the same domain of the journal in production](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/297) — add components of jp sgp bridge (!1444)
- [specs#2949: Implement SGP - JP Bridge](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2949) — add components of jp sgp bridge (!1444)
- [specs#3025: G8 GET typesetters stats (for monitoring)](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3025) — feat: G8 - add typesetters papers monitoring endpoint (!1503)
- [specs#3120: As a typesetter i need to distinguish between "taken in charge" and "proofs received" status](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3120) — feat: G8 - add typesetters papers monitoring endpoint (!1503)
- [specs#3048: Problems filtering by author in Vetrinetta](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3048) — fix(views): filter author landing page on frozen authors (!1510)
- [specs#2961: Adapt HTML post-processing to JQuant](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2961) — feature: use custom body processing function for JQuant (!1483)
- [specs#883: As sysadmin I want the import command not to depend on wjs_review plugin, because the latter is not yet installed in production](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/883) — feature: use custom body processing function for JQuant (!1483)
- [specs#3149: Adapt "dummy generator" to JQuant and JCAP](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3149) — feature: use custom body processing function for JQuant (!1483)
- [specs#2881: Sync authors text<->DB](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2881) — Sync authors (!1497)
- [specs#1804: As EO I want to update authors' data (name, email, etc.) if data from TeX is more complete](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/1804) — Sync authors (!1497)
- [specs#2924: Review check by Giorgia in #2771](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2924) — Sync authors (!1497)
- [specs#3133: Ensure that all "searches" of article authors use the FrozenAuthors and not A.article_authors](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3133) — Sync authors (!1497)
- [specs#3134: Tidy-up twitter handle](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3134) — Sync authors (!1497)
- [specs#3116: Review message sent to newly associated co-authors](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3116) — Sync authors (!1497)
- [specs#2997: As EO I want a UI to handle multiple attention conditions](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2997) — feature: add attention condition template and update review listing template (!1514)
- [specs#3061: Improvements to JCAP home page](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3061) — style(login): add exception to handle login btn style for jcap (!1494)
- [specs#3091: Add --dry-run option to release.sh script](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3091) — Add the option --dry-run to the release script (!1498)

## [2.1.0] - 2026-09-16

- No linked issue — fix: minor fixes preventing installation from scratch (!1508)
- [specs#2595: Migrate to Django 5.2](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2595) — Django 5.2 migration + pre-existing bug fix (!1478)
- [specs#3147: Internal Server Error: /plugins/wjs-review-articles/eo/workon/  FieldError: Unsupported lookup 'country' for ForeignKey](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3147) — Bugfix/issue 3147  fix author country filter lookup (!1506)
- [specs#3083: 28.8 feedback - JCAP (and other journals) entire flow](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3083) — fix(review): re-run step 8 checks and validation when authors submit a revision (!1492)
- [specs#3072: Set autocomplete=off on current password field in profile](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3072) — feat: add autocomplete attribute to current password field (!1502)
- [specs#107: As a manager I want to preserve the article metadata](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/107) — Restrict anonymous access to IMU, experimental, and article-list views (!1479)
- [wjs-help#29: 1603: reminders for "editor to be selected" should be included in reminder schedule](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/29) — fix: add missing content type to ArticleReminders.ArticleReminders (!1480)
- [wjs-profile-project#294: Improve custom admin view form template](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/294) — feat: AC for blacklisted authors (!1466)
- [specs#2980: As EO I want an attention condition when a submission includes at least one blacklisted author](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2980) — feat: AC for blacklisted authors (!1466)
- [wjs-help#190: revision submission errror (dev - 4220)](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/190) — fix(review): populate affiliation on revision and run step 7 for metadata revisions too (!1493)
- [specs#2884: Sync collaboration tex<->db](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2884) — Sync collaborations (!1472)
- [jcomassistant-project#42: Expose more metadata](https://gitlab.sissamedialab.it/wjs/jcomassistant-project/-/work_items/42) — Sync collaborations (!1472)
- [specs#2971: Analyse how to sync collaboration between tex and db](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2971) — API entry point for typ to download tabellone (!1465)
- [specs#3027: G10 GET collaborations list](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3027) — API entry point for typ to download tabellone (!1465)
- [wjs-profile-project#295: Refactor API authorisation layer to use DRF permission classes](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/295) — API entry point for typ to download tabellone (!1465)
- [specs#2851: Block failed API requests with fail2ban](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2851) — API entry point for typ to download tabellone (!1465)
- [specs#2804: Add security reuirementes to Django REST framework endpoints to allow upload](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2804) — API entry point for typ to download tabellone (!1465)
- [specs#3104: Internal Server Error: /plugins/wjs-review-articles/annotated_files/1628/](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3104) — annotated files: cannot delete files (!1496)
- [wjs-help#209: "Change due date" for revisions should update reminder texts](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/209) — Re-render reminder text when a revision due date is postponed (!1495)

## [2.0.28] - 2026-09-09

- [specs#3099: Test erratum / addendum](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3099) — Manually link wjs_review plugin (!1491)
- [specs#2977: 31 Jul feedback: JCAP settings and reminders for go-live](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2977) — Add Reminder body for JQuant (!1363)
- [specs#2605: Update reminder text for JQuant](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2605) — Add Reminder body for JQuant (!1363)
- [specs#2789: Change mail address from medialab.sissa.it to sissamedialab.it](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2789) — Replace @medialab.sissa with @sissamedialab (!1385)
- [specs#2871: Update referernces to https://medialab.sissa.it/ to new domain](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2871) — Replace @medialab.sissa with @sissamedialab (!1385)
- [specs#2805: Verify activity page performance with jhep / jcap database](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2805) — feat: improve activity page performance (!1463)
- [specs#3006: Improve UX for selecting unavailable reviewers](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3006) — feature: show detailed information about reviewer availability in select reviewer page (!1475)
- [specs#3088: Improve reviewer selection safety](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3088) — feature: show detailed information about reviewer availability in select reviewer page (!1475)
- [specs#3089: Improve sorting of editors in editors list](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3089) — feature: show detailed information about reviewer availability in select reviewer page (!1475)
- [specs#2918: JCAP corresponding author's required information](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2918) — refactor(account_validation): move jcom/jcap validators to wjs.jcom_profile (!1477)
- [specs#3083: 28.8 feedback - JCAP (and other journals) entire flow](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3083) — refactor(account_validation): move jcom/jcap validators to wjs.jcom_profile (!1477)
- [wjs-submission-project#30: Verify that wjs-submission does not depend from wjs-profile](https://gitlab.sissamedialab.it/wjs/wjs-submission-project/-/work_items/30) — refactor(account_validation): move jcom/jcap validators to wjs.jcom_profile (!1477)
- [specs#3012: JCOMAL editor had workload 1 and was automatically selected](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3012) — fix: fix missing assignment setting for jcomal (!1481)
- [wjs-help#156: JCOMAL: missing data on "extra" field in the "pending issue" section](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/156) — fix jcomal missing extra data in pending issue (develop) (!1485)
- [specs#3040: "Career stage" field is required regardless of the selected "Profession"](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3040) — fix: fix profession / career stage always required (!1482)
- chore: ignore .worktrees/ directory

## [2.0.27] - 2026-08-27

- [wjs-help#204: problems with new AC](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/204) — Fixes for ACs for unread messages (!1473)
- [specs#3069: Check if it's possible to have HAS_UNREAD_MESSAGE AC with high priority for EO and low priority for others](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3069) — Fixes for ACs for unread messages (!1473)

## [2.0.26] - 2026-08-24

- [specs#2969: Delete old deploy script](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2969) — Fix name of deploy script (!1468)
- [specs#2889: Revise workflow](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2889) — Test instances default wjs-develop (!1469)
- [specs#2879: Integrate hydra in wjs](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2879) — Integrate hydra (!1461)
- [specs#3045: Review latex preamble wrt errata](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3045) — Integrate hydra (!1461)
- [specs#3032: Simplify wjs_customization.article.wjs_filter_children_articles()](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3032) — Integrate hydra (!1461)
- [specs#3060: Check CI jobs that build Janeway image used for tests](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3060) — Integrate hydra (!1461)
- [specs#2782: Check Open Access Mode behaviour](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2782) — Use key "affiliation_pk" from revision storage data (!1464)
- [wjs-help#190: revision submission errror (dev - 4220)](https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/190) — Use key "affiliation_pk" from revision storage data (!1464)
- [specs#2954: Fundings delete does not work](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2954) — fix: fix JCAP settings (!1443)
- [specs#2042: JCAP / JHEP Submissions tests](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2042) — fix: fix JCAP settings (!1443)
- [specs#2931: Create skill to manage releases and changelog](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2931) — feat: add release.sh automation, Claude Code project rules, and CI test job (!1441)
- [wjs-profile-project#293: Align templates with djlint rules](https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/293) — feat: add release.sh automation, Claude Code project rules, and CI test job (!1441)
- [specs#123: Access to copy of JCOM Drupal (belwe)](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/123) — feat: add release.sh automation, Claude Code project rules, and CI test job (!1441)
- [specs#2952: Verify yakunin connection in t2](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2952) — Fix redis overlap on wjs-test instances (!1459)
- [specs#3003: When a paper is published, cache is cleared and roles cache is cleared also](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3003) — Fix redis overlap on wjs-test instances (!1459)

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
