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
    "Netherlands (the)": "Netherlands",
    "Philippines (the)": "Philippines",
    "Russian Federation (the)": "Russian Federation",
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


def decide_galley_label(pubid, file_name: str, file_mimetype: str):
    """Decide the galley's label."""
    # Remember that we can have ( PDF + EPUB galley ) x languages (usually two),
    # so a label of just "PDF" might not be sufficient.
    lang_match = re.search(r"_([a-z]{2,3})\.", file_name)
    mime_to_extension = {
        "application/pdf": "PDF",
        "application/epub+zip": "EPUB",
    }
    label = mime_to_extension.get(file_mimetype, None)
    if label is None:
        logger.error("""Unknown mime type "%s" for %s""", file_mimetype, pubid)
        label = "Other"
    language = None
    if lang_match is not None:
        language = lang_match.group(1)
        language = FUNNY_LANGUAGE_CODES.get(language, language)
        label = f"{label} ({language})"
    return (label, language)


def set_language(article, language):
    """Set the article's language.

    Must map from Drupal's iso639-2 (two chars) to Janeway iso639-3 (three chars).
    """
    # Some non-standard language codes have been used in JCOM through the years...
    language = FUNNY_LANGUAGE_CODES.get(language, language)
    lang_obj = pycountry.languages.get(alpha_2=language)
    if lang_obj is None:
        logger.error(
            'Unknown language code "%s" for %s. Keeping default "English"',
            language,
            article.get_identifier("pubid"),
        )
        return
    if lang_obj.alpha_3 not in JANEWAY_LANGUAGES_BY_CODE:
        logger.error(
            'Unknown language "%s" (from "%s") for %s. Keeping default "English"',
            lang_obj.alpha_3,
            language,
            article.get_identifier("pubid"),
        )
        return

    article.language = lang_obj.alpha_3

    # Small sanity check
    if lang_obj.name not in JANEWAY_LANGUAGES_BY_CODE.values():
        # We know about "Spanish" vs. "Spanish; Castilian" and it's ok to keep the latter.
        if lang_obj.name != "Spanish":
            logger.warning(
                """ISO639 language for "%s" is "%s" and is different from Janeway's "%s" (using the latter) for %s""",
                language,
                lang_obj.name,
                JANEWAY_LANGUAGES_BY_CODE[lang_obj.alpha_3],
                article.get_identifier("pubid"),
            )
    article.save()


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
    htc_h2 = html.xpath(f".//h2[text()='{how_to_cite[lang]}']")
    if len(htc_h2) == 0:
        logger.warning("No How-to-cite in WRITEME!!!")
        return

    if len(htc_h2) > 1:
        logger.error("Multiple How-to-cites in WRITEME!!!")

    htc_h2 = htc_h2[0]
    max_expected = 3
    count = 0
    while True:
        # we are going to `drop_tree` this element, so `getnext()`
        # should provide for new elments
        p = htc_h2.getnext()
        count += 1
        if count > max_expected:
            logger.warning("Too many elements after How-to-cite's H2 in WRITEME!!!")
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
    # promoted), then some text, then a <br/>, then some final text.
    # It should be at the end of the div.maketitle. E.g.:
    #
    # ...
    # <h2 class="likesectionHead"><a id="x1-3000"/>Contents</h2>
    #
    # <h2 class="likesectionHead"><a id="x1-4000"/>Reviewed Conference<a id="Q1-1-7"/></h2>
    # Forum Wissenschaftskommunikation 2022<br/>
    # Leibniz Universit&#228;t, Hannover, Germany, 4&#8211;6 October 2022
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

    if not header == maketitle[-2]:
        logger.error("Reviewd info found in unexpected place. Trying to continue.")

    # I prefer to have an element to move around
    wrapper = lxml.html.etree.Element("div")
    wrapper.append(header)
    # Let's also encapsulate the text in a <p>
    p = lxml.html.etree.SubElement(wrapper, "p")

    # The first text line should be the conference's title...
    p.text = header.tail
    header.tail = None

    br = maketitle[-1]
    if br.tag != "br":
        logger.error(f"Unexpected tag {br} found. Trying to continue.")

    p.append(br)
    # ...and the second text line should be the conference's venue and date.
    if not br.tail:
        logger.error("Missing expect final text. Trying to continue.")
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
        del img.attrib["width"]
        del img.attrib["height"]


def process_body(body: str, style=None, lang="eng") -> bytes:
    """Rewrite and adapt body / full-text HTML to match Janeway's expectations.

    Take care of
    - TOC (heading levels)
    - how-to-cite

    Images included in body are done elsewhere since they require an existing galley.
    """
    html = lxml.html.fromstring(body)

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
