import datetime
from pathlib import Path

from core import files
from core import models as core_models
from core.models import Account
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.db.models import Count
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404
from journal.models import Journal
from plugins.wjs_submission.models import Collaboration
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from ..logic import (
    states_when_article_is_considered_in_production,
    states_when_article_is_considered_production_archived,
    states_when_article_is_considered_typesetter_working_on,
)
from ..models import ArticleWorkflow
from .const import (
    COLLABORATIONS_EXPORT_VERSION,
    PUBLIC_LISTING_DEFAULT,
    PUBLIC_LISTING_FILTERS,
    TYPE_TO_MIME,
)
from .mixins import (
    EOOrTypesetterAccessMixin,
    LoggedRequestMixin,
    PublishedArticleAccessMixin,
)
from .permissions import IsEOOrTypesetterForArticle
from .serializers import (
    CollaborationSerializer,
    GalleyUploadSerializer,
    ProductionArticleSerializer,
    TypesetterPapersListSerializer,
)


class ArticleZipDownloadView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    def get(self, request, pk: int):
        """
        Download a zip file containing all galleys for the given article.
        """
        article = self.get_article(request, pk)

        try:
            article_workflow = article.articleworkflow
        except ObjectDoesNotExist:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        core_file = getattr(article_workflow, "publication_galleys_source_file", None)
        if not core_file:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        galleys_filename = core_file.uuid_filename
        galleys_path = Path(article.folder_path()) / galleys_filename
        if not galleys_path.exists():
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return files.serve_file_to_browser(file_path=galleys_path, file_to_serve=core_file, public=True)


class ArticleGalleyListView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    def get(self, request, pk: int):
        """
        List all galleys for the given article.
        """
        article = self.get_article(request, pk)

        galleys = article.galley_set.all().order_by("type")

        type_counts = {
            row["type"]: row["cnt"] for row in (article.galley_set.values("type").annotate(cnt=Count("id")))
        }

        items = []
        for galley in galleys:
            core_file = getattr(galley, "file", None)
            if not core_file:
                continue

            file_type = galley.type
            sequence = galley.sequence

            if file_type == "image":
                content_type = "image/*"
            else:
                content_type = sorted(TYPE_TO_MIME.get(file_type, {"application/octet-stream"}))[0]

            if type_counts.get(file_type, 0) > 1:
                download_url = (
                    f"/plugins/wjs-review-articles/api/v1/article/{article.pk}/galley/{file_type}/{sequence}/"
                )
            else:
                download_url = f"/plugins/wjs-review-articles/api/v1/article/{article.pk}/galley/{file_type}/"

            items.append(
                {
                    "type": file_type,
                    "sequence": sequence,
                    "filename": core_file.original_filename,
                    "contentType": content_type,
                    "download_url": download_url,
                }
            )

        return Response({"article_id": article.pk, "items": items}, status=status.HTTP_200_OK)


