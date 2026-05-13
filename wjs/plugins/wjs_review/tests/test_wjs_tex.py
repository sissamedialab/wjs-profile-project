import pytest
from journal.models import Journal
from plugins.wjs_submission.models import (
    ArticleCollaboration,
    Collaboration,
    CollaborationRelation,
)
from submission.models import Article

from ..templatetags.wjs_tex import collaborations


@pytest.mark.django_db
def test_collaborations_filter_returns_only_by_relation(journal: Journal):
    """
    Test that collaborations() returns only names of collabs linked with the "by" relation.

    We create these articles/relations:
    - a1 - no relation to any collab
    - a2 - related to collab c1 with collaborationrelation "by"
    - a3 - related to collab c2 with "on-behalf"
    - a4 - related to collab c3 and c4 with "by" and to c5 with "on-behalf"
    """
    c1 = Collaboration.objects.create(name="c1", creator=None)
    c2 = Collaboration.objects.create(name="c2", creator=None)
    c3 = Collaboration.objects.create(name="c3", creator=None)
    c4 = Collaboration.objects.create(name="c4", creator=None)
    c5 = Collaboration.objects.create(name="c5", creator=None)

    a1 = Article.objects.create(title="a1", journal=journal)
    a2 = Article.objects.create(title="a2", journal=journal)
    a3 = Article.objects.create(title="a3", journal=journal)
    a4 = Article.objects.create(title="a4", journal=journal)

    ArticleCollaboration.objects.create(article=a2, collaboration=c1, relation=CollaborationRelation.BY)
    ArticleCollaboration.objects.create(article=a3, collaboration=c2, relation=CollaborationRelation.ON_BEHALF_OF)
    ArticleCollaboration.objects.create(article=a4, collaboration=c3, relation=CollaborationRelation.BY)
    ArticleCollaboration.objects.create(article=a4, collaboration=c4, relation=CollaborationRelation.BY)
    ArticleCollaboration.objects.create(article=a4, collaboration=c5, relation=CollaborationRelation.ON_BEHALF_OF)

    assert set(collaborations(a1)) == set()
    assert set(collaborations(a2)) == {"c1"}
    assert set(collaborations(a3)) == set()
    assert set(collaborations(a4)) == {"c3", "c4"}
