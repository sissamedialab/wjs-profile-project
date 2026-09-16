from submission import models as submission_models


def always_accept(article: submission_models.Article) -> bool:
    """Always accept the article."""
    return True


def always_reject(article: submission_models.Article) -> bool:
    """Always reject the article."""
    return False


def at_least_one_author(article: submission_models.Article) -> bool:
    """At least two authors."""
    return article.author_accounts.count() >= 1


def check_blacklisted_authors(article: submission_models.Article) -> bool:
    """Check for blacklisted authors and create an attention condition.

    This check is **non-blocking**: it always returns True so the
    submission/revision process proceeds normally. As a side effect,
    it creates (or resolves) a BLACKLISTED_AUTHOR attention condition
    for the EO role when one or more author emails are found in the
    global blacklist.

    The actual logic lives in :func:ac_service.evaluate_blacklisted_author
    so it can be shared between the submission check, the revision path,
    and the nightly AC rebuild.
    """
    from ..ac_service import evaluate_blacklisted_author

    evaluate_blacklisted_author(article)
    return True