class ArticleGalleyView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    def _validate_type(self, type_: str):
        if type_ not in [key for key, _ in core_models.galley_type_choices()]:
            return False
        return True

    def get(self, request, pk: int, file_type: str, sequence: int = None):
        """
        Download a galley file for the given article.
        """
        if not self._validate_type(file_type) is not None:
            return Response(
                {"error": {"code": "TYPE_NOT_FOUND", "message": "Invalid parameters."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        article = self.get_article(request, pk)

        qs = core_models.Galley.objects.filter(article=article, type=file_type)
        if sequence is not None:
            qs = qs.filter(sequence=sequence)
        count = qs.count()
        if count == 0:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if count > 1:
            return Response(
                {
                    "error": {
                        "code": "DATA_INTEGRITY_CONFLICT",
                        "message": "Multiple galleys found for the same article/type/sequence",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        galley = qs.first()
        core_file = getattr(galley, "file", None)
        if not core_file:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        file_path = core_file.self_article_path()
        return files.serve_file_to_browser(file_path=file_path, file_to_serve=core_file, public=True)

    def post(self, request, pk: int, file_type: str, sequence: int = None):
        """
        Upload or replace a galley file for the given article.
        """
        if not self._validate_type(file_type) is not None:
            return Response(
                {"error": {"code": "TYPE_NOT_FOUND", "message": "Invalid parameters."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        article = self.get_article(request, pk)

        qs = core_models.Galley.objects.filter(article=article, type=file_type)
        if sequence is not None:
            qs = qs.filter(sequence=sequence)
        count = qs.count()
        if count == 0:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if count > 1:
            return Response(
                {
                    "error": {
                        "code": "DATA_INTEGRITY_CONFLICT",
                        "message": "Multiple galleys found for the same article/type/sequence",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        galley = qs.first()
        core_file = getattr(galley, "file", None)
        if not core_file:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = GalleyUploadSerializer(
            data={},
            context={"request": request},
            galley_type=file_type,
        )
        serializer.is_valid(raise_exception=True)

        uploaded_file = ContentFile(serializer.validated_data["data"], name=core_file.original_filename)

        files.overwrite_file(
            uploaded_file=uploaded_file,
            file_to_replace=core_file,
            path_parts=["articles", str(article.pk)],
        )

        resp = Response(status=status.HTTP_201_CREATED)
        resp["Location"] = f"/api/v1/article/{article.pk}/galley/{file_type}/"
        if sequence is not None:
            resp["Location"] += f"{sequence}/"
        return resp


class CollaborationListView(LoggedRequestMixin, EOOrTypesetterAccessMixin, ListAPIView):
    """
    List all collaborations, with the same data as "tabellone.json".

    The collaborations are ordered by short name and can be filtered by the "public_listing" query
    parameter: "all" (the default) returns every collaboration, "true" only the ones approved
    for public listing, "false" only the ones that are not.
    """

    serializer_class = CollaborationSerializer
    #: The entry point lists every collaboration, so the result must not be split into pages.
    pagination_class = None
    # LOWER() because the database collation is byte-ordered (C.UTF-8): a plain ORDER BY would
    # push every lowercase-initial short name (lpGBT, nEXO, sPHENIX, ...) past "ZEUS".
    queryset = Collaboration.objects.order_by(Lower("short_name"), "short_name")

    def get_public_listing(self) -> str:
        """
        Read the "public_listing" query parameter.

        :return: The requested filter, lowercased, or the default one when the parameter is not given.
        :rtype: str
        """
        return self.request.query_params.get("public_listing", PUBLIC_LISTING_DEFAULT).lower()

    def get_queryset(self):
        """
        Select the collaborations to export, filtered as requested by "public_listing".

        :return: The collaborations to serve.
        :rtype: QuerySet
        """
        queryset = super().get_queryset()
        wanted = PUBLIC_LISTING_FILTERS[self.get_public_listing()]
        if wanted is not None:
            queryset = queryset.filter(public_listing=wanted)
        return queryset

    def list(self, request, *args, **kwargs):  # noqa: A003 (DRF's own hook name)
        """
        Serve the collaborations wrapped in the same envelope as "tabellone.json".

        :return: The versioned list of collaborations, or an error when "public_listing" is unknown.
        :rtype: Response
        """
        if self.get_public_listing() not in PUBLIC_LISTING_FILTERS:
            return Response(
                {
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "Invalid parameters.",
                        "details": {
                            "public_listing": {
                                "expected": sorted(PUBLIC_LISTING_FILTERS),
                                "got": request.query_params.get("public_listing"),
                            }
                        },
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = {
            "version": COLLABORATIONS_EXPORT_VERSION,
            "collaborations": self.get_serializer(self.filter_queryset(self.get_queryset()), many=True).data,
        }
        return Response(payload, status=status.HTTP_200_OK)


class TypesetterPapersListView(LoggedRequestMixin, APIView):
    """
    G8 - List the papers a typesetter has handled/is handling, for EO workload monitoring.

    Papers with a TypesettingAssignment to the given typesetter, assigned in
    [start_date, end_date] (both extremes included, TypesettingAssignment.assigned), in state
    TypesetterSelected / Proofreading / ReadyForPublication / Published. See Specifications.md §3.7.

    Authentication/permission: same stack as the other API endpoints (Token
    authentication + IsEOOrTypesetterForArticle): on a list route only the
    has_permission part applies, letting EO and any typesetter in; the finer
    object-level check (is a typesetter *of this article*) would need an object,
    so it is not used here. The permission model is going to be refactored using
    permission classes; for now we reuse it as is (see Specifications.md §3.7).
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsEOOrTypesetterForArticle]

    def _date_range(self, request):
        """Parse the mandatory start_date=/end_date= params into a __range pair (inclusive)."""
        start_raw, end_raw = request.query_params.get("start_date"), request.query_params.get("end_date")
        if not start_raw or not end_raw:
            return None, Response(
                {
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "Missing required query parameters: start_date and end_date.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            start, end = datetime.date.fromisoformat(start_raw), datetime.date.fromisoformat(end_raw)
        except ValueError:
            return None, Response(
                {
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "Invalid date format: start_date and end_date must be ISO dates (YYYY-MM-DD).",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # TypesettingAssignment.assigned is a DateTime: build an inclusive datetime range so
        # that both extremes are included. Dates are midnight UTC, safely inside the window
        # whatever the server's TIME_ZONE is (±12h).
        return (
            (
                datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc),
                datetime.datetime.combine(end, datetime.time.max, tzinfo=datetime.timezone.utc),
            ),
            None,
        )

    def get(self, request, code: str, typesetter_pk: int):
        date_range, error = self._date_range(request)
        if error:
            return error

        get_object_or_404(Account, pk=typesetter_pk)  # unknown typesetter pk → 404

        workflows = (
            ArticleWorkflow.objects.filter(
                article__journal__code=code,
                article__typesettinground__isnull=False,
                article__typesettinground__typesettingassignment__typesetter__pk=typesetter_pk,
                article__typesettinground__typesettingassignment__assigned__range=date_range,
                state__in=states_when_article_is_considered_typesetter_working_on
                + states_when_article_is_considered_production_archived,
            )
            .distinct()
            .order_by("-article__date_accepted")
            .select_related("article", "article__journal")
        )
        return Response(
            TypesetterPapersListSerializer(workflows, many=True, context={"typesetter_pk": typesetter_pk}).data
        )


class JournalProductionListView(LoggedRequestMixin, EOOrTypesetterAccessMixin, ListAPIView):
    """
    G7 - Articles of a journal that are currently in production (excluding ACCEPTED). See Specifications.md §3.6.

    Uses the same LoggedRequestMixin/EOOrTypesetterAccessMixin/ListAPIView pattern as
    CollaborationListView above -- unlike TypesetterPapersListView (G8), which predates and uses
    a different (raw APIView + TokenAuthentication) stack; the two endpoints are independent and
    don't need to share a view base, only ProductionArticleSerializer's shared fields (see
    ProductionBaseSerializer in serializers.py).
    """

    serializer_class = ProductionArticleSerializer
    #: A journal's in-production articles are bounded like its collaborations list (not "all
    #: articles ever"); matches CollaborationListView's own no-pagination precedent above, and
    #: TypesetterPapersListView (G8) also returns a flat array, not a paginated envelope.
    pagination_class = None

    def get_queryset(self):
        journal = get_object_or_404(Journal, code=self.kwargs["code"].upper())
        qs = ArticleWorkflow.objects.filter(
            article__journal=journal,
            state__in=set(states_when_article_is_considered_in_production) - {ArticleWorkflow.ReviewStates.ACCEPTED},
        ).select_related("article", "article__journal")
        return qs
