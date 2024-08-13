"""Utility functions used only during data import."""

import re
from collections import namedtuple
from typing import Optional

import lxml.html
import pycountry
import requests
from core.models import Account, Country
from django.conf import settings
from lxml.html import HtmlElement
from submission import models as submission_models
from utils.logger import get_logger

logger = get_logger(__name__)


# Janeway and wjapp country names do not completely overlap (sigh...)
COUNTRIES_MAPPING = {
    "Czechia": "Czech Republic",
    "Netherlands (the)": "Netherlands",
    "Philippines (the)": "Philippines",
    "Russian Federation (the)": "Russian Federation",
    "United Arab Emirates (the)": "United Arab Emirates",
    "United Kingdom of Great Britain and Northern Ireland (the)": "United Kingdom",
    "United States of America (the)": "United States",
    "Taiwan": "Taiwan, Province of China",
}

JANEWAY_LANGUAGES_BY_CODE = {t[0]: t[1] for t in submission_models.LANGUAGE_CHOICES}
assert len(JANEWAY_LANGUAGES_BY_CODE) == len(submission_models.LANGUAGE_CHOICES)


# A mapping between some non-standard codes used in JCOM and iso639-2
FUNNY_LANGUAGE_CODES = {
    "slo": "sl",  # Slovenian
    "sp": "es",  # Spanish, Castilian
    "dk": "da",  # Danish
    "po": "pt",  # Portuguese
    "pt-br": "pt",  # This is not technically perfect (pt-br is better than just pt)
}


def admin_fake_request():
    FakeRequest = namedtuple("FakeRequest", ["user"])
    # Use a "technical account" (that is created if not already present)
    admin, _ = Account.objects.get_or_create(
        email="wjs-support@medialab.sissa.it",
        defaults={
            "first_name": "WJS",
            "last_name": "Support",
            "is_staff": True,
            "is_admin": True,
            "is_active": True,
            "is_superuser": True,
        },
    )
    fake_request = FakeRequest(user=admin)
    return fake_request


def query_wjapp_by_pubid(pubid, url="https://jcom.sissa.it/jcom/services/jsonpublished", api_key="WJAPP_JCOM_APIKEY"):
    """Get data from wjapp."""
    apikey = getattr(settings, api_key)
    params = {
        "pubId": pubid,
        "apiKey": apikey,
    }
    response = requests.get(url=url, params=params)
    if response.status_code != 200:
        logger.warning(
            "Got HTTP code %s from wjapp (%s) for %s",
            response.status_code,
            url,
            pubid,
        )
        return {}
    return response.json()


def set_author_country(author: Account, json_data):
    """Set the author's country according to wjapp info."""
    country_name = json_data["countryName"]
    if country_name is None:
        logger.warning("No country for %s", json_data["userCod"])
        return
    country_name = COUNTRIES_MAPPING.get(country_name, country_name)
    try:
        country = Country.objects.get(name=country_name)
    except Country.DoesNotExist:
        logger.error("""Unknown country "%s" for %s""", country_name, json_data["userCod"])
    if author.country:
        if author.country != country:
            logger.error(f"Different country {country} for author {author.email} ({author.country}).")
    else:
        author.country = country
        author.save()


def drop_render_galley(article):
    """Clean up only render galley of an article."""
    # TODO: check if render_galley is removed also from article.galley_set
    if article.render_galley:
        for file_obj in article.render_galley.images.all():
            file_obj.delete()
        article.render_galley.images.clear()
        article.render_galley.file.delete()
        article.render_galley.file = None
        article.render_galley.delete()
        article.render_galley = None
        article.save()


def drop_existing_galleys(article):
    """Clean up all existing galleys of an article."""
    for galley in article.galley_set.all():
        for file_obj in galley.images.all():
            file_obj.delete()
        galley.images.clear()
        galley.file.delete()
        galley.file = None
        galley.delete()
    article.galley_set.clear()
    article.render_galley = None
    article.save()


def decide_galley_label(file_name: str, file_mimetype: str):
    """Decide the galley's label."""
    # Remember that we can have ( PDF + EPUB galley ) x languages (usually two),
    # so a label of just "PDF" might not be sufficient.
    lang_match = re.search(r"_([a-z]{2,3})(?:-pulito)?\.", file_name)
    mime_to_extension = {
        "application/pdf": "PDF",
        "application/pdf+zip": "PDF",
        "application/epub+zip": "EPUB",
    }
    label = mime_to_extension.get(file_mimetype, None)
    if label is None:
        logger.error("""Unknown mime type "%s" for %s""", file_mimetype, file_name)
        label = "Other"
    language = None
    if lang_match is not None:
        language = lang_match.group(1)
        language = FUNNY_LANGUAGE_CODES.get(language, language)
        label = f"{label} ({language})"
    return (label, language)


def set_language(article, language):
    logger.critical("Function changed. Don't use me!")


