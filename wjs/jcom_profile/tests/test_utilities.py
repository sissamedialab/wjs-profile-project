"""Test the generation of the how-to-cite string."""

from collections import namedtuple
from unittest.mock import MagicMock

import pytest
from django.apps import apps
from submission.models import FrozenAuthor

from wjs.jcom_profile.permissions import has_eo_role
from wjs.jcom_profile.templatetags.wjs_tags import how_to_cite
from wjs.jcom_profile.utils import (
    abbreviate_first_middle,
    citation_name,
    from_pubid_to_eid,
)

MockAuthor = namedtuple(
    "MockAuthor",
    ["first_name", "middle_name", "last_name", "is_corporate", "corporate_name", "sep"],
)


# These have the form:
# first,middle,last,is_corporate,corporate_name,sep,abbreviation,expected_citation_name_apa,expected_citation_name
# Add new ones to the bottom: items 3, 4 and 5 are used in test_htc and hardcoded (sorry;)
# Note: The filed "sep" (number 6) is NOT an attribute of FrozenAuthor.
# SO in real life is always set to default " "
# So the field 7 (the apa citation) must be with spaces between names ever
AUTHORS_WITH_INTERESTING_NAMES = (
    ("Mario", "", "Rossi", False, None, "", "M.", "Rossi, M.", "M. Rossi"),
    # JCOM_2201_2023_A05
    ("Anne-Caroline", "", "Prévot", False, None, "", "A.-C.", "Prévot, A.-C.", "A.-C. Prévot"),
    # From PoS
    ("D'ann", "", "Barker", False, None, "", "D.", "Barker, D.", "D. Barker"),
    ("Haidar Mas'ud", "", "Alfanda", False, None, "", "H.M.", "Alfanda, H. M.", "H.M. Alfanda"),  # used in test_htc
    ("Natal'ya", "", "Peresadko", False, None, "", "N.", "Peresadko, N.", "N. Peresadko"),  # used in test_htc
    ("Re'em", "", "Sari", False, None, "", "R.", "Sari, R.", "R. Sari"),  # used in test_htc
    ("Shadi Adel Moh'd", "", "Bedoor", False, None, "", "S.A.M.", "Bedoor, S. A. M.", "S.A.M. Bedoor"),
    # With space as separator
    ("Anne-Caroline", "", "Prévot", False, None, " ", "A.-C.", "Prévot, A.-C.", "A.-C. Prévot"),
    ("D'ann", "", "Barker", False, None, " ", "D.", "Barker, D.", "D. Barker"),
    ("Shadi Adel Moh'd", "", "Bedoor", False, None, " ", "S. A. M.", "Bedoor, S. A. M.", "S.A.M. Bedoor"),
    # Corporate - abbreviation doesn't care, only citation name changes!
    ("First", "Middle", "Last", True, "Corporate name", "", "F.M.", "Corporate name", "Corporate name"),
    # With middlename (from PoS) - no space
    ("C.-J.", "David", "Lin", False, None, "", "C.-J.D.", "Lin, C.-J. D.", "C.-J.D. Lin"),
    ("Kim-Vy", "H.", "Tran", False, None, "", "K.-V.H.", "Tran, K.-V. H.", "K.-V.H. Tran"),
    ("M.-H.", "A.", "Huang", False, None, "", "M.-H.A.", "Huang, M.-H. A.", "M.-H.A. Huang"),
    ("Niels-Uwe", "Friedrich", "Bastian", False, None, "", "N.-U.F.", "Bastian, N.-U. F.", "N.-U.F. Bastian"),
    ("Zh.-A.", "M.", "Dzhilkibaev", False, None, "", "Z.-A.M.", "Dzhilkibaev, Z.-A. M.", "Z.-A.M. Dzhilkibaev"),
    (
        "Zhan-Arys",
        "Magysovich",
        "Dzhilkibaev",
        False,
        None,
        "",
        "Z.-A.M.",
        "Dzhilkibaev, Z.-A. M.",
        "Z.-A.M. Dzhilkibaev",
    ),
    ("Zhan-Arys", "M.", "Dzhlkibaev", False, None, "", "Z.-A.M.", "Dzhlkibaev, Z.-A. M.", "Z.-A.M. Dzhlkibaev"),
    # With middlename (from PoS) - with space
    ("C.-J.", "David", "Lin", False, None, " ", "C.-J. D.", "Lin, C.-J. D.", "C.-J.D. Lin"),
    ("Kim-Vy", "H.", "Tran", False, None, " ", "K.-V. H.", "Tran, K.-V. H.", "K.-V.H. Tran"),
    ("M.-H.", "A.", "Huang", False, None, " ", "M.-H. A.", "Huang, M.-H. A.", "M.-H.A. Huang"),
    ("Niels-Uwe", "Friedrich", "Bastian", False, None, " ", "N.-U. F.", "Bastian, N.-U. F.", "N.-U.F. Bastian"),
    ("Zh.-A.", "M.", "Dzhilkibaev", False, None, " ", "Z.-A. M.", "Dzhilkibaev, Z.-A. M.", "Z.-A.M. Dzhilkibaev"),
    (
        "Zhan-Arys",
        "Magysovich",
        "Dzhilkibaev",
        False,
        None,
        " ",
        "Z.-A. M.",
        "Dzhilkibaev, Z.-A. M.",
        "Z.-A.M. Dzhilkibaev",
    ),
    ("Zhan-Arys", "M.", "Dzhlkibaev", False, None, " ", "Z.-A. M.", "Dzhlkibaev, Z.-A. M.", "Z.-A.M. Dzhlkibaev"),
    # Missing (None) middle name (imported authors have it set to None)
    ("Mario", None, "Rossi", False, None, "", "M.", "Rossi, M.", "M. Rossi"),
)


