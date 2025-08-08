"""Test some parts of the command that imports JCOM articles from Drupal."""

import os
from pathlib import Path

import lxml
import lxml.html
import pytest
from core.models import COUNTRY_CHOICES

from wjs.jcom_profile.import_utils import COUNTRIES_MAPPING
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


@pytest.mark.django_db
@pytest.mark.parametrize(
    "xml_string, expected_info",
    [
        (
            """<div class="maketitle">
  <h1 class="titleHead">Visualising...</h1>
  <b><span class="author">Marnell...</span></b>
  <h2 class="likesectionHead"><a id="x1-1000"></a>Abstract<a id="Q1-1-2"></a></h2>
  ...
  <h2 class="likesectionHead"><a id="x1-2000"></a>Keywords<a id="Q1-1-4"></a></h2>
  ...
  <h2 class="likesectionHead"><a id="x1-3000"></a>Contents</h2>
  <div class="tableofcontents">
    <span class="sectionToc"><a href="#Q1-1-2">Abstract</a></span>
    <br> <span class="sectionToc"><a href="#Q1-1-4">...</a></span>
  </div>
  <h2 class="likesectionHead"><a id="x1-4000"></a>Reviewed Book<a id="Q1-1-7"></a></h2>
  Christiansen, J. (2023).<br>
  Building Science Graphics.<br>
  Boca Raton &amp; Oxon: CRC Press
</div>
""",
            (
                "Reviewed Book",
                "Christiansen",
                "Building Science",
                "Boca Raton",
            ),
        ),
        (
            """<div class="maketitle">
  <h1 class="titleHead">Visualising...</h1>
  <b><span class="author">Marnell...</span></b>
  <h2 class="likesectionHead"><a id="x1-1000"></a>Abstract<a id="Q1-1-2"></a></h2>
  ...
  <h2 class="likesectionHead"><a id="x1-2000"></a>Keywords<a id="Q1-1-4"></a></h2>
  ...
  <h2 class="likesectionHead"><a id="x1-3000"></a>Contents</h2>
  <div class="tableofcontents">
    <span class="sectionToc"><a href="#Q1-1-2">Abstract</a></span>
    <br> <span class="sectionToc"><a href="#Q1-1-4">...</a></span>
  </div>
  <h2 class="likesectionHead"><a id="x1-4000"></a>Reviewed Conference<a id="Q1-1-7"></a></h2>
  Christiansen, J. (2023).<br>
  Boca Raton &amp; Oxon: CRC Press
</div>
""",
            (
                "Reviewed Conference",
                "Christiansen",
                "Boca Raton",
            ),
        ),
    ],
)
def test_extract_reviews_info(xml_string: str, expected_info: list):
    """Test that the info about a review are extracted correctly."""
    from wjs.jcom_profile.import_utils import extract_reviews_info

    html = lxml.html.fromstring(xml_string)

    h2_elements = html.findall(".//h2")
    assert len(h2_elements) == 4

    wrapper_element: lxml.html.HtmlElement = extract_reviews_info(html)
    text_content = "".join(wrapper_element.itertext())
    for expected_fragment in expected_info:
        assert expected_fragment in text_content


