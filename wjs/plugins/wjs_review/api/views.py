import datetime
from pathlib import Path

from core import files
from core import models as core_models
from core.models import Account
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.db.models import Count
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from journal.models import Journal
from plugins.wjs_submission.models import Collaboration
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from wjs.jcom_profile.utils import create_rich_fake_request

from ..logic import (
    states_when_article_is_considered_in_production,
    states_when_article_is_considered_production_archived,
    states_when_article_is_considered_typesetter_working_on,
)
from ..models import ArticleWorkflow
from .const import (
    COLLABORATIONS_EXPORT_VERSION,
    GALLEY_DOWNLOAD_MEDIA_TYPES,
    GALLEY_UPLOAD_MEDIA_TYPES,
    PUBLIC_LISTING_DEFAULT,
    PUBLIC_LISTING_FILTERS,
    SOURCE_ZIP_MEDIA_TYPES,
    TYPE_TO_MIME,
    ZIP_MEDIA_TYPE,
)
from .exceptions import (
    BadRequest,
    DataIntegrityConflict,
    GalleyGenerationFailed,
    InvalidGalleyType,
    ResourceNotFound,
)
from .logic import INVALID_REQUEST_CODE, ReplaceSourceZipAndRegenerateGalleys
from .mixins import (
    EOOrTypesetterAccessMixin,
    IgnoreAcceptHeaderNegotiation,
    LoggedRequestMixin,
    PublishedArticleAccessMixin,
)
from .parsers import RawFileUploadParser
from .permissions import IsEOOrTypesetterForArticle
from .serializers import (
    ArticleGalleyListSerializer,
    CollaborationSerializer,
    CollaborationsExportSerializer,
    ErrorSerializer,
    GalleyUploadSerializer,
    ProductionArticleSerializer,
    RegeneratedGalleysSerializer,
    SourceZipUploadSerializer,
    TypesetterPapersListSerializer,
)


class ArticleZipView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    """Serve and replace the archive with the sources of a published article's galleys."""

    #: The archive is sent as the bare request body, by both the client and this API's own GET.
    parser_classes = [RawFileUploadParser]
    #: The GET serves a zip, whose media type this API's schema advertises: honour a client that
    #: asks for it (or for anything else) instead of answering 406.
    content_negotiation_class = IgnoreAcceptHeaderNegotiation

    # The media types of the 2xx responses are spelled out as `(code, *media_types)` keys: without
    # them drf-spectacular derives the media types from the view's renderers, which are the default
    # JSON ones -- the view needs them for its error envelopes, but the file itself is not JSON.
    @extend_schema(
        responses={
            (200, ZIP_MEDIA_TYPE): OpenApiResponse(
                response=OpenApiTypes.BINARY, description="The requested zip file."
            ),
            404: ErrorSerializer,
        },
    )
    def get(self, request, pk: int):
        """
        Download a zip file containing the sources of the published galleys for the given article.
        """
        article = self.get_article(request, pk)

        try:
            article_workflow = article.articleworkflow
        except ObjectDoesNotExist:
            raise ResourceNotFound("Requested resource was not found.")

        core_file = getattr(article_workflow, "publication_galleys_source_file", None)
        if not core_file:
            raise ResourceNotFound("Requested resource was not found.")
        sources_filename = core_file.uuid_filename
        sources_path = Path(article.folder_path()) / sources_filename
        if not sources_path.exists():
            raise ResourceNotFound("Requested resource was not found.")
        return files.serve_file_to_browser(file_path=sources_path, file_to_serve=core_file, public=True)

    # As for the GET above, the uploaded body is keyed by media type, so that the schema lists the
    # Content-Types `SourceZipUploadSerializer` accepts instead of the JSON/form ones that the
    # view's parsers would imply.
    @extend_schema(
        request={media_type: OpenApiTypes.BINARY for media_type in SOURCE_ZIP_MEDIA_TYPES},
        description=(
            "Replace the archive the article's galleys are built from, and regenerate every "
            "galley out of it. The request body is the raw zip archive."
        ),
        responses={
            200: RegeneratedGalleysSerializer,
            400: ErrorSerializer,
            404: ErrorSerializer,
            415: ErrorSerializer,
            502: ErrorSerializer,
        },
    )
    def put(self, request, pk: int):
        """
        Replace the sources of a published article's galleys and regenerate the galleys.

        This is what the obsolete "regen_galleys" management command used to do by hand: the
        client downloads the sources with GET, edits them, and sends them back here.

        :return: the regenerated galleys.
        :rtype: Response

        :raises ResourceNotFound: if the article has no workflow.
        :raises BadRequest: if the uploaded archive is not this article's.
        :raises GalleyGenerationFailed: if the galleys could not be rebuilt from the new sources.
        """
        article = self.get_article(request, pk)

        try:
            article_workflow = article.articleworkflow
        except ObjectDoesNotExist:
            raise ResourceNotFound("Requested resource was not found.")

        serializer = SourceZipUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        try:
            galleys = ReplaceSourceZipAndRegenerateGalleys(
                workflow=article_workflow,
                source_zip=serializer.validated_data["file"],
                user=request.user,
                # The galley machinery expects the kind of request that the publication process
                # (which runs asynchronously) builds for itself.
                request=create_rich_fake_request(journal=article.journal, settings=settings, user=request.user),
            ).run()
        except DjangoValidationError as exception:
            message = " ".join(exception.messages)
            # What the caller sent is their problem to fix, the rest is ours to report as a failure.
            if getattr(exception, "code", None) == INVALID_REQUEST_CODE:
                raise BadRequest(message)
            raise GalleyGenerationFailed(message)

        result_serializer = RegeneratedGalleysSerializer(instance={"article_id": article.pk, "galleys": galleys})
        return Response(result_serializer.data, status=status.HTTP_200_OK)


