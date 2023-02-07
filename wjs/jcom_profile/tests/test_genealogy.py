"""Test the parent/children relations between articles.

In JCOM, Articles in section "commentary" can either be "introductory"
or "invited". The invited ones are said to be children of the
introductory one.

"""
import pytest

from wjs.jcom_profile.models import Genealogy


class TestGenealogyModel:
    """Test adding, removing, reordering relations."""

    @pytest.mark.django_db
    def test_add_and_delete(self, journal, article_factory):
        """Set one or more articles as children of another one."""
        parent = article_factory(title="I am the parent", journal=journal)
        c1 = article_factory(title="Child One", journal=journal)
        c2 = article_factory(title="Child Two", journal=journal)

        genealogy = Genealogy.objects.create(parent=parent)
        assert parent.genealogy == genealogy

        parent.genealogy.children.add(c1)
        parent.genealogy.children.add(c2)
        assert genealogy.children.count() == 2
        assert genealogy.children.first() == c1

        c1.delete()
        assert genealogy.children.count() == 1
        assert genealogy.children.first() == c2

        c3 = article_factory(title="Child Three", journal=journal)
        parent.genealogy.children.add(c3)
        assert genealogy.children.last() == c3
