"""Tests related to the submission process."""
import lxml.html
import pytest
from core.middleware import SiteSettingsMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from submission import logic
from submission.models import Article, Section
from utils import setting_handler

from wjs.jcom_profile import views
from wjs.jcom_profile.models import SpecialIssue


class TestFilesStage:
    """Tests related to the file-submission stage."""

    @pytest.mark.django_db
    def test_additional_files_form_title_obeys_setting(self, roles, journal, jcom_user):
        """The title of the additional files field should obey its setting."""
        # TODO: flip-flapping when the order of the tests change!!!
        # set the setting
        value = "<h2>Qui ci metto un po' <strong>di</strong> tutto</h2>"
        setting_handler.save_setting(
            "styling",
            "submission_figures_data_title",
            journal=journal,
            value=value,
        )

        client = Client()

        # start a submission
        article = Article.objects.create(
            journal=journal,
            title="A title",
            current_step=3,
            owner=jcom_user.janeway_account,
            correspondence_author=jcom_user.janeway_account,
        )
        # for the value of "step", see submission.models.Article::step_to_url
        # Magic here ⮧ (see utils/install/roles.json)
        logic.add_user_as_author(user=jcom_user.janeway_account, article=article)

        # visit the correct page
        client.force_login(jcom_user.janeway_account)
        url = f"/{journal.code}/submit/{article.pk}/files/"
        response = client.get(url)
        # I'm expecting an "OK" response, not a redirect to /login or
        # /profile (e.g. for the gdpr checkbox)
        assert response.status_code == 200

        # check that the setting's value is there
        assert value in response.content.decode()

        # double check
        new_value = "ciao 🤞"
        setting_handler.save_setting(
            "styling",
            "submission_figures_data_title",
            journal=journal,
            value=new_value,
        )
        # django tests and cache; a bit unexpected:
        # https://til.codeinthehole.com/posts/django-doesnt-flush-caches-between-tests/
        cache.clear()  # 🠄 Important!
        response = client.get(url)
        assert new_value in response.content.decode()

    @pytest.mark.xfail
    @pytest.mark.django_db
    def test_admin_cannot_login(self, journal, admin):
        """Background study.

        Sembra che l'account admin (dalla fixture conftest.admin) non
        riesca ad autenticarsi...

        """
        client = Client()
        admin.jcomprofile.gdpr_checkbox = True
        admin.jcomprofile.save()
        client.force_login(admin)
        response = client.get("/")
        ru = response.wsgi_request.user
        assert ru is not None
        assert ru.is_authenticated


