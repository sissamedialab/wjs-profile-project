"""Views that manage redirect from Drupal-style URLs to Janeway."""

import re

from core.models import Account, Galley, SupplementaryFile
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.text import slugify
from django.views.generic import RedirectView
from journal.models import Issue
from submission.models import Article
from utils.logger import get_logger

logger = get_logger(__name__)

# JCOM's earliest years (2002-2006) have archive filenames like
# "jcom<volume><issue>(<year>)<type><num>.pdf", e.g. "jcom0302(2004)A01.pdf", but the article's
# actual pubid is "<type><volume><issue><num>", e.g. "A030201" (the type letter moves in front of
# volume/issue; <num> is omitted for types with a single instance per issue, e.g. editorials:
# "jcom0302(2004)E.pdf" -> pubid "E0302"). Confirmed against real DB data (specs#3265).
#
# 2006-2013 archive filenames have the same shape, but there the pubid IS stored verbatim in this
# shape, so the direct (untranslated) lookup already matches and this translation is never reached
# for them - confirmed by grouping identifiers_identifier.identifier by shape and publication year.
#
# The "jcom" prefix casing is inconsistent across the old archive ("jcom"/"Jcom"/"JCOM" all occur,
# confirmed from real pubids), so it is matched case-insensitively; the type letter is not, since
# it is copied as-is into the rebuilt pubid, and the DB always has it uppercase.
LEGACY_DRUPAL_PUBID_RE = re.compile(
    r"^[Jj][Cc][Oo][Mm](?P<volume>\d{2})(?P<issue>\d{2,3})\(\d{4}\)(?P<type>[A-Za-z])(?P<num>\d{0,2})$"
)


def legacy_drupal_pubid_to_pubid(raw_pubid: str) -> str | None:
    """Translate a very old Drupal-era archive filename's pubid into the article's actual pubid.

    Return None if ``raw_pubid`` does not match the legacy shape (e.g. it is already a modern
    "JCOM_XXXX_YYYY_Axx"-style pubid, which embeds the real pubid directly and needs no translation).
    """
    match = LEGACY_DRUPAL_PUBID_RE.match(raw_pubid)
    if not match:
        return None
    return f"{match['type']}{match['volume']}{match['issue']}{match['num']}"


class JcomIssueRedirect(RedirectView):
    permanent = True
    query_string = True

    def get_redirect_url(self, *args, **kwargs):  # noqa
        issues = Issue.objects.filter(
            journal=self.request.journal,
            volume=int(kwargs["volume"]),
            issue=f"{int(kwargs['issue']):02d}",
        ).order_by("-date")
        if issues.count() > 1:
            logger.warning(
                f"Warning, more than 1 issue found for volume {kwargs['volume']} and issue {kwargs['issue']}",
            )
        if not issues.first():
            raise Http404()

        redirect_location = reverse(
            "journal_issue",
            kwargs={
                "issue_id": issues.first().pk,
            },
        )
        return redirect_location


