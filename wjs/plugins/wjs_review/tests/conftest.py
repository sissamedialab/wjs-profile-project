import pytest  # noqa
from review import models as review_models
from utils import setting_handler  # noqa

from wjs.jcom_profile.tests.conftest import *  # noqa

from ..logic import AssignToEditor
from ..models import ArticleWorkflow
from ..plugin_settings import set_default_plugin_settings


@pytest.fixture
def review_settings():
    set_default_plugin_settings()


@pytest.fixture
def assigned_article(fake_request, article, section_editor):
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()
    workflow = AssignToEditor(
        article=article,
        editor=section_editor,
        request=fake_request,
    ).run()
    workflow.article.stage = "Assigned"
    workflow.article.save()
    return workflow.article


@pytest.fixture
def review_form(journal):
    review_form = review_models.ReviewForm(name="A Form", slug="A Slug", intro="i", thanks="t", journal=journal)
    review_form.save()

    review_form_element, __ = review_models.ReviewFormElement.objects.get_or_create(
        name="Review",
        kind="text",
        order=1,
        width="full",
        required=True,
    )
    review_form.elements.add(review_form_element)
    setting_handler.save_setting(
        "general",
        "default_review_form",
        journal,
        review_form_element.pk,
    )
