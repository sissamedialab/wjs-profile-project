"""Test related to the submission process."""

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.test.client import Client
from django.urls import reverse
from submission.models import Article, FrozenAuthor, Keyword, Licence, Section

from wjs.jcom_profile.forms import KeywordSelectionArticleInfoSubmit
from wjs.jcom_profile.models import JCOMProfile, WjsSimpleBleach

from ..forms import EditorRevisionRequestForm

Account = get_user_model()

# --- Tabella casi: input -> output atteso ---
BLEACH_CASES = [
    ('Questo testo è tutto "ammmesso" anche le ".', 'Questo testo è tutto "ammmesso" anche le ".'),
    ("S&T coverage in English-language Indian dailies", "S&amp;T coverage in English-language Indian dailies"),
    ("<iframe>TEST with & H1</iframe>", "TEST with &amp; H1"),
    (
        """Fake form<form action="https://attacker.example.com" method="POST"><input type="text" name="username" /><input type="submit" value="Invia" /></form>""",  # noqa E501
        "Fake form",
    ),
    ("""fake image <img src="https://attacker.example.com/track.png" alt="Immagine" />""", "fake image "),
    ("""Fake script <script>alert('XSS')</script>""", "Fake script alert('XSS')"),
    ("&<>", "&amp;&lt;&gt;"),
    ("<!--Try to remove me!-->&<><!--End-->", "&amp;&lt;&gt;"),
]


@pytest.mark.skip("Old submission is not used anymore")
@pytest.mark.django_db
def test_create_coauthor_during_submission(
    article: Article,
    normal_user: JCOMProfile,
    client: Client,
):
    """New users created as co-authors are not active."""
    # Janeway decides whether to create a new Account if the given email cannot be found in the DB
    new_email = "mc1@invalid.com"
    assert not Account.objects.filter(email=new_email).exists()

    # The creation of the new co-author Account is performed by the view.
    # This was the easiest way I was able to find to simulate the process...

    # Janeway decorator article_edit_user_required() checks for article owner:
    article.correspondence_author = normal_user.janeway_account
    article.owner = normal_user.janeway_account
    # Janeway decorator article_is_not_submitted() checks for the submitted date:
    article.date_submitted = None
    # submit_authors view checks the article's current submission step:
    article.current_step = 3
    article.save()
    url = reverse("submit_authors", kwargs={"article_id": article.pk})
    client.force_login(normal_user)
    response = client.post(
        path=url,
        data={
            "first_name": "Marco",
            "last_name": "Caco",
            "frozen_email": new_email,
            "add_author": "",  # ⇦ this triggers the creation of new Account!
        },
    )
    # When a new co-author is created and added to the article the page is rendered again and success message is
    # added to the messages list.
    assert response.status_code == 200
    messages = list(response.context["messages"])
    assert f"Marco Caco ({new_email}) added to the article." == messages[0].message
    # Here is the interesting part:
    # a new Account has been created
    # but it's not active (this is a problem)
    with pytest.raises(Account.DoesNotExist):
        assert Account.objects.get(email=new_email)
    assert FrozenAuthor.objects.filter(frozen_email=new_email).exists()


@pytest.mark.parametrize(("string_with_tag", "bleached_string"), BLEACH_CASES)
def test_bleach_title_param(string_with_tag, bleached_string):
    """Test a html string into title with some special chars"""

    class TestBleach(forms.Form):
        title = WjsSimpleBleach()

    form = TestBleach(data={"title": string_with_tag})
    assert form.is_valid()
    title = form.cleaned_data["title"]
    assert title == bleached_string


@pytest.mark.skip("Old submission is not used anymore")
@pytest.mark.parametrize(("string_with_tag", "bleached_string"), BLEACH_CASES)
@pytest.mark.django_db
def test_submission_form(sections, article, journal, string_with_tag, bleached_string):
    keyword = Keyword.objects.filter(journal=journal).first()
    licence = Licence.objects.filter(journal=journal).first()
    section = Section.objects.filter(journal=journal).first()

    form = KeywordSelectionArticleInfoSubmit(
        data={
            "title": string_with_tag,
            "abstract": "test",
            "section": section,
            "license": licence,
            "keywords": [keyword],
        },
        journal=journal,
        instance=article,
    )
    assert form.is_valid()
    title = form.cleaned_data["title"]
    assert title == bleached_string
    instance = form.save()
    assert instance.title == bleached_string


@pytest.mark.parametrize(("string_with_tag", "bleached_string"), BLEACH_CASES)
@pytest.mark.django_db
def test_revision_form(editor_revision, string_with_tag, bleached_string):

    form = EditorRevisionRequestForm(data={"title": string_with_tag, "abstract": "test"}, instance=editor_revision)
    assert form.is_valid()
    title = form.cleaned_data["title"]
    assert title == bleached_string
    instance = form.save()
    assert instance.title == bleached_string
