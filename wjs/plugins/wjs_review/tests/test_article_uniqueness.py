import pytest
from plugins.wjs_submission.step5.forms import RevisionStep5Form, SubmissionStep5Form
from plugins.wjs_submission.unique_check import check_article_unique
from submission.models import Article


@pytest.mark.django_db
def test_unique_submission_for_submitted(article: Article, submitted_article: Article):
    """
    An unsubmitted article with the same metadata as submitted one, fails uniqueness validation.

    A submitted article with the same metadata as unsubmitted one, passes uniqueness validation.

    :param article: The original article instance used for testing uniqueness.
    :type article: Article
    :param submitted_article: The submitted article instance being tested for duplication.
    :type submitted_article: Article
    """
    submitted_article.title = article.title
    submitted_article.abstract = article.abstract
    submitted_article.save()
    response_content = {
        "arxiv_id": None,
        "title": article.title,
        "abstract": article.abstract,
    }
    assert not check_article_unique(response_content, article.journal, article.pk)
    assert check_article_unique(response_content, submitted_article.journal, submitted_article.pk)


@pytest.mark.django_db
def test_unique_submission_for_self(article: Article):
    """
    Article itself is not considered in uniqueness validation.

    :param article: The article instance to test for uniqueness.
    :type article: Article
    :return: None
    """
    response_content = {
        "arxiv_id": None,
        "title": article.title,
        "abstract": article.abstract,
    }
    assert check_article_unique(response_content, article.journal, article.pk)


@pytest.mark.parametrize(
    "revision",
    (True, False),
)
@pytest.mark.django_db
def test_unique_submission_form_for_self(article: Article, revision: bool):
    """
    Article itself is not considered in uniqueness validation when submitting a new article.

    :param article: The Article instance used to populate and validate the form
    :type article: Article
    :param revision: Whether the article is a revision or not
    :type revision: bool
    """
    data = {
        "title": article.title,
        "abstract": article.abstract,
        "language": article.language,
        "section": article.journal.section_set.first().pk,
        "license": article.journal.licence_set.first().pk,
    }
    if revision:
        form = RevisionStep5Form(step=5, journal=article.journal, data=data, instance=article)
    else:
        form = SubmissionStep5Form(step=5, journal=article.journal, data=data, instance=article)
    assert form.is_valid()


@pytest.mark.parametrize(
    "revision",
    (True, False),
)
@pytest.mark.django_db
def test_unique_submission_form_with_submitted(article: Article, submitted_article: Article, revision: bool):
    """
    If a submitted article exists, newly submitted article fails uniqueness check.

    :param article: The Article instance used to populate and validate the form
    :type article: Article
    :param submitted_article: The submitted article instance being tested for duplication.
    :type submitted_article: Article
    :param revision: Whether the article is a revision or not
    :type revision: bool
    """
    submitted_article.title = article.title
    submitted_article.abstract = article.abstract
    submitted_article.save()
    data = {
        "title": article.title,
        "abstract": article.abstract,
        "language": article.language,
        "section": article.journal.section_set.first().pk,
        "license": article.journal.licence_set.first().pk,
    }
    if revision:
        form = RevisionStep5Form(step=5, journal=article.journal, data=data, instance=article)
    else:
        form = SubmissionStep5Form(step=5, journal=article.journal, data=data, instance=article)
    assert not form.is_valid()


@pytest.mark.django_db
def test_unique_submission_form_for_submitted_article(article: Article, submitted_article: Article):
    """
    Submitted article is considered unique during revision, unsubmitted one is ignored.

    :param article: The Article instance used to populate and validate the form
    :type article: Article
    :param submitted_article: The submitted article instance being tested for duplication.
    :type submitted_article: Article
    """
    submitted_article.title = article.title
    submitted_article.abstract = article.abstract
    submitted_article.save()
    data = {
        "title": article.title,
        "abstract": article.abstract,
        "language": article.language,
        "section": article.journal.section_set.first().pk,
        "license": article.journal.licence_set.first().pk,
    }
    form = RevisionStep5Form(step=5, journal=submitted_article.journal, data=data, instance=submitted_article)
    assert form.is_valid()