class TestUtils:
    """Test unittest-friendly utility functions."""

    @pytest.mark.parametrize(
        "pubid,eid",
        (
            ("JCOM_1401_2015_C02", "C02"),
            ("JCOM_1401_2015_E", "E"),
            ("Jcom1102(2012)A01", "A01"),
            ("Jcom1102(2012)E", "E"),
            ("R020401", "R01"),
            ("E0204", "E"),
        ),
    )
    def test_from_pubid_to_eid(self, pubid, eid):
        """Test the extraction of the eid from the pubid."""
        assert from_pubid_to_eid(pubid) == eid

    @pytest.mark.parametrize(
        "first,middle,last,is_corporate,corporate_name,sep,abbreviation,"
        "expected_citation_name_apa,expected_citation_name",
        AUTHORS_WITH_INTERESTING_NAMES,
    )
    def test_abbreviate_first_middle(
        self,
        first,
        middle,
        last,
        is_corporate,
        corporate_name,
        sep,
        abbreviation,
        expected_citation_name_apa,  # not used here
        expected_citation_name,  # not used here
    ):
        """Test the abbreviation of given names."""
        author = MockAuthor(first, middle, last, is_corporate, corporate_name, sep)
        assert abbreviate_first_middle(author, sep=sep) == abbreviation

    @pytest.mark.parametrize(
        "first,middle,last,is_corporate,corporate_name,sep,abbreviation,"
        "expected_citation_name_apa,expected_citation_name",
        AUTHORS_WITH_INTERESTING_NAMES,
    )
    def test_expected_citation_name_apa(
        # APA7 formatting rule:
        # Initials for first/middle names must be separated by a space
        # (e.g. "Rossi, M. G.", not "Rossi, M.G.").
        # This corresponds to using sep=" " (the default) in citation_name()
        # and abbreviate_first_middle().
        # Note: hyphens within a compound name (e.g. "N.-U.") are NOT affected —
        # the space only applies between separate initials, not around an
        # internal hyphen.
        self,
        first,
        middle,
        last,
        is_corporate,
        corporate_name,
        sep,
        abbreviation,  # not used here
        expected_citation_name_apa,
        expected_citation_name,  # not used here
    ):
        """Test the APA citation name (Lastname, I.)."""
        author = MockAuthor(first, middle, last, is_corporate, corporate_name, sep)
        assert citation_name(author, apa=True) == expected_citation_name_apa

    @pytest.mark.parametrize(
        "first,middle,last,is_corporate,corporate_name,sep,abbreviation,"
        "expected_citation_name_apa, expected_citation_name",
        AUTHORS_WITH_INTERESTING_NAMES,
    )
    def test_expected_citation_name(
        # NOT APA7 formatting rule (e.g for jquant and other jounrals)
        # Initials for first/middle names must NOT be separated by a space
        # (e.g. "M.G. Rossi", not "M. G. Rossi").
        # the default in citation_name() for sep is  " " so in caas of apa=false the sap is ""
        # and abbreviate_first_middle().
        # Note: hyphens within a compound name (e.g. "N.-U.") are NOT affected —
        # the space only applies between separate initials, not around an
        # internal hyphen.
        self,
        first,
        middle,
        last,
        is_corporate,
        corporate_name,
        sep,
        abbreviation,  # not used here
        expected_citation_name_apa,  # not used here
        expected_citation_name,
    ):
        """Test the citation name (I. Lastname)."""
        author = MockAuthor(first, middle, last, is_corporate, corporate_name, sep)
        assert citation_name(author, apa=False) == expected_citation_name


