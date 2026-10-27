## 2026-10-27 — Flag the "XML sent to publisher" EO message as read by the EO
**What:** `SendProductionXMLToPublisher._log_operation` now passes `flag_as_read_by_eo=True`, so the timeline message for the accepted-paper zip transmission is created already read by the EO.
**Why:** wjs-profile-project#298 asks the EO to be informed through the timeline, not to have an unread item to dismiss; the "mark as read" box for the accepted case was the only open point.
**Decisions:** Only the "XML sent" message is flagged. The "corresponding author affiliation incomplete" warning stays unread because it asks the EO to act. The "after production" transmission is not touched (the issue says the feature does not exist yet), and verbosity stays `FULL`.
**Follow-ups:** The issue's open question (is an unread timeline entry enough, or should the EO get an email) is unanswered; "after production" half of #298 is still to do when that feature lands.
**Refs:** wjs-profile-project#298; Eval: 88% — artifacts/evaluations/2026-10-27-xml-sent-message-read-by-eo.md