def test_countries_mapping():
    """Test that all the wjapp countries are mapped in wjs."""

    wjapp_countries = [
        "Afghanistan",
        "Åland Islands",
        "Albania",
        "Algeria",
        "American Samoa",
        "Andorra",
        "Angola",
        "Anguilla",
        "Antigua and Barbuda",
        "Argentina",
        "Armenia",
        "Aruba",
        "Australia",
        "Austria",
        "Azerbaijan",
        "Bahamas",
        "Bahrain",
        "Bangladesh",
        "Barbados",
        "Belarus",
        "Belgium",
        "Belize",
        "Benin",
        "Bermuda",
        "Bhutan",
        "Bolivia (Plurinational State of)",
        "Bonaire, Saint Eustatius and Saba",
        "Bosnia and Herzegovina",
        "Botswana",
        "Brazil",
        "British Virgin Islands",
        "Brunei Darussalam",
        "Bulgaria",
        "Burkina Faso",
        "Burundi",
        "Cambodia",
        "Cameroon",
        "Canada",
        "Cabo Verde",
        "Cayman Islands",
        "Central African Republic",
        "Chad",
        "Chile",
        "China",
        "Hong Kong",
        "Macao",
        "Colombia",
        "Comoros",
        "Congo (the)",
        "Cook Islands (the)",
        "Costa Rica",
        "Côte d`Ivoire",
        "Croatia",
        "Cuba",
        "Curaçao",
        "Cyprus",
        "Czechia",
        "Korea (the Democratic People`s Republic of)",
        "Congo (the Democratic Republic of the)",
        "Denmark",
        "Djibouti",
        "Dominica",
        "Dominican Republic (the)",
        "Ecuador",
        "Egypt",
        "El Salvador",
        "Equatorial Guinea",
        "Eritrea",
        "Estonia",
        "Ethiopia",
        "Faroe Islands (the)",
        "Falkland Islands (the) [Malvinas]",
        "Fiji",
        "Finland",
        "France",
        "French Guiana",
        "French Polynesia",
        "Gabon",
        "Gambia (the)",
        "Georgia",
        "Germany",
        "Ghana",
        "Gibraltar",
        "Greece",
        "Greenland",
        "Grenada",
        "Guadeloupe",
        "Guam",
        "Guatemala",
        "Guernsey",
        "Guinea",
        "Guinea-Bissau",
        "Guyana",
        "Haiti",
        "Holy See (the)",
        "Honduras",
        "Hungary",
        "Iceland",
        "India",
        "Indonesia",
        "Iran (Islamic Republic of)",
        "Iraq",
        "Ireland",
        "Isle of Man",
        "Israel",
        "Italy",
        "Jamaica",
        "Japan",
        "Jersey",
        "Jordan",
        "Kazakhstan",
        "Kenya",
        "Kiribati",
        "Kuwait",
        "Kyrgyzstan",
        "Lao People`s Democratic Republic (the)",
        "Latvia",
        "Lebanon",
        "Lesotho",
        "Liberia",
        "Libya",
        "Liechtenstein",
        "Lithuania",
        "Luxembourg",
        "Madagascar",
        "Malawi",
        "Malaysia",
        "Maldives",
        "Mali",
        "Malta",
        "Marshall Islands (the)",
        "Martinique",
        "Mauritania",
        "Mauritius",
        "Mayotte",
        "Mexico",
        "Micronesia (Federated States of)",
        "Monaco",
        "Mongolia",
        "Montenegro",
        "Montserrat",
        "Morocco",
        "Mozambique",
        "Myanmar",
        "Namibia",
        "Nauru",
        "Nepal",
        "Netherlands (the)",
        "New Caledonia",
        "New Zealand",
        "Nicaragua",
        "Niger (the)",
        "Nigeria",
        "Niue",
        "Norfolk Island",
        "Northern Mariana Islands (the)",
        "Norway",
        "Palestine",
        "Oman",
        "Pakistan",
        "Palau",
        "Panama",
        "Papua New Guinea",
        "Paraguay",
        "Peru",
        "Philippines (the)",
        "Pitcairn",
        "Poland",
        "Portugal",
        "Puerto Rico",
        "Qatar",
        "Korea (the Republic of)",
        "Moldova (the Republic of)",
        "Réunion",
        "Romania",
        "Russian Federation (the)",
        "Rwanda",
        "Saint Barthélemy",
        "Saint Helena, Ascension and Tristan da Cunha",
        "Saint Kitts and Nevis",
        "Saint Lucia",
        "Saint Martin (French part)",
        "Saint Pierre and Miquelon",
        "Saint Vincent and the Grenadines",
        "Samoa",
        "San Marino",
        "Sao Tome and Principe",
        "Saudi Arabia",
        "Senegal",
        "Serbia",
        "Seychelles",
        "Sierra Leone",
        "Singapore",
        "Sint Maarten (Dutch part)",
        "Slovakia",
        "Slovenia",
        "Solomon Islands",
        "Somalia",
        "South Africa",
        "South Sudan",
        "Spain",
        "Sri Lanka",
        "Sudan (the)",
        "Suriname",
        "Svalbard and Jan Mayen",
        "Eswatini",
        "Sweden",
        "Switzerland",
        "Syrian Arab Republic (the)",
        "Tajikistan",
        "Thailand",
        "North Macedonia",
        "Timor-Leste",
        "Togo",
        "Tokelau",
        "Tonga",
        "Trinidad and Tobago",
        "Tunisia",
        "Turkey",
        "Turkmenistan",
        "Turks and Caicos Islands (the)",
        "Tuvalu",
        "Uganda",
        "Ukraine",
        "United Arab Emirates (the)",
        "United Kingdom of Great Britain and Northern Ireland (the)",
        "Tanzania, the United Republic of",
        "United States of America (the)",
        "Virgin Islands (U.S.)",
        "Uruguay",
        "Uzbekistan",
        "Vanuatu",
        "Venezuela (Bolivarian Republic of)",
        "Viet Nam",
        "Wallis and Futuna",
        "Western Sahara*",
        "Yemen",
        "Zambia",
        "Zimbabwe",
        "Antarctica",
        "Bouvet Island",
        "British Indian Ocean Territory (the)",
        "Christmas Island",
        "Cocos (Keeling) Islands (the)",
        "French Southern Territories (the)",
        "Heard Island and McDonald Islands",
        "South Georgia and the South Sandwich Islands",
        "Taiwan",
        "United States Minor Outlying Islands (the)",
    ]

    for country_name in wjapp_countries:
        found = []
        mapped_name = COUNTRIES_MAPPING.get(country_name, country_name)
        for _, c_name in COUNTRY_CHOICES:
            if mapped_name == c_name:
                found.append(c_name)
        assert len(found) == 1
