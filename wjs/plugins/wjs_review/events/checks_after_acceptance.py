"""Libray or functions that can be run on a just-accepted article.

They should verify if the paper might have issues that would prevent a typesetter from taking it in charge.

"""
from submission import models as submission_models


# TODO: might want to refactor with checks.always_assign(), but I need a stub here
# see specs#684
def always_pass(article: submission_models.Article) -> bool:
    """Do not perform any check."""
    return True