# TODO: typehint correctly  -> pycountry.db.Language
# but `Language` is generated dinamically...
def map_language(language: str):
    """Map language code from iso639-2 (two chars) to Janeway iso639-3 (three chars)."""
    # Some non-standard language codes have been used in JCOM through the years...
    language = FUNNY_LANGUAGE_CODES.get(language, language)
    lang_obj = pycountry.languages.get(alpha_2=language)
    if lang_obj is None:
        logger.error(
            f'Non-standard language code "{language}". Keeping default "English"',
        )
        return pycountry.languages.get(alpha_2="en")

    if lang_obj.alpha_3 not in JANEWAY_LANGUAGES_BY_CODE:
        logger.error(
            f'Language "{lang_obj.alpha_3}" (from "{language}") is unknown to Janeway. Keeping default "English"',
        )
        return pycountry.languages.get(alpha_2="en")

    # Small sanity check
    if lang_obj.name not in JANEWAY_LANGUAGES_BY_CODE.values():
        # We know about "Spanish" vs. "Spanish; Castilian" and it's ok to keep the latter.
        if lang_obj.name != "Spanish":
            logger.warning(
                f'ISO639 language for "{language}" is "{lang_obj.name}"'
                f""" and is different from Janeway's "{JANEWAY_LANGUAGES_BY_CODE[lang_obj.alpha_3]}" """
                "(using the latter)",
            )
    return lang_obj


def set_language_specific_field(article, field, value, clear_en=False):
    """Set the given field for the article's language to given value.

    Warning: I'm not going to "save()" the article!

    We only know about JCOMAL, all other journals we just se the field.
    """
    if article.journal.code != "JCOMAL":
        setattr(article, field, value)
        return

    if not article.language:
        logger.error(f"No language set for {article.get_identifier('pubid')}")
        return

    # Remember that article.language is alpha3, but modeltranslation's adapted fields are alpha2
    lang_obj = pycountry.languages.get(alpha_3=article.language)
    if lang_obj is None:
        logger.error(
            'Unknown language code "%s" for %s. Keeping default "English"',
            article.language,
            article.get_identifier("pubid"),
        )
        return

    language_specific_field = f"{field}_{lang_obj.alpha_2}"
    if not hasattr(article, language_specific_field):
        logger.error(f"Article {article.get_identifier('pubid')} has no field {language_specific_field}. Unexpected!")
        return

    setattr(article, language_specific_field, value)

    # This is mainly a workaround for the fact that I use `title` to
    # create the article.  There could be better ways but it doesn't
    # seem worth the effort. See
    # e.g. https://django-modeltranslation.readthedocs.io/en/latest/usage.html#multilingual-manager-1
    if clear_en:
        en_field = f"{field}_en"
        setattr(article, en_field, None)

    article.save()


def publish_article(article):
    """Publish an article."""
    # see src/journal/views.py:1078
    article.stage = submission_models.STAGE_PUBLISHED
    article.snapshot_authors()
    article.close_core_workflow_objects()
    if article.date_published < article.issue.date_published:
        article.issue.date = article.date_published
        article.issue.save()
    article.save()
    logger.debug(f"Article {article.get_identifier('pubid')} run through Janeway's publication process")


def promote_headings(html: HtmlElement):
    """Promote all h2-h6 headings by one level."""
    for level in range(2, 7):
        for heading in html.findall(f".//h{level}"):
            heading.tag = f"h{level-1}"


def drop_toc(html: HtmlElement):
    """Drop the "manual" TOC present in Drupal body content."""
    tocs = html.find_class("tableofcontents")
    if len(tocs) == 0:
        logger.warning("No TOC in WRITEME!!!")
        return

    if len(tocs) > 1:
        logger.error("Multiple TOCs in WRITEME!!!")

    tocs[0].drop_tree()


def drop_how_to_cite(html: HtmlElement, lang="eng"):
    """Drop the "manual" How-to-cite present in Drupal body content."""
    how_to_cite = {
        "eng": "How to cite",
        "spa": "Cómo citar",
        "por": "Como citar",
    }
    # It is possible that the <h2> contains <a>s, so a xpath query
    # such as .//h2[text()='How to cite'] might not be sufficient.
    htc_h2 = [element for element in html.findall(".//h2") if how_to_cite[lang] in element.text_content()]
    if len(htc_h2) == 0:
        logger.warning("No How-to-cite in HTML.")
        return

    if len(htc_h2) > 1:
        logger.error("Multiple How-to-cites in HTML.")

    htc_h2 = htc_h2[0]
    max_expected = 3
    count = 0
    while True:
        # we are going to `drop_tree` this element, so `getnext()`
        # should provide for new elments
        p = htc_h2.getnext()
        count += 1
        if count > max_expected:
            logger.warning("Too many elements after How-to-cite's H2 in HTML.")
            break
        if p is None:
            break
        if p.tag != "p":
            break
        if p.text is not None and p.text.strip() == "":
            p.drop_tree()
            break
        p.drop_tree()

    htc_h2.drop_tree()