class JcomFileRedirect(RedirectView):
    """Redirect files (galleys).

    Take language in consideration (JCOM accepts submissions in some
    languages other than english).

    The url path can also contain an "error" parts that is discarded.

    Examples
    --------
    - simplest case
      JCOM_2106_2022_A04.epub     --> galley.label == "EPUB"

    - language in file name _en _pt ...
      JCOM_2107_2022_A05_pt.epub  --> galley.label == "EPUB (pt)"
      JCOM_2107_2022_A05_en.epub  --> galley.label == "EPUB (en)"

    - errors in file name  _0 _1 ...
      JCOM_2106_2022_A04_0.epub    --> galley.label == "EPUB"
      JCOM_2107_2022_A05_en_0.epub --> galley.label == "EPUB (en)"

    """

    permanent = True
    query_string = True

    def get_redirect_url(self, *args, **kwargs):  # noqa
        # NB: Article.get_article does *not* raise Article.DoesNotExist, just returns None
        article = Article.get_article(
            journal=self.request.journal,
            identifier_type="pubid",
            identifier=kwargs["pubid"],
        )
        if article is None and (legacy_pubid := legacy_drupal_pubid_to_pubid(kwargs["pubid"])):
            # Very old archive URL (see legacy_drupal_pubid_to_pubid()): the raw filename is not
            # itself the pubid, translate it before giving up.
            article = Article.get_article(
                journal=self.request.journal,
                identifier_type="pubid",
                identifier=legacy_pubid,
            )
        if article is None:
            raise Http404()

        redirect = None

        # For citation_pdf_url URLs
        if galley_id := kwargs.get("galley_id", None):
            galley = get_object_or_404(
                Galley,
                id=galley_id,
            )
            # TODO: refactor me!
            redirect = reverse(
                "article_download_galley",
                kwargs={
                    "article_id": article.pk,
                    "galley_id": galley.pk,
                },
            )
            # For supllementary material files
        elif attachment_part := kwargs.get("attachment", None):
            supplementary_file_label = kwargs["pubid"] + attachment_part
            try:
                supplementary_file = article.supplementary_files.get(file__label=supplementary_file_label)
            except SupplementaryFile.DoesNotExist:
                raise Http404()
            else:
                redirect = reverse(
                    "article_download_supp_file",
                    kwargs={
                        "article_id": article.pk,
                        "supp_file_id": supplementary_file.pk,
                    },
                )

        else:
            # For old Drupal files
            galley_label = kwargs["extension"].upper()
            if language := kwargs.get("language", None):
                galley_label = f"{galley_label} ({language})"
            galley = get_object_or_404(
                Galley,
                label=galley_label,
                article=article,
                public=True,
            )
            # TODO: refactor me!
            redirect = reverse(
                "article_download_galley",
                kwargs={
                    "article_id": article.pk,
                    "galley_id": galley.pk,
                },
            )

        return redirect


class DrupalKeywordsRedirect(RedirectView):
    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        """Find the kwd using the old slug and redirect to the kwd's new URL."""
        old_slug = kwargs["kwd_slug"]
        journal_keywords = self.request.journal.keywords.all().values("id", "word")
        for kwd in journal_keywords:
            if slugify(kwd["word"]) == old_slug:
                return f"/articles/keyword/{kwd['id']}/"
        raise Http404()


class DrupalAuthorsRedirect(RedirectView):
    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        """Find the author using the old slug and redirect to the author's new URL."""
        old_slug = kwargs["author_slug"]
        if kwargs.get("jcomal_lang", None) is not None:
            accents_to_ascii = True
        else:
            # JCOM used "-"
            accents_to_ascii = False
        pieces = old_slug.split("-")

        # Watch out for  O'hara --> ohara (use only initials)
        if len(pieces) == 2:
            # let's cover first the most common case (which should yield smaller results)
            first_initial = pieces[0][0]
            last_initial = pieces[-1]
            authors = Account.objects.filter(
                first_name__istartswith=first_initial,
                last_name__istartswith=last_initial,
            ).values("id", "first_name", "last_name")
            for author in authors:
                if (
                    drupal_style_slugify(
                        [author["first_name"], author["last_name"]],
                        accents_to_ascii=accents_to_ascii,
                    )
                    == old_slug
                ):
                    return f"/articles/author/{author['id']}/"
            raise Http404()

        else:
            # here I'm not sure where the middle/last name starts...
            first_initial = pieces[0][0]
            authors = Account.objects.filter(
                first_name__istartswith=first_initial,
            ).values("id", "first_name", "middle_name", "last_name")

            for author in authors:
                # Mario M. Rossi --> mario-m-rossi
                # Maria Antonia Fiorenza della Valle Bruna
                if (
                    drupal_style_slugify(
                        [
                            author["first_name"],
                            author["middle_name"],
                            author["last_name"],
                        ],
                        accents_to_ascii=accents_to_ascii,
                    )
                    == old_slug
                ):
                    return f"/articles/author/{author['id']}/"
            raise Http404()


def drupal_style_slugify(elements, accents_to_ascii=True):
    """Slugify ala Drupal.

    - JCOM style: "-" in place of accented chars
    - JCOMAL style: same as django's slugify
    """
    full_name = " ".join([name for name in elements if name])

    if accents_to_ascii:
        django_slug = slugify(full_name, allow_unicode=False)
    else:
        django_slug = slugify(full_name, allow_unicode=True)
        django_slug = "".join([c if ord(c) <= 127 else "-" for c in django_slug])

    return django_slug


class FaviconRedirect(RedirectView):
    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        """Get the url of the favicon for this journal."""
        if self.request.journal and self.request.journal.favicon:
            return self.request.journal.favicon.url
        return Http404()
