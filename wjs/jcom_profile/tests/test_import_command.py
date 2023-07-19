"""Test some parts of the command that imports JCOM articles from Drupal."""
import os
from pathlib import Path

import lxml
import lxml.html
import pytest
from core.models import Account

from wjs.jcom_profile.models import Correspondence
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


def mock_query_wjapp_by_pubid(*args, **kwargs):
    return {
        "userCod": "12301",
        "correspondence_source": "jcom",
    }


def mock_set_author_country(*args, **kwargs):
    pass


@pytest.fixture
def set_authors_common_setup(roles, article, monkeypatch):
    """Setup xml_obj and patch what's necessary.

    Also remove authors from article, so that known ones can be added.
    """

    xml_str_template = """<root>
        <volume volumeid="22">Volume 22, 2023</volume>
        <issue volumeid="22" issueid="2202">Issue 02, 2023</issue>
        {authors_ext}
        <document documentid="18274">
            <volume volumeid="22"/>
            <issue issueid="2202"/>
            <articleid>JCOM_2202_2023_A07</articleid>
            {authors_int}
            <year>2023</year>
            <date_submitted>2022-10-17</date_submitted>
            <date_accepted>2023-04-02</date_accepted>
            <date_published>2023-05-15</date_published>
            <doi>10.22323/2.22020207</doi>
            <type>article</type>
            <title>Diversifying citizen science</title>
            <abstract>The study presents findings.</abstract>
            <keyword>Citizen science</keyword>
            <contribution url="private://wjapp/JCOM_2202_2023_A07.pdf">JCOM_2202_2023_A07.pdf</contribution>
        </document>
    </root>
    """  # noqa W921

    xml_str = xml_str_template.format(
        authors_ext="""<author
        authorid="12301"
        email="natasha.constant@rspb.org.uk"
        firstname="Natasha"
        lastname="Constant">Natasha Constant</author>""",
        authors_int="""<author authorid="12301"/>""",
    )
    # Be sure to call getroottree(), because fromstring() returns an Element, not an ElementTree
    xml_obj = lxml.etree.fromstring(xml_str).getroottree()

    from wjs.jcom_profile.management.commands import import_from_wjapp

    monkeypatch.setattr(import_from_wjapp, "query_wjapp_by_pubid", mock_query_wjapp_by_pubid)
    monkeypatch.setattr(import_from_wjapp, "set_author_country", mock_set_author_country)

    article.authors.clear()

    from wjs.jcom_profile.management.commands.import_from_wjapp import Command

    command = Command()
    command.journal_data = import_from_wjapp.JOURNALS_DATA["JCOM"]

    return (article, command, xml_obj)