def extract_reviews_info(maketitle: HtmlElement) -> Optional[HtmlElement]:
    """Extract Book and Conference Review info from div.maketitle."""
    # The part that we are interested in starts with a <h2> (please
    # note that this method is called _after_ headings have been
    # promoted), then some texts and <br/>s.
    # It should be at the end of the div.maketitle. E.g.:
    #
    # For a CONFERENCE review
    # ...
    #   <h2 class="likesectionHead"><a id="x1-3000"/>Contents</h2>
    #
    #   <h2 class="likesectionHead"><a id="x1-4000"/>Reviewed Conference<a id="Q1-1-7"/></h2>
    #   Forum Wissenschaftskommunikation 2022<br/>
    #   Leibniz Universit&#228;t, Hannover, Germany, 4&#8211;6 October 2022
    # </div>
    # ...
    #
    # OR, for a BOOK review
    # ...
    #   <h2 class="likesectionHead"><a id="x1-3000"/>Contents</h2>
    #
    #   <h2 class="likesectionHead"><a id="x1-4000"></a>Reviewed Book<a id="Q1-1-7"></a></h3>
    #   Christiansen, J. (2023).<br/>
    #   Building Science Graphics: an... diagrams and visualizations.<br/>
    #   Boca Raton &amp; Oxon: CRC Press
    # </div>
    # ...
    #
    # Per evitare questo, dovremmo spostare il comando al di fuori del maketitle
    #
    header: HtmlElement = None
    found = False
    for header in maketitle.findall("h2"):
        if header_text := header.text_content():
            if header_text.startswith("Reviewed"):
                found = True
                break

    if not found:
        return None

    logger.debug(f'Found "{header_text}"')
    review_header_index = maketitle.index(header)

    # I prefer to have an element to move around
    wrapper = lxml.html.etree.Element("div")
    wrapper.append(header)
    # Let's also encapsulate the text in a <p>
    p = lxml.html.etree.SubElement(wrapper, "p")
    p.text = header.tail
    header.tail = None

    # For a conference review, I expect header + title + venue = 3 items
    # For a book review, I expect header + author + title + publisher = 4 items
    index_from_bottom = review_header_index - len(maketitle) - 1
    if index_from_bottom not in (-3, -4):
        logger.warning("Please check info about review in the galley.")

    elements_to_keep = maketitle[review_header_index:]
    for element in elements_to_keep:
        p.append(element)
    return wrapper


def drop_frontmatter(html: HtmlElement):
    """Drop <head> and the div.maketitle, but keep reivews info if present."""
    heads = html.findall("head")

    if len(heads) != 1:
        logger.error(f"Found {len(heads)} (expected 1). Proceeding anyway")
    for head in heads:
        html.remove(head)

    maketitle: HtmlElement = html.find(".//div[@class='maketitle']")
    if maketitle is None:
        logger.error("No <div class='maketitle'> found!")
        return

    review_data = extract_reviews_info(maketitle)
    if review_data is not None:
        html.insert(0, review_data)

    maketitle.drop_tree()


def remove_images_dimensions(html: HtmlElement):
    """Remove dimensions from <img> tags, let Janeway decide."""
    for img in html.findall(".//img"):
        if "width" in img.attrib:
            del img.attrib["width"]
        if "height" in img.attrib:
            del img.attrib["height"]


def standalone_html_to_fragment(html_element: HtmlElement):
    """Replace tag <html> with a <div> and drop tag <body>."""
    if html_element.tag != "html":
        logger.debug("No <html> tag found as root element. Nothing to do.")
        return
    html_element.tag = "div"
    html_element.find(".//body").drop_tag()


def process_body(body: str, style=None, lang="eng") -> bytes:
    """Rewrite and adapt body / full-text HTML to match Janeway's expectations.

    Take care of
    - TOC (heading levels)
    - how-to-cite

    Images included in body are done elsewhere since they require an existing galley.
    """
    html = lxml.html.fromstring(body)
    standalone_html_to_fragment(html)

    # src/themes/material/assets/toc.js expects
    # - the root element of the article must have id="main_article"
    html.set("id", "main_article")
    # - the headings that go in the toc must be h2-level, but Drupal has them at h3-level
    promote_headings(html)
    drop_toc(html)
    drop_how_to_cite(html, lang=lang)
    if style == "wjapp":
        drop_frontmatter(html)
        remove_images_dimensions(html)
    return lxml.html.tostring(html)


def evince_language_from_filename_and_article(filename: str, article):
    """Evince the galley's language from its filename.

    NB: in JCOMAL, the main galley (which is never English) does not
    have the lang. indication in the filename. We hope that the
    article's language has been set correctly...

    """
    if "_es" in filename:
        return "spa"
    if "_pt" in filename:
        return "por"
    return article.language