class TestHTC:
    """Test How To Cite."""

    def test_htc(self):
        """Generic test on how to cite jcom with 3 authors"""
        au1 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[3][0:6])
        au2 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[4][0:6])
        au3 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[5][0:6])
        mockarticle = MagicMock()
        mockarticle.date_published.year = 2000
        mockarticle.title = "TITLE"
        mockarticle.journal.code = "JCOM"
        mockarticle.issue.volume = 1
        mockarticle.issue.issue = 2
        mockarticle.page_numbers = "A03"
        mockarticle.get_doi.return_value = "10.22323/2.123456"
        simple_piece = "(2000). TITLE. " "<i>JCOM</i> 1(2), " "A03. " "https://doi.org/10.22323/2.123456"
        mockarticle.frozenauthor_set.all.return_value = [au1]
        assert how_to_cite(mockarticle) == f"{AUTHORS_WITH_INTERESTING_NAMES[3][7]} {simple_piece}"
        mockarticle.frozenauthor_set.all.return_value = [au1, au2, au3]
        assert (
            how_to_cite(mockarticle) == f"{AUTHORS_WITH_INTERESTING_NAMES[3][7]}, "
            f"{AUTHORS_WITH_INTERESTING_NAMES[4][7]}, & "
            f"{AUTHORS_WITH_INTERESTING_NAMES[5][7]} "
            f"{simple_piece}"
        )
        mockarticle.frozenauthor_set.exists.return_value = False
        assert how_to_cite(mockarticle) == ""
        mockarticle.frozenauthor_set = FrozenAuthor.objects.none()
        assert how_to_cite(mockarticle) == ""

    def test_htc_jquant_less_than_10_authors(self):
        """Test how to cite for JQuant format with less than 10 authors."""
        au1 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[3][0:6])
        au2 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[4][0:6])
        au3 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[5][0:6])
        mockarticle = MagicMock()
        mockarticle.date_published.year = 2024
        mockarticle.title = "TITLE"
        # NB: the journal code is JQuant, while the how-to-cite reports JQUANT.
        #     This is desired!
        mockarticle.journal.code = "JQuant"
        mockarticle.issue.volume = 2026
        mockarticle.issue.issue = 4
        mockarticle.page_numbers = "154"
        mockarticle.get_doi.return_value = "10.1202020/test"
        mockarticle.frozenauthor_set.exists.return_value = True
        mockarticle.frozenauthor_set.all.return_value = [au1, au2, au3]
        assert how_to_cite(mockarticle) == (
            f"{AUTHORS_WITH_INTERESTING_NAMES[3][8]}, "
            f"{AUTHORS_WITH_INTERESTING_NAMES[4][8]} and "
            f"{AUTHORS_WITH_INTERESTING_NAMES[5][8]},"
            " <i>TITLE</i>,"
            " <i>JQUANT</i>"
            " <b>4</b> (2026) 154,"
            "  doi:10.1202020/test"
        )

    def test_htc_jquant_more_than_10_authors(self):
        """Test JQuant format with 10 or more authors: only first author + et al."""
        authors = [
            MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[i % len(AUTHORS_WITH_INTERESTING_NAMES)][0:6])
            for i in range(11)
        ]
        mockarticle = MagicMock()
        mockarticle.date_published.year = 2024
        mockarticle.title = "TITLE"
        # NB: the journal code is JQuant, while the how-to-cite reports JQUANT.
        #     This is desired!
        mockarticle.journal.code = "JQuant"
        mockarticle.issue.volume = 2026
        mockarticle.issue.issue = 4
        mockarticle.page_numbers = "154"
        mockarticle.get_doi.return_value = "10.1202020/test"
        mockarticle.frozenauthor_set.exists.return_value = True
        mockarticle.frozenauthor_set.all.return_value = authors
        assert how_to_cite(mockarticle) == (
            f"{AUTHORS_WITH_INTERESTING_NAMES[0][8]} et al.,"
            " <i>TITLE</i>,"
            " <i>JQUANT</i>"
            " <b>4</b> (2026) 154,"
            "  doi:10.1202020/test"
        )

    def test_htc_jcom_less_than_20_authors(self):
        """Test JCOM APA7 format with less than 20 authors."""
        au1 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[3][0:6])
        au2 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[4][0:6])
        au3 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[5][0:6])
        mockarticle = MagicMock()
        mockarticle.date_published.year = 2000
        mockarticle.title = "TITLE"
        mockarticle.journal.code = "JCOM"
        mockarticle.issue.volume = 1
        mockarticle.issue.issue = 2
        mockarticle.page_numbers = "A03"
        mockarticle.get_doi.return_value = "10.22323/2.123456"
        mockarticle.frozenauthor_set.exists.return_value = True
        mockarticle.frozenauthor_set.all.return_value = [au1, au2, au3]
        assert how_to_cite(mockarticle) == (
            f"{AUTHORS_WITH_INTERESTING_NAMES[3][7]}, "
            f"{AUTHORS_WITH_INTERESTING_NAMES[4][7]}, & "
            f"{AUTHORS_WITH_INTERESTING_NAMES[5][7]}"
            " (2000). TITLE."
            " <i>JCOM</i>"
            " 1(2), A03."
            " https://doi.org/10.22323/2.123456"
        )

    def test_htc_jcom_more_than_20_authors(self):
        """Test JCOM APA7 format with more than 20 authors: first 19 + ellipsis + last."""
        authors = [
            MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[i % len(AUTHORS_WITH_INTERESTING_NAMES)][0:6])
            for i in range(21)
        ]
        mockarticle = MagicMock()
        mockarticle.date_published.year = 2000
        mockarticle.title = "TITLE"
        mockarticle.journal.code = "JCOM"
        mockarticle.issue.volume = 1
        mockarticle.issue.issue = 2
        mockarticle.page_numbers = "A03"
        mockarticle.get_doi.return_value = "10.22323/2.123456"
        mockarticle.frozenauthor_set.exists.return_value = True
        mockarticle.frozenauthor_set.all.return_value = authors
        expected_authors = (
            ", ".join(AUTHORS_WITH_INTERESTING_NAMES[i % len(AUTHORS_WITH_INTERESTING_NAMES)][7] for i in range(19))
            + ", ... "
            + AUTHORS_WITH_INTERESTING_NAMES[20 % len(AUTHORS_WITH_INTERESTING_NAMES)][7]
        )
        assert how_to_cite(mockarticle) == (
            f"{expected_authors}" " (2000). TITLE." " <i>JCOM</i>" " 1(2), A03." " https://doi.org/10.22323/2.123456"
        )


@pytest.mark.django_db
def test_eo_permission(eo_user, jcom_user):
    """Test the EO membership."""
    assert has_eo_role(eo_user)
    assert not has_eo_role(jcom_user)


@pytest.mark.django_db
def test_email_setting(settings):
    """Ensure EMAIL_BACKEND is forced to console if DEBUG = True."""
    bkup = {
        "debug": settings.DEBUG,
        "email_backend": settings.EMAIL_BACKEND,
        "nl_backend": getattr(settings, "NEWSLETTER_EMAIL_BACKEND", None),
    }
    settings.DEBUG = True
    app_config = apps.get_app_config("jcom_profile")
    app_config._prevent_public_email_send()

    if settings.EMAIL_PORT == 1025:
        assert bkup["email_backend"] == settings.EMAIL_BACKEND
        assert bkup["nl_backend"] == getattr(settings, "NEWSLETTER_EMAIL_BACKEND", None)
    else:
        # Overridden configuration
        assert settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"
        assert bkup["email_backend"] == settings.NEWSLETTER_EMAIL_BACKEND

    settings.DEBUG = bkup["debug"]
