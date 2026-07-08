"""Tests for the correction (erratum/addendum) post-submission handler."""

from unittest.mock import MagicMock, patch

import pytest
from plugins.wjs_submission.events import SubmissionEvent


@pytest.mark.django_db
class TestProcessSubmittedCorrection:
    """Tests for the process_submitted_correction handler and AuthorHandleCorrection."""

    def test_event_is_registered(self):
        """Verify that ON_CORRECTION_SUBMISSION_COMPLETED is defined."""
        assert SubmissionEvent.ON_CORRECTION_SUBMISSION_COMPLETED == "on_correction_submission_completed"

    @patch("plugins.wjs_review.events.handlers.AuthorHandleCorrection")
    def test_process_submitted_correction_calls_handler(self, mock_handler_class):
        """Verify that process_submitted_correction delegates to AuthorHandleCorrection."""
        from plugins.wjs_review.events.handlers import process_submitted_correction

        mock_instance = MagicMock()
        mock_handler_class.return_value = mock_instance

        process_submitted_correction(
            request=MagicMock(),
            article=MagicMock(),
        )

        mock_handler_class.assert_called_once()
        mock_instance.run.assert_called_once()

    @patch("plugins.wjs_review.logic.dispatch_eo_assignment")
    @patch("plugins.wjs_review.logic.AuthorHandleCorrection._notify_coauthors")
    @patch("plugins.wjs_review.logic.AuthorHandleCorrection._log_operation")
    def test_author_handle_correction_run(self, mock_log, mock_notify, mock_eo):
        """Verify that AuthorHandleCorrection.run() calls all three steps."""
        from plugins.wjs_review.logic import AuthorHandleCorrection

        handler = AuthorHandleCorrection(
            request=MagicMock(),
            article=MagicMock(),
        )
        handler.run()

        mock_log.assert_called_once()
        mock_notify.assert_called_once()
        mock_eo.assert_called_once()


@pytest.mark.django_db
class TestCorrectionConstants:
    """Tests for the correction section constants."""

    def test_erratum_code_is_x(self):
        """Verify erratum code is X."""
        from wjs.jcom_profile.constants import JCOM_SECTION_TO_PUBIDSECTIONCODE

        assert JCOM_SECTION_TO_PUBIDSECTIONCODE["erratum"] == "X"

    def test_addendum_code_is_z(self):
        """Verify addendum code is Z."""
        from wjs.jcom_profile.constants import JCOM_SECTION_TO_PUBIDSECTIONCODE

        assert JCOM_SECTION_TO_PUBIDSECTIONCODE["addendum"] == "Z"

    def test_erratum_does_not_collide_with_editorial(self):
        """Verify erratum code differs from editorial code."""
        from wjs.jcom_profile.constants import JCOM_SECTION_TO_PUBIDSECTIONCODE

        assert JCOM_SECTION_TO_PUBIDSECTIONCODE["erratum"] != JCOM_SECTION_TO_PUBIDSECTIONCODE["editorial"]

    def test_addendum_does_not_collide_with_article(self):
        """Verify addendum code differs from article code."""
        from wjs.jcom_profile.constants import JCOM_SECTION_TO_PUBIDSECTIONCODE

        assert JCOM_SECTION_TO_PUBIDSECTIONCODE["addendum"] != JCOM_SECTION_TO_PUBIDSECTIONCODE["article"]
