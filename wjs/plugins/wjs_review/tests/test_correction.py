"""Tests for the correction (erratum/addendum) post-submission handler."""

import pytest
from plugins.wjs_review.models import ArticleWorkflow, WjsEditorAssignment
from plugins.wjs_submission.correction.logic import ERRATUM, SetupCorrectionStorage
from plugins.wjs_submission.step8.logic import CompleteSubmission


@pytest.mark.django_db
class TestProcessSubmittedCorrection:
    """Tests for the process_submitted_correction handler and AuthorHandleCorrection."""

    def test_process_submitted_correction_calls_handler(
        self, review_settings, fake_request, published_article_with_standard_galleys, correction_sections, eo_user
    ):
        """Verify that corrections (errata / addenda) have same EO of original paper and no editor assigned."""
        from events import registration  # noqa: Forces events to load into memory

        fake_request.user = published_article_with_standard_galleys.correspondence_author
        published_article_with_standard_galleys.articleworkflow.eo_in_charge = eo_user
        published_article_with_standard_galleys.articleworkflow.save()

        setup = SetupCorrectionStorage(
            article_id=published_article_with_standard_galleys.pk,
            relationship=ERRATUM,
            request=fake_request,
        )
        to_article = setup.run()

        CompleteSubmission(article=to_article, request=fake_request, first_submission=True).run()

        to_article.refresh_from_db()
        assert to_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        assert to_article.articleworkflow.eo_in_charge
        assert not WjsEditorAssignment.objects.filter(article=to_article).exists()

    def test_author_handle_correction_run(self, fake_request, article, eo_user):
        """Verify that process_submission process the article as a standard submission."""
        from plugins.wjs_review.events.handlers import process_submission

        fake_request.user = article.owner
        workflow = article.articleworkflow
        workflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        process_submission(
            request=fake_request,
            workflow=workflow,
        )

        article.refresh_from_db()
        assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        assert article.articleworkflow.eo_in_charge
        assert not WjsEditorAssignment.objects.filter(article=article).exists()


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
