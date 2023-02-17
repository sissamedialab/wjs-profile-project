"""Test the generation of the how-to-cite string."""

from collections import namedtuple
from unittest.mock import MagicMock

import pytest

from wjs.jcom_profile.templatetags.wjs_tags import how_to_cite
from wjs.jcom_profile.utils import (
    abbreviate_first_middle,
    citation_name,
    from_pubid_to_eid,
)

MockAuthor = namedtuple(
    "MockAuthor",
    [
        "first_name",
        "middle_name",
        "last_name",
        "is_corporate",
        "corporate_name",
    ],
)


# These have the form:
# first,middle,last,is_corporate,corporate_name,sep,abbreviation,expected_citation_name
# Add new ones to the bottom: items 3, 4 and 5 are used in test_htc and hardcoded (sorry;)
AUTHORS_WITH_INTERESTING_NAMES = (
    ("Mario", "", "Rossi", False, None, "", "M.", "Rossi, M."),
    # JCOM_2201_2023_A05
    ("Anne-Caroline", "", "Prévot", False, None, "", "A.-C.", "Prévot, A.-C."),
    # From PoS
    ("D'ann", "", "Barker", False, None, "", "D.", "Barker, D."),
    ("Haidar Mas'ud", "", "Alfanda", False, None, "", "H.M.", "Alfanda, H.M."),  # used in test_htc
    ("Natal'ya", "", "Peresadko", False, None, "", "N.", "Peresadko, N."),  # used in test_htc
    ("Re'em", "", "Sari", False, None, "", "R.", "Sari, R."),  # used in test_htc
    ("Shadi Adel Moh'd", "", "Bedoor", False, None, "", "S.A.M.", "Bedoor, S.A.M."),
    # With space as separator
    ("Anne-Caroline", "", "Prévot", False, None, " ", "A.-C.", "Prévot, A.-C."),
    ("D'ann", "", "Barker", False, None, " ", "D.", "Barker, D."),
    ("Shadi Adel Moh'd", "", "Bedoor", False, None, " ", "S. A. M.", "Bedoor, S. A. M."),
    # Corporate - abbreviation doen't care, only citation name changes!
    ("First", "Middle", "Last", True, "Corporate name", "", "F.M.", "Corporate name"),
    # With middlename (from PoS) - no space
    ("C.-J.", "David", "Lin", False, None, "", "C.-J.D.", "Lin, C.-J.D."),
    ("Kim-Vy", "H.", "Tran", False, None, "", "K.-V.H.", "Tran, K.-V.H."),
    ("M.-H.", "A.", "Huang", False, None, "", "M.-H.A.", "Huang, M.-H.A."),
    ("Niels-Uwe", "Friedrich", "Bastian", False, None, "", "N.-U.F.", "Bastian, N.-U.F."),
    ("Zh.-A.", "M.", "Dzhilkibaev", False, None, "", "Z.-A.M.", "Dzhilkibaev, Z.-A.M."),
    ("Zhan-Arys", "Magysovich", "Dzhilkibaev", False, None, "", "Z.-A.M.", "Dzhilkibaev, Z.-A.M."),
    ("Zhan-Arys", "M.", "Dzhlkibaev", False, None, "", "Z.-A.M.", "Dzhlkibaev, Z.-A.M."),
    # With middlename (from PoS) - with space
    ("C.-J.", "David", "Lin", False, None, " ", "C.-J. D.", "Lin, C.-J. D."),
    ("Kim-Vy", "H.", "Tran", False, None, " ", "K.-V. H.", "Tran, K.-V. H."),
    ("M.-H.", "A.", "Huang", False, None, " ", "M.-H. A.", "Huang, M.-H. A."),
    ("Niels-Uwe", "Friedrich", "Bastian", False, None, " ", "N.-U. F.", "Bastian, N.-U. F."),
    ("Zh.-A.", "M.", "Dzhilkibaev", False, None, " ", "Z.-A. M.", "Dzhilkibaev, Z.-A. M."),
    ("Zhan-Arys", "Magysovich", "Dzhilkibaev", False, None, " ", "Z.-A. M.", "Dzhilkibaev, Z.-A. M."),
    ("Zhan-Arys", "M.", "Dzhlkibaev", False, None, " ", "Z.-A. M.", "Dzhlkibaev, Z.-A. M."),
    # Missing (None) middle name (imported authors have it set to None)
    ("Mario", None, "Rossi", False, None, "", "M.", "Rossi, M."),
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
        "first,middle,last,is_corporate,corporate_name,sep,abbreviation,expected_citation_name",
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
        expected_citation_name,  # not used here
    ):
        """Test the abbreviation of given names."""
        author = MockAuthor(first, middle, last, is_corporate, corporate_name)
        assert abbreviate_first_middle(author, sep=sep) == abbreviation

    @pytest.mark.parametrize(
        "first,middle,last,is_corporate,corporate_name,sep,abbreviation,expected_citation_name",
        AUTHORS_WITH_INTERESTING_NAMES,
    )
    def test_expected_citation_name(
        self,
        first,
        middle,
        last,
        is_corporate,
        corporate_name,
        sep,
        abbreviation,  # not used here
        expected_citation_name,
    ):
        """Test the abbreviation of given names."""
        author = MockAuthor(first, middle, last, is_corporate, corporate_name)
        assert citation_name(author, sep=sep) == expected_citation_name


class TestHTC:
    """Test How To Cite."""

    def test_htc(self):
        """Document (not really a test) what we expect in the how to cite string."""
        # Mock an article-like data strcuture that can have an "how to cite"
        au1 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[3][0:5])
        au2 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[4][0:5])
        au3 = MockAuthor(*AUTHORS_WITH_INTERESTING_NAMES[5][0:5])
        mockarticle = MagicMock()
        mockarticle.date_published.year = 2000
        mockarticle.title = "TITLE"
        mockarticle.journal.code = "JCOM"
        mockarticle.issue.volume = 1
        mockarticle.issue.issue = 2
        mockarticle.page_numbers = "A03"
        mockarticle.get_doi.return_value = "10.22323/2.123456"
        simple_piece = "(2000). TITLE <i>JCOM</i> 1(2), A03. https://doi.org/10.22323/2.123456"
        mockarticle.frozenauthor_set.all.return_value = [au1]
        assert how_to_cite(mockarticle) == f"Alfanda, H. M. {simple_piece}"
        mockarticle.frozenauthor_set.all.return_value = [au1, au2, au3]
        assert how_to_cite(mockarticle) == f"Alfanda, H. M., Peresadko, N. and Sari, R. {simple_piece}"