class TestImportFromWjappSetAuthors:
    """Test set_authors in import_from_wjapp.

    Given a certain email in the XML (imported_email), we test what
    the function does wrt possibly existing Account and
    Correspondences.

    We should have the folloing cases:
    - [a0] no Account
    - [a1] Account w/ same email as imported_email
    - [ax] Account w/ different email
    ✕
    - [c0] no Correspondence
    - [c1] Correspondence w/ same email as imported_email
    - [cx] Correspondence w/ different email
    that gives us the 9 cases below.

    However
    a0-c1 and a0-cx are impossible because a Correspondence always has an Account

    Also, we should check the behaviour when then are multiple Accounts/Correspondences
    TODO: WRITEME
    """

    @pytest.mark.django_db
    def test_a0_c0(self, set_authors_common_setup):
        """Test with no existing Accounts nor Correspondences."""
        (article, command, xml_obj) = set_authors_common_setup

        existing_accounts_count = Account.objects.count()
        assert Correspondence.objects.exists() is False

        command.set_authors(article, xml_obj)

        # A new Account should have been created for the new author:
        assert Account.objects.count() == existing_accounts_count + 1
        # and a Correspondence object also:
        assert Correspondence.objects.count() == 1

        assert article.authors.count() == 1
        assert article.authors.first().first_name == "Natasha"

    @pytest.mark.django_db
    def test_a1_c0(self, set_authors_common_setup):
        """Test with matching Account but without any Correspondence."""
        (article, command, xml_obj) = set_authors_common_setup

        existing_account = Account.objects.create(
            email="natasha.constant@rspb.org.uk",
            first_name="Natasha",
            last_name="Constant",
        )

        existing_accounts_count = Account.objects.count()
        assert Correspondence.objects.count() == 0

        command.set_authors(article, xml_obj)

        # No new Account should have been created for the new author:
        assert Account.objects.count() == existing_accounts_count
        # and no new Correspondence object:
        assert Correspondence.objects.count() == 1
        assert Correspondence.objects.first().email == "natasha.constant@rspb.org.uk"

        assert article.authors.count() == 1
        assert article.authors.first() == existing_account

    @pytest.mark.django_db
    def test_a1_c1(self, set_authors_common_setup):
        """Test with matching Account and Correspondence."""
        (article, command, xml_obj) = set_authors_common_setup

        existing_account = Account.objects.create(
            email="natasha.constant@rspb.org.uk",
            first_name="Natasha",
            last_name="Constant",
        )

        existing_correspondence = Correspondence.objects.create(
            account=existing_account,
            email=existing_account.email,
            source="jcom",
            user_cod=12301,
        )

        existing_accounts_count = Account.objects.count()
        assert Correspondence.objects.count() == 1

        command.set_authors(article, xml_obj)

        # No new Account should have been created for the new author:
        assert Account.objects.count() == existing_accounts_count
        # and no new Correspondence object:
        assert Correspondence.objects.count() == 1
        assert Correspondence.objects.first() == existing_correspondence

        assert article.authors.count() == 1
        assert article.authors.first() == existing_account

    @pytest.mark.django_db
    def test_a1_cx(self, set_authors_common_setup, capsys, caplog):
        """Call set_authors with matching Account, but the Correspondence has a different email.

        We don't want to loose an email, so here I'm expecting a new
        Correspondence to be created.
        """
        (article, command, xml_obj) = set_authors_common_setup

        existing_account = Account.objects.create(
            email="natasha.constant@rspb.org.uk",
            first_name="Natasha",
            last_name="Constant",
        )

        existing_correspondence = Correspondence.objects.create(
            account=existing_account,
            email="different@email.it",
            source="jcom",
            user_cod=12301,
        )

        existing_accounts_count = Account.objects.count()
        assert Correspondence.objects.count() == 1

        command.set_authors(article, xml_obj)

        # No new Account should have been created for the new author:
        assert Account.objects.count() == existing_accounts_count
        # but a new Correspondence object should:
        assert Correspondence.objects.count() == 2
        mappings = Correspondence.objects.all().order_by("id")
        assert existing_correspondence == mappings.first()
        assert existing_correspondence.email == "different@email.it"
        new_correspondence = mappings.last()
        assert new_correspondence.email == "natasha.constant@rspb.org.uk"

        assert article.authors.count() == 1
        assert article.authors.first() == existing_account
        assert existing_account.email == "natasha.constant@rspb.org.uk"

        # I'm not sure why, but the log messages from the command sometimes end
        # up in pytest's capsys, some other times in caplog.
        expected_log_text = f"Created new mapping {new_correspondence.source}/{new_correspondence.user_cod}/{new_correspondence.email} for account {new_correspondence.account}"  # noqa E501
        if caplog.text == "":
            captured = capsys.readouterr()
            assert expected_log_text in captured
        else:
            assert expected_log_text in caplog.text

    @pytest.mark.django_db
    def test_ax_c0(self, set_authors_common_setup):
        """Test with Account with different email and no Correspondence."""
        (article, command, xml_obj) = set_authors_common_setup

        existing_account = Account.objects.create(
            email="different@email.it",
            first_name="Natasha",
            last_name="Constant",
        )

        existing_accounts_count = Account.objects.count()
        assert Correspondence.objects.count() == 0

        command.set_authors(article, xml_obj)

        # Since there is no correspondence and the Account email is
        # different, a new Account should have been created:
        assert Account.objects.count() == existing_accounts_count + 1
        # and a new Correspondence object:
        assert Correspondence.objects.count() == 1
        # and the email set to the imported_email
        new_correspondence = Correspondence.objects.get()
        assert new_correspondence.email == "natasha.constant@rspb.org.uk"

        assert article.authors.count() == 1
        author = article.authors.first()
        assert author != existing_account
        assert author.email == new_correspondence.email
        assert existing_account.email == "different@email.it"

    @pytest.mark.django_db
    def test_ax_c1(self, set_authors_common_setup):
        """Test with Account with different email but with matching Correspondence."""
        (article, command, xml_obj) = set_authors_common_setup

        existing_account = Account.objects.create(
            email="different@email.it",
            first_name="Natasha",
            last_name="Constant",
        )

        existing_correspondence = Correspondence.objects.create(
            account=existing_account,
            email="natasha.constant@rspb.org.uk",
            source="jcom",
            user_cod=12301,
        )

        existing_accounts_count = Account.objects.count()
        assert Correspondence.objects.count() == 1

        command.set_authors(article, xml_obj)

        # No new Account should have been created for the new author:
        assert Account.objects.count() == existing_accounts_count
        # and no new Correspondence object:
        assert Correspondence.objects.count() == 1
        # and the email did not change
        assert existing_correspondence.email == "natasha.constant@rspb.org.uk"

        assert article.authors.count() == 1
        assert article.authors.first() == existing_account
        assert existing_account.email == "different@email.it"

    @pytest.mark.django_db
    def test_ax_cx(self, set_authors_common_setup, caplog):
        """Test with Account with different email and a Correspondence with different email."""
        (article, command, xml_obj) = set_authors_common_setup

        existing_account = Account.objects.create(
            email="different@email.it",
            first_name="Natasha",
            last_name="Constant",
        )

        existing_correspondence = Correspondence.objects.create(
            account=existing_account,
            email="another_different@email.it",
            source="jcom",
            user_cod=12301,
        )

        existing_accounts_count = Account.objects.count()
        assert Correspondence.objects.count() == 1

        command.set_authors(article, xml_obj)

        # Since a Correspondence exists no new Account should have been created:
        assert Account.objects.count() == existing_accounts_count
        # but a new Correspondence object:
        assert Correspondence.objects.count() == 2
        # and the email set to the imported_email
        new_correspondence = Correspondence.objects.all().order_by("id").last()
        assert new_correspondence.email == "natasha.constant@rspb.org.uk"
        # The old correspondence is still there, and both point to the same account
        assert existing_correspondence.account == new_correspondence.account
        assert existing_correspondence.source == new_correspondence.source
        assert existing_correspondence.user_cod == new_correspondence.user_cod
        assert existing_correspondence.email != new_correspondence.email

        assert article.authors.count() == 1
        assert article.authors.first() == existing_account
        assert existing_account.email == "different@email.it"

        assert (
            f"Created new mapping {new_correspondence.source}/{new_correspondence.user_cod}/{new_correspondence.email} for account {new_correspondence.account}"  # noqa E501
            in caplog.text
        )
        assert "Janeway different@email.it vs. new natasha.constant@rspb.org.uk" in caplog.text