class ArticleGalleyListView(LoggedRequestMixin, PublishedArticleAccessMixin, GenericAPIView):
    serializer_class = ArticleGalleyListSerializer

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

        serializer = self.get_serializer(instance={"article_id": article.pk, "items": items})
        return Response(serializer.data, status=status.HTTP_200_OK)


class ArticleGalleyView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    #: The GET serves a galley, whose media type this API's schema advertises: honour a client
    #: that asks for it (or for anything else) instead of answering 406.
    content_negotiation_class = IgnoreAcceptHeaderNegotiation

    def _validate_type(self, type_: str):
        if type_ not in [key for key, _ in core_models.galley_type_choices()]:
            return False
        return True

    # As for the zip entry point, the 200 response's media types are given explicitly. The served
    # one is the one matching the "file_type" path parameter (see `TYPE_TO_MIME`), which is not
    # statically knowable here, so every type this entry point can serve is listed.
    @extend_schema(
        responses={
            (200, *GALLEY_DOWNLOAD_MEDIA_TYPES): OpenApiResponse(
                response=OpenApiTypes.BINARY,
                description=(
                    "The requested galley file. It is served with the media type matching the "
                    "galley's `file_type` (see `TYPE_TO_MIME`); image galleys keep the media type "
                    "of the stored image."
                ),
            ),
            400: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    )
    def get(self, request, pk: int, file_type: str, sequence: int = None):
        """
        Download a galley file for the given article.
        """
        if not self._validate_type(file_type) is not None:
            raise InvalidGalleyType("Invalid parameters.")

        article = self.get_article(request, pk)

        qs = core_models.Galley.objects.filter(article=article, type=file_type)
        if sequence is not None:
            qs = qs.filter(sequence=sequence)
        count = qs.count()
        if count == 0:
            raise ResourceNotFound("Requested resource was not found.")
        if count > 1:
            raise DataIntegrityConflict("Multiple galleys found for the same article/type/sequence")

        galley = qs.first()
        core_file = getattr(galley, "file", None)
        if not core_file:
            raise ResourceNotFound("Requested resource was not found.")

        file_path = core_file.self_article_path()
        return files.serve_file_to_browser(file_path=file_path, file_to_serve=core_file, public=True)

    # The request body is keyed by media type so that the schema lists exactly the Content-Types
    # `GalleyUploadSerializer` accepts. Left to drf-spectacular, the media types would come from the
    # view's parsers, which advertise JSON, form-urlencoded and multipart -- all of them rejected.
    @extend_schema(
        request={media_type: OpenApiTypes.BINARY for media_type in GALLEY_UPLOAD_MEDIA_TYPES},
        description=(
            "Upload or replace a galley file. The request body is the raw file content; "
            "its Content-Type must match the expected MIME type(s) for `file_type` "
            "(see `TYPE_TO_MIME`)."
        ),
        responses={
            201: None,
            400: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    )
    def post(self, request, pk: int, file_type: str, sequence: int = None):
        """
        Upload or replace a galley file for the given article.
        """
        if not self._validate_type(file_type) is not None:
            raise InvalidGalleyType("Invalid parameters.")

        article = self.get_article(request, pk)

        qs = core_models.Galley.objects.filter(article=article, type=file_type)
        if sequence is not None:
            qs = qs.filter(sequence=sequence)
        count = qs.count()
        if count == 0:
            raise ResourceNotFound("Requested resource was not found.")
        if count > 1:
            raise DataIntegrityConflict("Multiple galleys found for the same article/type/sequence")

        galley = qs.first()
        core_file = getattr(galley, "file", None)
        if not core_file:
            raise ResourceNotFound("Requested resource was not found.")

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


class CollaborationListView(LoggedRequestMixin, EOOrTypesetterAccessMixin, GenericAPIView):
    """
    List all collaborations, with the same data as "tabellone.json".

    The collaborations are ordered by short name and can be filtered by the "public_listing" query
    parameter: "all" (the default) returns every collaboration, "true" only the ones approved
    for public listing, "false" only the ones that are not.

    It is a `GenericAPIView` serving `get()`, not a `ListAPIView`: the response is a single
    envelope object, and drf-spectacular wraps the response of any `ListModelMixin` view in an
    array (`AutoSchema._is_list_view()`), which `@extend_schema(responses=...)` cannot undo.
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

    @extend_schema(responses={200: CollaborationsExportSerializer, 400: ErrorSerializer})
    def get(self, request, *args, **kwargs):
        """
        Serve the collaborations wrapped in the same envelope as "tabellone.json".

        :return: The versioned list of collaborations, or an error when "public_listing" is unknown.
        :rtype: Response
        """
        if self.get_public_listing() not in PUBLIC_LISTING_FILTERS:
            raise BadRequest(
                "Invalid parameters.",
                details={
                    "public_listing": {
                        "expected": sorted(PUBLIC_LISTING_FILTERS),
                        "got": request.query_params.get("public_listing"),
                    }
                },
            )

        export_serializer = CollaborationsExportSerializer(
            instance={
                "version": COLLABORATIONS_EXPORT_VERSION,
                "collaborations": self.filter_queryset(self.get_queryset()),
            },
            context=self.get_serializer_context(),
        )
        return Response(export_serializer.data, status=status.HTTP_200_OK)


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
        """
        Parse the mandatory start_date=/end_date= params into a __range pair (inclusive).

        :return: the two extremes of the requested window.
        :rtype: tuple

        :raises BadRequest: if the parameters are missing or are not ISO dates.
        """
        start_raw, end_raw = request.query_params.get("start_date"), request.query_params.get("end_date")
        if not start_raw or not end_raw:
            raise BadRequest("Missing required query parameters: start_date and end_date.")
        try:
            start, end = datetime.date.fromisoformat(start_raw), datetime.date.fromisoformat(end_raw)
        except ValueError:
            raise BadRequest("Invalid date format: start_date and end_date must be ISO dates (YYYY-MM-DD).")

        # TypesettingAssignment.assigned is a DateTime: build an inclusive datetime range so
        # that both extremes are included. Dates are midnight UTC, safely inside the window
        # whatever the server's TIME_ZONE is (±12h).
        return (
            datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc),
            datetime.datetime.combine(end, datetime.time.max, tzinfo=datetime.timezone.utc),
        )

    def get(self, request, code: str, typesetter_pk: int):
        date_range = self._date_range(request)

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


def _filter_to_wjs_review_api(endpoints, **kwargs):
    """Keep only this plugin's own API entry points.

    Without this, ``SpectacularAPIView`` introspects the whole project's URL resolver
    (``settings.ROOT_URLCONF``) and the generated schema ends up documenting Janeway's core
    ``/api/`` endpoints too, which are a separate, unrelated API.
    """
    return [
        (path, path_regex, method, callback)
        for path, path_regex, method, callback in endpoints
        if path.startswith("/plugins/wjs-review-articles/api/v1/")
    ]


@extend_schema_view(get=extend_schema(exclude=True))
class SchemaView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularAPIView):
    """
    Serve the OpenAPI schema, gated like the rest of this API.

    As everywhere else here, a logged-in Janeway session authenticates as well as a token, so a
    human can view it directly in a browser -- see `EOOrTypesetterAccessMixin`.
    """

    custom_settings = {"PREPROCESSING_HOOKS": ["plugins.wjs_review.api.views._filter_to_wjs_review_api"]}


@extend_schema_view(get=extend_schema(exclude=True))
class SwaggerUIView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularSwaggerView):
    """
    Serve Swagger UI, gated like the rest of this API.

    As everywhere else here, a logged-in Janeway session authenticates as well as a token, so an
    EO member or typesetter can open this page -- and use its "Try it out" against the API entry
    points -- with no token at all; see `EOOrTypesetterAccessMixin`.
    """


@extend_schema_view(get=extend_schema(exclude=True))
class RedocUIView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularRedocView):
    """
    Serve Redoc, gated like the rest of this API.

    As everywhere else here, a logged-in Janeway session authenticates as well as a token, so a
    human can read it directly in a browser -- see `EOOrTypesetterAccessMixin`.
    """
