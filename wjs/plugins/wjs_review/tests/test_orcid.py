"""Tests related to UI, forms validations, and such."""

import pytest
from core.models import Country
from django.shortcuts import render
from django.test.client import Client
from django.urls import reverse
from identifiers import logic
from identifiers.models import Identifier
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

ORCIDS = [
    ("0000-0002-8324-7644", True),
    ("0000-0002-1694-233X", True),
    ("https://orcid.org/0000-0002-8324-7644", False),
    ("http://orcid.org/0000-0002-8324-7644", False),
]


@pytest.mark.parametrize(
    argnames="orcid,valid",
    argvalues=ORCIDS,
)
@pytest.mark.django_db
def test_orcid_input(
    client: Client,
    normal_user: JCOMProfile,
    country: Country,
    orcid: str,
    valid: bool,
):
    """
    Document that the orcid is saved as given by the user.

    I.e., if a user inputs "https://orcid.org/123-456"
    then  "https://orcid.org/123-456" is saved into the DB.

    This is a problem during DOI registration, because the
    domain part is added again.
    """
    user = normal_user.janeway_account  # an alias
    assert user.orcid is None
    url = reverse("core_edit_profile")
    client.force_login(normal_user)
    data = {
        "orcid": orcid,
        "edit_profile": True,  # needed by core.views.edit_profile
        # The following fields are all mandatory:
        "first_name": normal_user.first_name or "Something",
        "last_name": normal_user.last_name or "Something",
        "country": str(user.country.id) if user.country else str(country.id),
        "profession": normal_user.profession or "1",
        "gdpr_checkbox": "on",
    }
    response = client.post(path=url, data=data, follow=True)
    assert response.status_code == 200

    user.refresh_from_db()
    if valid:
        assert user.orcid == orcid
    else:
        assert user.orcid is None


@pytest.mark.parametrize(
    argnames="orcid,valid",
    argvalues=ORCIDS,
)
@pytest.mark.django_db
def test_doi_batch(
    article: Article,
    doi_identifier: Identifier,
    normal_user: JCOMProfile,
    orcid: str,
    valid: bool,
):
    """
    Test handling of the users' orcids when creating the registration deposit.

    Since we use both valid and invalid orcids (see :py:param: valid), this test verifies that orcids are fixed.
    """
    normal_user.janeway_account.orcid = orcid
    normal_user.janeway_account.save()
    article.authors.add(normal_user.janeway_account)
    article.snapshot_authors()
    identifier = doi_identifier(article)
    template_context = logic.create_crossref_doi_batch_context(
        article.journal,
        {identifier},
    )
    # Authors are rendered only if the article has been scheduled for publication.
    # Here we just "force" the flag. An alternative might be to really schedule the article.
    for cri in template_context["crossref_issues"]:
        for art in cri["articles"]:
            art["scheduled"] = True
    template = "common/identifiers/crossref_doi_batch.xml"
    deposit = render(None, template, template_context, content_type="application/xml")
    content = deposit.content.decode()
    assert normal_user.first_name in content
    if not orcid.startswith("http"):
        prefixed_orcid = f"https://orcid.org/{orcid}"
    else:
        prefixed_orcid = orcid.replace("http://", "https://")
    assert prefixed_orcid in content
