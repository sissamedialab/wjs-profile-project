import pytest
from plugins.wjs_review import states
from submission.models import Article

from ..models import ArticleWorkflow


@pytest.mark.parametrize("batch_publish", (True, False))
@pytest.mark.django_db
def test_article_requires_eo_attention_missing_image_and_missing_description(
    rfp_article: Article, eo_user, batch_publish
):

    assert not rfp_article.meta_image
    assert rfp_article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION

    params = rfp_article.issue.issueparameters
    params.batch_publish = batch_publish
    params.save()
    assert rfp_article.issue.issueparameters.batch_publish == batch_publish

    rpf_state_class = getattr(states, rfp_article.articleworkflow.state)
    attention_condition = rpf_state_class.article_requires_attention(article=rfp_article, user=eo_user)

    if batch_publish:
        assert attention_condition == ""
    else:
        assert attention_condition == "Missing image and/or short description for social media"


@pytest.mark.parametrize("batch_publish", (True, False))
@pytest.mark.django_db
def test_article_requires_eo_attention_image_ok_but_missing_description(
    rfp_article: Article, eo_user, generate_image, settings, issue, batch_publish
):

    settings.WJS_JOURNALS_WITH_ENGLISH_CONTENT = [rfp_article.journal.code]

    assert rfp_article.title_en
    assert rfp_article.abstract_en

    rfp_article.meta_image = generate_image
    rfp_article.save()

    params = rfp_article.issue.issueparameters
    params.batch_publish = batch_publish
    params.save()
    assert rfp_article.issue.issueparameters.batch_publish == batch_publish

    rpf_state_class = getattr(states, rfp_article.articleworkflow.state)
    attention_condition = rpf_state_class.article_requires_attention(article=rfp_article, user=eo_user)

    if batch_publish:
        assert attention_condition == ""
    else:
        assert attention_condition == "Missing image and/or short description for social media"


@pytest.mark.parametrize("batch_publish", [True, False])
@pytest.mark.django_db
def test_article_requires_eo_attention_description_ok_but_missing_image(
    rfp_article: Article, eo_user, settings, batch_publish
):

    settings.WJS_JOURNALS_WITH_ENGLISH_CONTENT = [rfp_article.journal.code]

    assert rfp_article.title_en
    assert rfp_article.abstract_en
    assert not rfp_article.meta_image

    rfp_article.articleworkflow.social_media_short_description = "Placeholder"
    rfp_article.articleworkflow.save()

    params = rfp_article.issue.issueparameters
    params.batch_publish = batch_publish
    params.save()
    assert rfp_article.issue.issueparameters.batch_publish == batch_publish

    rpf_state_class = getattr(states, rfp_article.articleworkflow.state)
    attention_condition = rpf_state_class.article_requires_attention(article=rfp_article, user=eo_user)

    if batch_publish:
        assert attention_condition == ""
    else:
        assert attention_condition == "Missing image and/or short description for social media"


@pytest.mark.parametrize(
    ("title_en", "abstract_en", "expected_attention_condition"),
    (
        ("", "", True),
        ("Title placeholder", "", True),
        ("", "Abstract placeholder", True),
        ("Title placeholder", "Abstract placeholder", False),
    ),
)
@pytest.mark.django_db
def test_article_requires_eo_attention_for_missing_title_or_abstract(
    rfp_article: Article,
    eo_user,
    title_en: str,
    abstract_en: str,
    expected_attention_condition: bool,
    generate_image,
    settings,
):

    settings.WJS_JOURNALS_WITH_ENGLISH_CONTENT = [rfp_article.journal.code]

    rfp_article.title_en = title_en
    rfp_article.abstract_en = abstract_en
    rfp_article.save()

    rfp_article.meta_image = generate_image
    rfp_article.save()

    rfp_article.articleworkflow.social_media_short_description = "Placeholder"
    rfp_article.articleworkflow.save()

    rpf_state_class = getattr(states, rfp_article.articleworkflow.state)
    attention_condition = rpf_state_class.article_requires_attention(article=rfp_article, user=eo_user)

    if expected_attention_condition:
        assert attention_condition == "Missing English translation of title or abstract"


@pytest.mark.django_db
def test_article_requires_eo_attention_journal_without_social_media_files(rfp_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_WITH_SOCIAL_MEDIA_FILES = []

    assert not rfp_article.meta_image
    assert not rfp_article.articleworkflow.social_media_short_description

    params = rfp_article.issue.issueparameters
    params.batch_publish = False
    params.save()

    rpf_state_class = getattr(states, rfp_article.articleworkflow.state)
    attention_condition = rpf_state_class.article_requires_attention(article=rfp_article, user=eo_user)

    assert attention_condition == ""