class TestSIStage:
    """Tests related to the "info" stage."""

    @pytest.mark.django_db
    def test_choose_si_skipped_when_no_si_exist(self, admin, article):
        """Test that the SI-choosing page just redirects if there are no SIs."""
        client = Client()
        client.force_login(admin)
        # visit the correct page
        url = reverse("submit_info", args=(article.pk,))
        response = client.get(url)
        assert response.status_code == 302
        assert response.url == reverse(
            "submit_info_original",
            args=(article.pk,),
        )

    @pytest.mark.django_db
    def test_choose_si_skipped_when_no_open_si(self, admin, article):
        """Test that the SI-choosing page just redirects if there are
        SIs with open date in the future and no close date."""
        client = Client()
        client.force_login(admin)
        tomorrow = timezone.now() + timezone.timedelta(1)
        SpecialIssue.objects.create(name="Test SI", journal=article.journal, open_date=tomorrow)
        assert not SpecialIssue.objects.open_for_submission().exists()
        url = reverse("submit_info", args=(article.pk,))
        response = client.get(url)
        assert response.status_code == 302
        assert response.url == reverse("submit_info_original", args=(article.pk,))

    @pytest.mark.django_db
    def test_choose_si_shown_when_si_open(self, admin, article):
        """Test that the SI-choosing page is shown if there are SIs
        with open date in the past and no close date."""
        client = Client()
        client.force_login(admin)
        yesterday = timezone.now() - timezone.timedelta(1)
        SpecialIssue.objects.create(name="Test SI", journal=article.journal, open_date=yesterday)
        assert SpecialIssue.objects.open_for_submission().exists()
        # visit the correct page
        url = f"/{article.journal.code}/submit/{article.pk}/info/"
        response = client.get(url)

        assert response.status_code == 200
        targets = (
            "<h1>Submission Destination",
            "Choose Submission Destination",
        )
        content = response.content.decode()
        for target in targets:
            assert target in content

    @pytest.mark.django_db
    def test_choose_si_shown_when_si_open_and_not_yet_closed(self, admin, article):
        """Test that the SI-choosing page is shown if there are SIs
        with open date in the past and close date in the future."""
        client = Client()
        client.force_login(admin)
        yesterday = timezone.now() - timezone.timedelta(1)
        tomorrow = timezone.now() + timezone.timedelta(1)
        SpecialIssue.objects.create(name="Test SI", journal=article.journal, open_date=yesterday, close_date=tomorrow)
        assert SpecialIssue.objects.open_for_submission().exists()
        # visit the correct page
        url = f"/{article.journal.code}/submit/{article.pk}/info/"
        response = client.get(url)

        assert response.status_code == 200
        targets = (
            "<h1>Submission Destination",
            "Choose Submission Destination",
        )
        content = response.content.decode()
        for target in targets:
            assert target in content


@pytest.fixture
def journal_with_three_sections(journal):
    """Set three sections to a journal.

    Two "public" (article and letter) and one not "public" (editorial).
    """
    # All journals automatically get a section, so there is no need to
    # Section.objects.create(name="Article",...
    Section.objects.create(name="Letter", sequence=10, journal=journal, public_submissions=True).save()
    Section.objects.create(name="Editorial", sequence=10, journal=journal, public_submissions=False).save()
    journal.save()
    return journal


@pytest.fixture
def special_issue_with_all_sections(journal_with_three_sections):
    """Make a special issue that allows all journal's "section"s."""
    sections = journal_with_three_sections.section_set.all()
    yesterday = timezone.now() - timezone.timedelta(1)
    special_issue = SpecialIssue.objects.create(
        journal=journal_with_three_sections,
        name="Special Issue One Section",
        description="SIONE description",
        short_name="SIONE",
        open_date=yesterday,
    )
    special_issue.allowed_sections = sections
    return special_issue


@pytest.fixture
def special_issue_with_two_sections(journal_with_three_sections):
    """Make a special issue with two "sections".

    One "public" (article) and one not "public" (editorial)."""
    sections = (
        Section.objects.get(name="Article", journal=journal_with_three_sections, public_submissions=True),
        Section.objects.get(name="Editorial", journal=journal_with_three_sections, public_submissions=False),
    )
    yesterday = timezone.now() - timezone.timedelta(1)
    special_issue = SpecialIssue.objects.create(
        journal=journal_with_three_sections,
        name="Special Issue Two Sections",
        description="SITWO description",
        short_name="SITWO",
        open_date=yesterday,
    )
    special_issue.allowed_sections = sections
    special_issue.save()
    return special_issue


