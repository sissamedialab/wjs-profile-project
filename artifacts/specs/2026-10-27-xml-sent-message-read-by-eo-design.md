# Mark the "XML sent to publisher" EO message as read by the EO

- **Issue:** wjs-profile-project#298 ("As EO I want to be notified on IOP XML ZIP transmission")
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-10-27
- **Status:** awaiting approval

## Problem

On transmission of the production export zip at acceptance, `SendProductionXMLToPublisher._log_operation`
(`logic__production.py`) logs a message to the EO through `communication_utils.log_operation`. The issue's
"Current status" says the timeline entry exists but is not yet "marked as read": the message is created unread,
so it shows up as a pending notification the EO has to dismiss, although the issue wants the EO only to be
informed through the timeline.

## Requirements

- The "XML sent to publisher" message (subject/body from the `xml_sent_to_publisher_*` settings) is created with
  `read_by_eo=True`, by passing `flag_as_read_by_eo=True` to `communication_utils.log_operation`.
- Nothing else changes: recipients, verbosity (`FULL`), subject and body stay as they are.

## Non-goals

- The separate "corresponding author affiliation incomplete" EO warning logged by
  `_log_incomplete_affiliation_warning` stays unread: it asks the EO to act.
- The "after production" transmission: the issue itself marks that feature as not available yet.
- Changing the verbosity or adding email notification (the issue's open question; IOP confirmed no notification
  is needed).

## Design

One-line change in `SendProductionXMLToPublisher._log_operation`: add `flag_as_read_by_eo=True` to the
`log_operation` call. `log_operation` already supports the parameter and sets `read_by_eo` on the `Message`
(`communication_utils.py:255-301`); other EO timeline logs in the plugin use it the same way.

## Testing

Extend `test_send_production_xml_to_publisher_sends_zip_and_logs_message` (`tests/test_production.py`), which
already inspects the first `log_operation` call, to assert `flag_as_read_by_eo is True`. Add an unmocked test
asserting the created `Message` has `read_by_eo=True`, so the check does not only depend on the call arguments.
