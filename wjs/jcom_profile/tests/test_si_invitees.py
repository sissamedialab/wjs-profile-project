"""Tests related to the submission process."""
import lxml.html
import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from wjs.jcom_profile.factories import SpecialIssueFactory


class TestSIInvitees:
    """Tests related to Special Issues' invitees.

    Considering only open SIs, we have:
    - [x] si without invitees ⇨ si step is showing

    - si with invitees
      - [x] I'm out ⇨ si step is not showing
      - [x] I'm in ⇨ si step is showing

    - [x] all three together: multiple SIs, one without invitees, one with
      invitees but I'm in, and one with invitees and I'm in
      ⇨ si step is showing only for first and last

    """

    @pytest.mark.django_db
    def test_no_invitees(
        self,
        admin,
        article,
        open_special_issue,
    ):
        """Test that the SI-choosing page is showing when the open SI has no invitees."""
        client = Client()
        client.force_login(admin)

        assert not open_special_issue.invitees.exists()

        # visit the correct page
        url = reverse("submit_info", args=(article.pk,))
        response = client.get(url)
        assert response.status_code == 200

        # The page I'm visiting is the one that lets me choose the SI
        html = lxml.html.fromstring(response.content.decode())
        assert html.xpath(".//h1[text()='Submission Destination']")

        # The SI is among the choices
        # NB: don't just `assert <Element...>`: elements are False if they don't have children
        assert html.find(f".//input[@value='{open_special_issue.id}']") is not None

    @pytest.mark.django_db
    def test_has_invitees_but_i_am_out(
        self,
        admin,
        article,
        open_special_issue,
        existing_user,
    ):
        """Test that the SI-choosing page just redirects when I'm not invited to any open SI."""
        client = Client()
        client.force_login(admin)

        # Ensure that the si has some invitees,
        # and that I'm not between the invitees
        open_special_issue.invitees.set(
            [
                existing_user,
            ],
        )
        open_special_issue.save()
        assert existing_user.janeway_account in open_special_issue.invitees.all()
        assert admin not in open_special_issue.invitees.all()

        # visit the correct page
        url = reverse("submit_info", args=(article.pk,))
        response = client.get(url)
        assert response.status_code == 302
        assert response.url == reverse(
            "submit_info_original",
            args=(article.pk,),
        )

    @pytest.mark.django_db
    def test_has_invitees_and_i_am_in(
        self,
        admin,
        article,
        open_special_issue,
    ):
        """Test that the SI-choosing page is showing when I'm in the list of invitees."""
        client = Client()
        client.force_login(admin)

        # Ensure that the si has some invitees,
        # and that I'm among them
        open_special_issue.invitees.set(
            [
                admin,
            ],
        )
        open_special_issue.save()
        assert admin.janeway_account in open_special_issue.invitees.all()

        # visit the correct page
        url = reverse("submit_info", args=(article.pk,))
        response = client.get(url)
        assert response.status_code == 200

        # The page I'm visiting is the one that lets me choose the SI
        html = lxml.html.fromstring(response.content.decode())
        assert html.xpath(".//h1[text()='Submission Destination']")

        # The SI is among the choices
        assert html.find(f".//input[@value='{open_special_issue.id}']") is not None

    @pytest.mark.django_db
    def test_all_three_cases(
        self,
        admin,
        article,
        existing_user,
    ):
        """Test that the SI-choosing page is showing as expected.

        Expectations are:
        - no invitees
        - I'm invited
        Not showing otherwise.
        """
        client = Client()
        client.force_login(admin)

        yesterday = timezone.now() - timezone.timedelta(1)
        si_no_invitees = SpecialIssueFactory(open_date=yesterday)
        si_no_invitees.invitees.clear()

        # Korpiklaani
        si_vodka = SpecialIssueFactory(open_date=yesterday)
        si_vodka.invitees.set([admin])

        # Elio e le storie tese
        si_lafestadellemedie = SpecialIssueFactory(open_date=yesterday)
        si_lafestadellemedie.invitees.set([existing_user])

        assert admin.janeway_account in si_vodka.invitees.all()
        assert admin.janeway_account not in si_lafestadellemedie.invitees.all()

        # visit the correct page
        url = reverse("submit_info", args=(article.pk,))
        response = client.get(url)
        assert response.status_code == 200

        # The page I'm visiting is the one that lets me choose the SI
        html = lxml.html.fromstring(response.content.decode())
        assert html.xpath(".//h1[text()='Submission Destination']")

        # I can see the expected SIs
        assert html.find(f".//input[@value='{si_no_invitees.id}']") is not None
        assert html.find(f".//input[@value='{si_vodka.id}']") is not None
        assert len(html.findall(".//input[@name='special_issue']")) == 3  # 2 SIs + normal submission