class TestInfoStage:
    """Test which section choices are presented to the author.

    Possibilities:
    - no SI has been choosen (normal submission)
      - [x] if manager/editor: all sections
      - [x] if not manager/editor: only "public" sections
    - SI has been choosen
      - SI allows all sections
        - [ ] if manager/editor: same as "no SI"
        - [x] if not manager/editor: same as "no SI"
      - SI allows subset of journal's sections
        - [x] if manager/editor: all sections allowed by SI
        - [x] if not manager/editor: only "public" sections allowed by SI
        - [-] in any case: no section that is not allowed by the SI
    """

    @pytest.mark.django_db
    def test_no_si_and_manager_submitting(self, rf, admin, journal_with_three_sections, article_factory):
        """When no SI has been choosen, a manager sees all sections."""
        # create an article owned by the user that will do the request (admin)
        article = article_factory.create(journal=journal_with_three_sections, owner=admin)

        url = reverse("submit_info", args=(article.pk,))
        request = rf.get(url)
        self.simulate_middleware(request, user=admin, journal=journal_with_three_sections)

        # NB: do NOT use unnamed args as in ...submit_info(request, article.id)!!!
        response = views.submit_info(request, article_id=article.id)
        assert response.status_code == 200
        got = self.sections_in_the_form(response)

        journal_sections = journal_with_three_sections.section_set.all()
        self.compare(got=got, expected=journal_sections)

    @pytest.mark.django_db
    def test_no_si_and_author_submitting(self, rf, coauthor, journal_with_three_sections, fb_article):
        """When no SI has been choosen, a normal author (i.e. not manager) sees only public sections."""
        # add role "Author" to user coauthor (cannot move to fixture,
        # because roles are related to a journal)
        from core.models import AccountRole, Role

        author_role = Role.objects.create(name="Author", slug="author")
        coauthor.add_account_role(author_role.slug, journal_with_three_sections)
        print(AccountRole.objects.filter(journal=journal_with_three_sections))

        # create an article owned by the user that will do the request (admin)
        #
        # (trying with the article object fixture generated by the
        # registration of the ArticleFactory: when customizing many
        # attributes it may be better to use the factory)
        fb_article.journal = journal_with_three_sections
        # WARNING: the fixture "coauthor" returns a JCOMProfile
        # object, which is different from the article.owner (which is
        # a core.Account) when checked by Article.can_edit (l.1289)
        fb_article.owner = coauthor.janeway_account
        fb_article.save()

        url = reverse("submit_info", args=(fb_article.pk,))
        request = rf.get(url)
        self.simulate_middleware(request, user=coauthor.janeway_account, journal=journal_with_three_sections)

        response = views.submit_info(request, article_id=fb_article.id)
        assert response.status_code == 200
        got = self.sections_in_the_form(response)

        # expect all journal sections + the empty label
        journal_public_sections = journal_with_three_sections.section_set.filter(public_submissions=True)
        self.compare(got=got, expected=journal_public_sections)

    @pytest.mark.django_db
    def test_si_with_no_limits_and_author_submitting(
        self,
        rf,
        coauthor,
        journal_with_three_sections,
        article_factory,
        special_issue_with_all_sections,
    ):
        """The choosen SI allows all sections; a normal user sees all the public sections."""
        # create an article owned by the user that will do the request (coauthor)
        article = article_factory.create(
            journal=journal_with_three_sections,
            owner=coauthor.janeway_account,
        )
        article.articlewrapper.special_issue = special_issue_with_all_sections
        article.articlewrapper.save()

        url = reverse("submit_info", args=(article.pk,))
        request = rf.get(url)
        self.simulate_middleware(request, user=coauthor.janeway_account, journal=journal_with_three_sections)

        response = views.submit_info(request, article_id=article.id)
        assert response.status_code == 200
        got = self.sections_in_the_form(response)

        # double check: si's sections must be the same as the journal's sections
        assert (
            len(
                set(article.articlewrapper.special_issue.allowed_sections.all())
                - set(journal_with_three_sections.section_set.all()),
            )
            == 0
        )

        # expect only si's public sections + the empty label
        si_public_sections = special_issue_with_all_sections.allowed_sections.filter(public_submissions=True)
        self.compare(got=got, expected=si_public_sections)

    @pytest.mark.django_db
    def test_si_with_limited_sections_and_manager_submitting(
        self,
        rf,
        admin,
        journal_with_three_sections,
        article_factory,
        special_issue_with_two_sections,
    ):
        """When the SI limits the possible sections, a manager sees all sections in the subset."""
        # create an article owned by the user that will do the request (coauthor)
        article = article_factory.create(
            journal=journal_with_three_sections,
            owner=admin,
        )
        article.articlewrapper.special_issue = special_issue_with_two_sections
        article.articlewrapper.save()

        url = reverse("submit_info", args=(article.pk,))
        request = rf.get(url)
        self.simulate_middleware(request, user=admin, journal=journal_with_three_sections)

        response = views.submit_info(request, article_id=article.id)
        assert response.status_code == 200
        got = self.sections_in_the_form(response)

        # expect all si's sections + the empty label
        si_sections = special_issue_with_two_sections.allowed_sections.all()
        self.compare(got=got, expected=si_sections)

    @pytest.mark.django_db
    def test_si_with_limited_sections_and_author_submitting(
        self,
        rf,
        coauthor,
        journal_with_three_sections,
        article_factory,
        special_issue_with_two_sections,
    ):
        """When the SI limits the possible sections, a normal user sees only the public sections in the subset."""
        # create an article owned by the user that will do the request (coauthor)
        article = article_factory.create(
            journal=journal_with_three_sections,
            owner=coauthor.janeway_account,
        )
        article.articlewrapper.special_issue = special_issue_with_two_sections
        article.articlewrapper.save()

        url = reverse("submit_info", args=(article.pk,))
        request = rf.get(url)
        self.simulate_middleware(request, user=coauthor.janeway_account, journal=journal_with_three_sections)

        response = views.submit_info(request, article_id=article.id)
        assert response.status_code == 200
        got = self.sections_in_the_form(response)

        # expect only si's public sections + the empty label
        si_public_sections = special_issue_with_two_sections.allowed_sections.filter(public_submissions=True)
        self.compare(got=got, expected=si_public_sections)

    def simulate_middleware(self, request, **kwargs):
        """Simulate Janeway's middleware."""
        # simulate login
        request.user = kwargs["user"]
        # simulate session middleware (it is needed because the
        # template of the response uses the templatetag
        # "hijack_notification")
        SessionMiddleware().process_request(request)
        # simulate J. middleware
        request.journal = kwargs["journal"]
        SiteSettingsMiddleware.process_request(request)
        # https://youtu.be/vZraNnWnYXE?t=10

    def sections_in_the_form(self, response):
        """Extract the options of the section select tag."""
        html = lxml.html.fromstring(response.content.decode())
        sections_select = html.get_element_by_id(id="id_section")
        allowed_sections_as_select_options = sections_select.findall("option")
        return allowed_sections_as_select_options

    def compare(self, got=None, expected=None):
        """Compare a bunch of <select> <option>s to a list of submission.Section."""
        # expect to find all the expected sections + the empty label
        assert len(got) == len(expected) + 1

        texts = [e.text for e in got]
        values = [e.attrib.get("value") for e in got]
        for section in expected:
            assert str(section.id) in values
            section_text = str(section)
            assert section_text in texts


@pytest.mark.django_db
def test_normal_issue_article_show_normal_issue_type_in_article_info(admin, article, coauthors_setting):
    client = Client()
    client.force_login(admin)
    url = reverse("submit_review", args=(article.pk,))

    response = client.get(url)
    assert response.status_code == 200

    html = lxml.html.fromstring(response.content.decode())
    article_info_table = html.get_element_by_id(id="article-info-table")
    assert "Normal Issue" in [
        td.text for td in (e.find("td") for e in article_info_table.findall("tr")) if td is not None
    ]


@pytest.mark.django_db
def test_special_issue_article_show_issue_name_in_article_info(admin, article, coauthors_setting, special_issue):
    client = Client()
    client.force_login(admin)
    # TODO: Rework this using speical_issue fixture from #84!

    url = reverse("submit_review", args=(article.pk,))

    response = client.get(url)
    assert response.status_code == 200

    html = lxml.html.fromstring(response.content.decode())
    article_info_table = html.get_element_by_id(id="article-info-table")
    assert special_issue.name in [
        td.text for td in (e.find("td") for e in article_info_table.findall("tr")) if td is not None
    ]
