from submission import models as submission_models


def always_accept(article: submission_models.Article) -> bool:
    """Always accept the article."""
    return True


def always_reject(article: submission_models.Article) -> bool:
    """Always reject the article."""
    return False


def at_least_one_author(article: submission_models.Article) -> bool:
    """At least two authors."""
    return article.authors.count() >= 1
