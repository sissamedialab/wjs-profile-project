"""Test some parts of the command that imports JCOM articles from Drupal."""
import os
from pathlib import Path

import lxml
import lxml.html
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

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "lang, header",
        (
            ("eng", "How to cite"),
            ("spa", "Cómo citar"),
            ("por", "Como citar"),
        ),
    )
    def test_drop_how_to_cite(self, lang, header):
        """Test that the how-to-cite part is removed from the HTML galley."""
        from wjs.jcom_profile.import_utils import drop_how_to_cite

        html = lxml.html.fromstring(
            f"""<root>
        <h2>keepme 1</h2>
        <p>keepme 1 par</p>
        <h2>{header}</h2>
        <p>Ciao</p>
        <h2>keepme 2</h2>
        <p>keepme 2</p>
        </root>""",
        )

        drop_how_to_cite(html=html, lang=lang)

        h2_elements = html.findall(".//h2")
        assert len(h2_elements) == 2
        for h2 in h2_elements:
            assert "keepme" in h2.text

        p_elements = html.findall(".//p")
        assert len(p_elements) == 2
        for p in p_elements:
            assert "keepme" in p.text

    @pytest.mark.django_db
    def test_drop_how_to_cite_jcomal_0601_2023_a03(self):
        """Test that the how-to-cite part is removed from this galley fragment."""
        from wjs.jcom_profile.import_utils import drop_how_to_cite

        html = lxml.html.fromstring(
            f"""<root>
 <p class="noindent">
  Nat&#225;lia Martins Flores. Jornalista, gerente de conte&#250;do da Ag&#234;ncia Bori, doutora
em Comunica&#231;&#227;o pela Universidade Federal de Pernambuco (UFPE), tem
p&#243;s-doutorado na &#225;rea de Comunica&#231;&#227;o, nas linhas de pesquisa de Estrat&#233;gias
Comunicacionais (UFSM) e Comunica&#231;&#227;o de ci&#234;ncia e divulga&#231;&#227;o cient&#237;fica
(Unicamp). Tem experi&#234;ncia com an&#225;lise de discurso e de linguagem, tendo realizado
est&#225;gio doutoral na Universit&#233; Sorbonne IV, em Paris. Ela colabora com o grupo de
pesquisa  TemCi&#234;ncianoBR: produ&#231;&#227;o cient&#237;fica brasileira e sua dissemina&#231;&#227;o
(Labjor/Unicamp).
  <br class="newline">
  E-mail:
  <a href="mailto:nataliflores@gmail.com">
   nataliflores@gmail.com
  </a>
 </p>
 <h2 class="likesectionHead">
  <a id="x1-13000">
  </a>
  Como citar
  <a id="Q1-1-25">
  </a>
 </h2>
 <p class="indent">
  Hafiz, M., Righetti, S., Gamba, E., Quaglio de Andrade, F. e Martins Flores, N., Quaglio de
Andrade, F. e (2023). &#8216;Ci&#234;ncia na m&#237;dia: uma proposta de classifica&#231;&#227;o de
informa&#231;&#227;o a partir de estudo de caso sobre a "Folha" e o "NYT" no primeiro ano da
pandemia&#8217;. JCOM &#8211;
  <i>
   Am&#233;rica Latina
  </i>
  06 (01), A03.
  <a href="https://doi.org/10.22323/3.06010203">
   https://doi.org/10.22323/3.06010203
  </a>
  .
 </p>
 <p class="indent">
 </p>
 <h2 class="likesectionHead">
  <a id="x1-14000">
  </a>
  Notas
  <a id="Q1-1-27">
  </a>
 </h2>
 <div class="footnotes">
  <a id="x1-4009x1">
  </a>
  <p class="noindent">
   <span class="footnote-mark">
    <a href="#fn1x0-bk" id="fn1x0">
     <sup class="textsuperscript">
      1
     </sup>
    </a>
   </span>
   Um caso simb&#243;lico se deu na afirma&#231;&#227;o, do presidente Bolsonaro, de que caso fosse contaminado
pelo v&#237;rus "nada sentiria ou seria acometido, quando muito, de uma gripezinha ou resfriadinho"j&#225; que
teria "hist&#243;rico de atleta". A declara&#231;&#227;o foi feita em rede nacional em 24 de mar&#231;o de 2020
e foi amplamente rebatida pela imprensa a partir de evid&#234;ncias cient&#237;ficas dispon&#237;veis na
&#233;poca.
  </p>
        </root>""",  # noqa
        )

        h2_elements = html.findall(".//h2")
        assert len(h2_elements) == 2

        drop_how_to_cite(html=html, lang="por")

        h2_elements = html.findall(".//h2")
        assert len(h2_elements) == 1
        assert "Notas" in h2_elements[0].text_content()

    @pytest.mark.skip(reason="Una-tantum test. Not related to the application.")
    def test_lxml_from_to_string(self):
        """Verify that lxml tostring method doesn't messes with the spaces."""
        input_str = """<root><p>ciao [<a href="#">Name, 2000</a>] bel</p></root>"""
        html = lxml.html.fromstring(input_str)
        output_str = lxml.html.tostring(html)
        assert input_str == output_str.decode("utf-8")

    @pytest.mark.django_db
    def test_process_body_does_not_add_spaces(self):
        """Test that process_body does introduction spurious spaces."""
        body = """<html id="main_article" lang="en" xml:lang="en"><body><p class="noindent">ciao [<a href="#">Name, 2000</a>] bel</p></body></html>"""  # noqa E501
        style = "wjapp"  # important!
        lang = "eng"
        from wjs.jcom_profile.import_utils import process_body

        processed_body: bytes = process_body(body=body, style=style, lang=lang)
        processed_body_element = lxml.html.fromstring(processed_body)
        assert processed_body_element.find(".//p").text_content() == "ciao [Name, 2000] bel"

    @pytest.mark.django_db
    def test_process_body_does_not_add_spaces_sanity_check(self):
        """Test that process_body does introduction spurious spaces, but they are maintained."""
        body = """<html id="main_article" lang="en" xml:lang="en"><body><p class="noindent">ciao [ <a href="#">Name, 2000</a>
        ] bel</p></body></html>"""  # noqa E501
        style = "wjapp"  # important!
        lang = "eng"
        from wjs.jcom_profile.import_utils import process_body

        processed_body: bytes = process_body(body=body, style=style, lang=lang)
        processed_body_element = lxml.html.fromstring(processed_body)
        assert processed_body_element.find(".//p").text_content() == "ciao [ Name, 2000\n        ] bel"


