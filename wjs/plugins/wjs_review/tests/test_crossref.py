"""Tests related to the XML deposit for Crossref's DOI registration."""

import pytest
from django.test import override_settings
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from identifiers.models import CrossrefDeposit, Identifier
from submission.models import STAGE_PUBLISHED, Article

from wjs.jcom_profile.models import JCOMProfile


@pytest.mark.parametrize(
    ("selected_lang", "en_metadata_exists", "en_metadata_expected_in_deposit"),
    [
        # Common case: deposit created in English "context" with English metadata:
        ("en", True, True),
        # Also common case: deposit created in English "context", but Enlish metadata missing
        # (until March 2025, in JCOMAL, at the time of DOI registration, we had the pt/es title, but not the en title):
        ("en", False, False),
        # Deposit created in Portuguese "context" with English metadata
        # Here Janeway does not behave "correctly": it uses the metadata in the selected-language (if available)
        # This is not a real problem, because publication and DOI registration are always performed by EO
        # in an English context (and, if all goes wrong, we can re-send the metadata to CR at libitum).
        pytest.param("pt", True, True, marks=pytest.mark.xfail(reason="Using selected-lang during rendering.")),
        # Deposit created in Portuguese "context" and English metadata missing:
        ("pt", False, False),
    ],
)
@pytest.mark.django_db
def test_always_en(
    *,
    selected_lang: bool,
    en_metadata_exists: bool,
    en_metadata_expected_in_deposit: bool,
    article: Article,
    client: Client,
    admin: JCOMProfile,
) -> None:
    """
    Verify that the CrossRef deposit always uses English metadta.

    We want to verify that, if available, we always use the English metadata, independently of the currently selected
    language. The system should fall-back to the translation-language metadata only if the English metadata is missing.

    """
    en_title = "In Defense of Eating Pizza With a Fork and Knife"
    pt_title = "Em Defesa de Comer Pizza com Garfo e Faca"
    article.title = en_title
    article.title_pt = pt_title

    # We need to mark the article as scheduled-for-publication,
    # because the template renders the title only in that case.
    article.stage = STAGE_PUBLISHED
    article.date_published = timezone.now()
    article.save()
    doi_identifier = Identifier.objects.create(
        id_type="doi",
        identifier="123",
        article=article,
    )
    url = reverse(
        "show_doi",
        kwargs={
            "article_id": article.id,
            "identifier_id": doi_identifier.id,
        },
    )
    client.force_login(admin)

    # small sanity check
    assert article.title_en == article.title
    assert article.scheduled_for_publication

    # If the deposit is missing (CrossrefStatus.CrossrefDeposit.document)
    # it is created (i.e. the deposit template is rendered), but not saved
    # (see the end of identifiers.views.show_doi).
    # This probably makes sense, becuase when hitting "show_doi" we don't send anything to CR.
    #
    # Here I'm making sure that no such object exists, so that the view will re-render the deposit-template everytime
    # it's called.
    assert not CrossrefDeposit.objects.exists()

    if en_metadata_exists:
        article.title = en_title
        article.title_en = en_title
        article.title_pt = pt_title
        article.save()
    else:
        article.title = pt_title
        article.title_en = ""
        article.title_pt = pt_title
        article.save()

        # NB: for some reason, when setting the English title empty, the Portuguese version is used.
        article.refresh_from_db()
        assert article.title_en == pt_title

    with override_settings(LANGUAGE_CODE=selected_lang):
        response = client.get(url)
        response_content = response.content.decode()
        if en_metadata_expected_in_deposit:
            assert en_title in response_content
            assert pt_title not in response_content
        else:
            assert en_title not in response_content
            assert pt_title in response_content


@pytest.mark.skipif("not config.getoption('--run-academic')")
@pytest.mark.django_db
def test_language_setup(
    client: Client,
    admin: JCOMProfile,
    article: Article,  # needed because it sets-up all the site  # noqa: ARG001
) -> None:
    """Reminder about language setup."""
    # Most of the settings necessary for i18n are already active;
    # we can change the language used by "client" with LANGUAGE_CODE
    client.force_login(admin)
    with override_settings(
        # not necessary: # USE_I18N=True,
        # not necessary: # LANGUAGES=[
        # not necessary: #     ("en", "English"),
        # not necessary: #     ("pt", "Portuguese"),
        # not necessary: # ],
        # not necessary: # MIDDLEWARE=[
        # not necessary: #     "django.middleware.locale.LocaleMiddleware",
        # not necessary: #     "django.middleware.common.CommonMiddleware",
        # not necessary: # ],
        LANGUAGE_CODE="pt",
    ):
        response = client.get(reverse("website_index"))
        assert "Terminar a sessão" in response.content.decode()
    with override_settings(LANGUAGE_CODE="en"):
        response = client.get(reverse("website_index"))
        assert "Logout" in response.content.decode()
