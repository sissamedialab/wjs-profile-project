"""Test the parent/children relations between articles.

In JCOM, Articles in section "commentary" can either be "introductory"
or "invited". The invited ones are said to be children of the
introductory one.

"""
import pytest
from conftest import yesterday

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


@pytest.fixture
def related_and_not_related_articles(
    journal,
    article_factory,
    fb_issue,
    account_factory,
    section_factory,
    keyword_factory,
):
    """Setup a journal with related and non-related articles."""
    author_a = account_factory()
    a = article_factory(
        title="Lone wolf",
        abstract="Lonewolfabstract",
        journal=journal,
        date_published=yesterday,
        stage="Published",
        correspondence_author=author_a,
        section=section_factory(),
    )
    a.keywords.add(keyword_factory())
    a.authors.add(author_a)
    a.snapshot_authors()
    author_p = account_factory()
    p = article_factory(
        title="Parent",
        abstract="Parentabstract",
        journal=journal,
        date_published=yesterday,
        stage="Published",
        correspondence_author=author_p,
        section=section_factory(),
    )
    p.keywords.add(keyword_factory())
    p.authors.add(author_p)
    p.snapshot_authors()
    author_c = account_factory()
    c = article_factory(
        title="Children",
        abstract="Childrenabstract",
        journal=journal,
        date_published=yesterday,
        stage="Published",
        correspondence_author=author_c,
        section=section_factory(),
    )
    c.keywords.add(keyword_factory())
    c.authors.add(author_c)
    c.snapshot_authors()
    genealogy = Genealogy.objects.create(parent=p)
    genealogy.children.add(c)
    fb_issue.journal = journal
    fb_issue.articles.add(a)
    fb_issue.articles.add(p)
    fb_issue.articles.add(c)
    fb_issue.save()
    return (a, p, c)


@pytest.mark.django_db
class TestChildrenExclusion:
    """Views listing articles shoule hide papers that are "children" of other papers.

    Here we test that:
    - general articles listing excludes children (e.g. https://jcom-test.sissamedialab.it/articles/)
    - specific issue listing excludes children; (e.g. https://jcom-test.sissamedialab.it/issue/192/info/)
    - search results do **not** exclude children;
          e.g. https://jcom-test.sissamedialab.it/search/?article_search=What+is+‘‘science+communication’’&sort=title
    - filters "by-author", "by-section", "by-keyword" do **not** exclude children
    """

    def test_articles(self, related_and_not_related_articles, client):
        """Articles listing excludes children."""
        article, parent, child = related_and_not_related_articles
        # View's URL
        url = f"/{article.journal.code}/articles/"
        # ...or I could also do:
        # * client.get(f"/{article.journal.code}/")
        # * url = reverse("journal_articles")
        response = client.get(url)
        content = response.content.decode()
        assert article.title in content
        assert parent.title in content
        assert child.title in content
        assert article.abstract in content
        assert parent.abstract in content
        assert child.abstract not in content  # ⇦ child's abstract NOT IN content

    def test_issue(self, related_and_not_related_articles, client):
        """Issue lising excludes children."""
        article, parent, child = related_and_not_related_articles
        issue_id = article.issues.first().id
        url = f"/{article.journal.code}/issue/{issue_id}/info/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title in content
        assert parent.title in content
        assert child.title in content
        assert article.abstract in content
        assert parent.abstract in content
        assert child.abstract not in content

    def test_search(self, related_and_not_related_articles, client):
        """Search results do **not** exclude children."""
        article, parent, child = related_and_not_related_articles
        url = f"/{article.journal.code}/search/"

        response = client.get(url, {"article_search": article.title})
        content = response.content.decode()
        assert article.title in content
        assert parent.title not in content
        assert child.title not in content

        response = client.get(url, {"article_search": parent.title})
        content = response.content.decode()
        assert article.title not in content
        assert parent.title in content
        assert child.title in content
        assert child.abstract not in content

        response = client.get(url, {"article_search": child.title})
        content = response.content.decode()
        assert article.title not in content
        assert parent.title not in content
        assert child.title in content
        assert child.abstract in content

    def test_filter_by_author(self, related_and_not_related_articles, client):
        """Filter by author do **not** exclude children."""
        article, parent, child = related_and_not_related_articles
        journal_code = article.journal.code

        url = f"/{journal_code}/articles/author/{article.correspondence_author.id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title in content
        assert parent.title not in content
        assert child.title not in content

        url = f"/{journal_code}/articles/author/{parent.correspondence_author.id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title not in content
        assert parent.title in content
        assert parent.abstract in content
        assert child.title in content
        assert child.abstract not in content

        url = f"/{journal_code}/articles/author/{child.correspondence_author.id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title not in content
        assert parent.title not in content
        assert child.title in content
        assert child.abstract in content

    def test_filter_by_section(self, related_and_not_related_articles, client):
        """Filter by section do **not** exclude children."""
        article, parent, child = related_and_not_related_articles
        journal_code = article.journal.code

        url = f"/{journal_code}/articles/section/{article.section.id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title in content
        assert parent.title not in content
        assert child.title not in content

        url = f"/{journal_code}/articles/section/{parent.section.id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title not in content
        assert parent.title in content
        assert parent.abstract in content
        assert child.title in content
        assert child.abstract not in content

        url = f"/{journal_code}/articles/section/{child.section.id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title not in content
        assert parent.title not in content
        assert child.title in content
        assert child.abstract in content

    def test_filter_by_keyword(self, related_and_not_related_articles, client):
        """Filter by keyword do **not** exclude children."""
        article, parent, child = related_and_not_related_articles
        journal_code = article.journal.code

        url = f"/{journal_code}/articles/keyword/{article.keywords.first().id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title in content
        assert parent.title not in content
        assert child.title not in content

        url = f"/{journal_code}/articles/keyword/{parent.keywords.first().id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title not in content
        assert parent.title in content
        assert parent.abstract in content
        assert child.title in content
        assert child.abstract not in content

        url = f"/{journal_code}/articles/keyword/{child.keywords.first().id}/"
        response = client.get(url)
        content = response.content.decode()
        assert article.title not in content
        assert parent.title not in content
        assert child.title in content
        assert child.abstract in content
