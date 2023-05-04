"""Views that manage redirect from Drupal-style URLs to Janeway."""
from core.models import Galley, SupplementaryFile
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
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
            if language := kwargs["language"]:
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
