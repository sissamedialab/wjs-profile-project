"""Test some parts of the command that imports JCOM articles from Drupal."""
import os
from pathlib import Path

import lxml
import pytest

from wjs.jcom_profile.utils import from_pubid_to_eid


class TestImport:
    """Test only the generic utilities."""

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
    def test_eid_from_pubid(self, pubid, eid):
        """Test the extraction of the eid from the pubid."""
        assert from_pubid_to_eid(pubid) == eid

    @pytest.mark.django_db
    def test_process_body_drops_html(self):
        """Test that tags <html> and <body> are droppend from the galley.

        A <div> with all the attributes from <html> and <body> should be present instead.

        """
        body = """<html id="main_article" lang="en" xml:lang="en"><body><p class="noindent">ciao</p></body></html>"""
        style = None
        lang = "eng"

        # Not sure why, but if this is at the top of the file, pytest
        # complains about missing access to the DB...
        from wjs.jcom_profile.import_utils import process_body

        processed_body: bytes = process_body(body=body, style=style, lang=lang)
        processed_body_element = lxml.html.fromstring(processed_body)
        assert processed_body_element.tag == "div"
        expected_attributes = (("id", "main_article"), ("lang", "en"), ("xml:lang", "en"))
        found_items = processed_body_element.items()
        for attribute in expected_attributes:
            assert attribute in found_items

        first_kid = processed_body_element.getchildren()[0]
        assert first_kid.tag == "p"
        assert first_kid.get("class") == "noindent"

    @pytest.mark.django_db
    def test_process_body_drops_html_real_galley(self, tmp_path):
        """Test that tags <html> and <body> are droppend from the galley.

        Use a real galley from article.id 1234 and compare with a known result.

        NB: The result of this test depends on the complete
        process_body() function, not only on the drop-html part.

        """
        here = Path(os.path.abspath(__file__)).parent
        galley_1234 = here / "aux" / "326ef1f7-7246-4bd4-9087-002c208709ea.html"
        with open(galley_1234) as galley_file:
            # Not sure why, but if this is at the top of the file, pytest
            # complains about missing access to the DB...
            from wjs.jcom_profile.import_utils import process_body

            style = None
            lang = "eng"
            processed_body: bytes = process_body(body=galley_file.read(), style=style, lang=lang)

        expected_result = here / "aux" / "326ef1f7-7246-4bd4-9087-002c208709ea__processed.html"
        with open(expected_result, "rb") as expected_result_file:
            expected_body = expected_result_file.read()

        assert processed_body == expected_body