class TestCommandMethods:
    """Let's test if this is possible."""

    @pytest.mark.skip(reason="A proof of concept that I don't really need.")
    @pytest.mark.django_db
    def test_import_from_wjapp__set_html_galley(self, article, tmp_path):
        """Create a cmd obj and call a method."""
        # Import here or trigger the issue with missing django_db fixture
        from wjs.jcom_profile.management.commands.import_from_wjapp import Command

        # Setup html files (TODO: might be better as a fixture)
        galley_str = """<p class="noindent">  Although the genetic technologies in
  <i>Orphan Black</i>
  are imaginary, this AAAA--BBBB study
focuses on connections between CCCC-ff-DDDD attention to science fiction and perceptions of
the real technology of human genome editing (HGE). We are concerned with
science fiction because it directly addresses the social aspects of science, such as
power and
politics [<a id="x1-3001"></a>Maynard, <a href="#X0-Maynard2018">2018</a>], and
frequently offers vivid pictures of science
and of scientists. Further, science fiction also offers nonexperts with a way to
think about genetics and science more broadly: evidence shows that nonexperts
use science fiction metaphors and narratives as a means to express their beliefs
about genetics and to make sense of the
technology [<a id="x1-3002"></a>Roberts, Archer, DeWitt &amp; Middleton,  <a href="#X0-Robertsetal2019">2019</a>],
meaning science fiction could be useful for engagement purposes.
 </p>
"""  # noqa W921
        html_galley_filename = str(tmp_path / "galley.html")
        with open(html_galley_filename, "w") as of:
            of.write(html_galley_filename)

        c = Command()
        assert c.set_html_galley(self, article, html_galley_filename=html_galley_filename)
