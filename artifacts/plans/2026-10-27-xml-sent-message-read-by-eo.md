# Plan: mark the "XML sent to publisher" EO message as read by the EO

Spec: `artifacts/specs/2026-10-27-xml-sent-message-read-by-eo-design.md` (wjs-profile-project#298).
Branch: `bugfix/issue-298-flag-xml-sent-message-read-by-eo`.

## Steps

1. **Red.** In `wjs/plugins/wjs_review/tests/test_production.py`:
   - in `test_send_production_xml_to_publisher_sends_zip_and_logs_message`, assert
     `export_prepared_call.kwargs["flag_as_read_by_eo"] is True`;
   - add `test_send_production_xml_to_publisher_message_is_read_by_eo`: run `SendProductionXMLToPublisher` for a
     configured journal (same setup as the test above, `send_zip_to_iop` mocked, `log_operation` not mocked) and
     assert the "XML sent" `Message` for the article has `read_by_eo=True` (the affiliation warning, if present,
     is excluded by subject).
   Run from `janeway/src`: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_production.py -k send_production_xml`
   and confirm both fail.
2. **Green.** In `logic__production.py`, `SendProductionXMLToPublisher._log_operation`, add
   `flag_as_read_by_eo=True` to the `communication_utils.log_operation(...)` call. Re-run the same selector: all
   `send_production_xml` tests pass.
3. **Check.** Run `pre-commit run --all-files`.
4. **Wrap-up** (flow steps 9-11): code-eval, doc-sync, activity-log entry; changes left in the working tree for
   commit review. No Playwright (backend only); no towncrier fragment (no `changes/` directory).
