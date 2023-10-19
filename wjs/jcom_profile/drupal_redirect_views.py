"""Views that manage redirect from Drupal-style URLs to Janeway."""
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
