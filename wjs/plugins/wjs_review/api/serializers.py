import zipfile

from core.models import Galley
from django.db.models import Min
from plugins.wjs_submission.helpers.collaborations import TABELLONE_FIELDS
from plugins.wjs_submission.models import Collaboration
from rest_framework import serializers
from typesetting.models import TypesettingAssignment

from .const import COLLABORATIONS_EXPORT_KEYS, SOURCE_ZIP_MEDIA_TYPES, TYPE_TO_MIME
from .exceptions import BadRequest, UnsupportedMediaType

#: Keys of an exported collaboration, in the order they are written: the ones listed in the export
#: order first, then the keys of the import map that are not (yet) listed there.
COLLABORATION_EXPORT_FIELDS: tuple[str, ...] = tuple(
    key for key in COLLABORATIONS_EXPORT_KEYS if key in TABELLONE_FIELDS
) + tuple(key for key in TABELLONE_FIELDS if key not in COLLABORATIONS_EXPORT_KEYS)


class ErrorDetailSerializer(serializers.Serializer):
    """The `"error"` object of this API's error envelope."""

    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField(required=False)


class ErrorSerializer(serializers.Serializer):
    """This API's error envelope: `{"error": {"code", "message", "details"?}}`."""

    error = ErrorDetailSerializer()


class GalleyItemSerializer(serializers.Serializer):
    """One entry of `ArticleGalleyListSerializer.items`."""

    type = serializers.CharField()  # noqa: A003 (matches the existing JSON contract's key)
    sequence = serializers.IntegerField()
    filename = serializers.CharField()
    contentType = serializers.CharField()  # noqa: N815 (matches the existing JSON contract's key)
    download_url = serializers.CharField()


class ArticleGalleyListSerializer(serializers.Serializer):
    """Response of `ArticleGalleyListView`: an article's galleys, by type and sequence."""

    article_id = serializers.IntegerField()
    items = GalleyItemSerializer(many=True)


class CollaborationSerializer(serializers.ModelSerializer):
    """
    Serialize a collaboration as a record of a "tabellone.json"-like file.

    The keys are the ones wjs_submission's import command reads (`TABELLONE_FIELDS`), so that an
    exported file can be fed back to it; they are ordered as in "tabellone.json" to keep the two
    files comparable. `public_listing` is not exported: it has no counterpart in "tabellone.json",
    it is only what the export can be filtered by.
    """

    class Meta:
        model = Collaboration
        fields = COLLABORATION_EXPORT_FIELDS
        # the model field each key is read from; a key named as the field it reads needs no source
        extra_kwargs = {
            key: {"read_only": True, **({} if TABELLONE_FIELDS[key] == key else {"source": TABELLONE_FIELDS[key]})}
            for key in COLLABORATION_EXPORT_FIELDS
        }


class CollaborationsExportSerializer(serializers.Serializer):
    """Response of `CollaborationListView`: the versioned "tabellone.json"-like envelope."""

    version = serializers.FloatField()  # COLLABORATIONS_EXPORT_VERSION is a float (1.0)
    collaborations = CollaborationSerializer(many=True)


class GalleyUploadSerializer(serializers.Serializer):
    content = serializers.SerializerMethodField()
    raw_body = serializers.CharField(write_only=True, required=False)

    def __init__(self, *args, **kwargs):
        self.galley_type = kwargs.pop("galley_type")
        super().__init__(*args, **kwargs)

    def validate(self, attrs):
        request = self.context["request"]

        content_type = request.content_type or ""
        allowed = TYPE_TO_MIME.get(self.galley_type)

        if not allowed:
            raise serializers.ValidationError({"code": "TYPE_NOT_FOUND", "message": "Invalid parameters."})

        if content_type not in allowed:
            raise serializers.ValidationError(
                {
                    "code": "UNSUPPORTED_MEDIA_TYPE",
                    "message": "Content-Type does not match expected type for this galley.",
                    "details": {
                        "expected": sorted(allowed),
                        "got": content_type,
                    },
                }
            )

        data = request.body
        if not data:
            raise serializers.ValidationError(
                {
                    "code": "BAD_REQUEST",
                    "message": "Missing request body.",
                }
            )

        attrs["data"] = data
        return attrs


class ProductionBaseSerializer(serializers.Serializer):
    """
    Shared field set of the production-monitoring responses (G7/G8 - Specifications.md §3.6/§3.7).

    Instance is an ArticleWorkflow.
    """

    preprint_id = serializers.ReadOnlyField()
    published_id = serializers.SerializerMethodField()
    doi = serializers.SerializerMethodField()
    # Article.date_accepted is a DateTimeField: do not coerce it to a date,
    # or DRF would refuse (timezone-naive coercion) - render the raw ISO datetime.
    date_accepted = serializers.DateTimeField(source="article.date_accepted", allow_null=True)
    status = serializers.SerializerMethodField()
    last_status_change = serializers.DateTimeField(source="modified")

    def get_published_id(self, obj):
        """Pubid identifier (e.g. JCAP07(2010)027); empty string until publication."""
        return obj.article.get_identifier("pubid") or ""

    def get_doi(self, obj):
        """DOI, set at acceptance; present even before the paper is published."""
        return obj.article.get_doi()

    def get_status(self, obj):
        # name = the computed state label (state_value → ReviewComputedStates where applicable).
        # Today a typesetter-side paper in the working-on states renders its raw state label
        # ("Typesetter selected"); the TiC vs "Back to typesetter" distinction lands
        # automatically here once specs#3120 adds that computed state.
        return {"code": obj.state, "name": obj.state_label}

    def get_date_taken_in_charge(self, obj):
        """First time this article was assigned to the URL typesetter (may predate the window).

        Requires "typesetter_pk" in the serializer context (provided by the G8 view); null-safe:
        any typesetter-less rendering yields None rather than crashing.
        """
        typesetter_pk = self.context.get("typesetter_pk")
        if not typesetter_pk:
            return None
        return TypesettingAssignment.objects.filter(
            round__article=obj.article,
            typesetter__pk=typesetter_pk,
        ).aggregate(min_assigned=Min("assigned"))["min_assigned"]


class TypesetterPapersListSerializer(ProductionBaseSerializer):
    """Response item of G8 - GET /journal/<code>/typesetter/<typesetter_pk>/papers/ (Specifications.md §3.7)."""

    date_taken_in_charge = serializers.SerializerMethodField()


class ProductionArticleSerializer(ProductionBaseSerializer):
    """Response item of G7 - GET /journal/<code>/production/ (Specifications.md §3.6).

    Extends the shared ProductionBaseSerializer (see its docstring) for preprint_id/published_id/
    doi/date_accepted/status/last_status_change; adds the two fields specific to this endpoint.
    """

    special_issue = serializers.IntegerField(source="article.primary_issue_id", allow_null=True)
    typesetter = serializers.SerializerMethodField()

    def get_typesetter(self, obj):
        """Full name of the typesetter on the latest typesetting assignment, if any."""
        assignment = obj.get_latest_typesetting_assignment()
        return assignment.typesetter.full_name() if assignment else None


class SourceZipUploadSerializer(serializers.Serializer):
    """Validate the raw zip archive uploaded to replace an article's publication-galleys sources.

    The archive is uploaded as the bare request body (see
    :class:`~.parsers.RawFileUploadParser`), so the content type is read from the request
    rather than from a multipart part.
    """

    #: Neither required nor non-empty here: a missing or empty body is reported by `validate()`,
    #: in this API's own error shape.
    file = serializers.FileField(write_only=True, required=False, allow_empty_file=True)

    def validate(self, attrs: dict) -> dict:
        """
        Check that the request carries a non-empty, well-formed zip archive.

        :param attrs: the deserialized fields.
        :type attrs: dict

        :return: the validated fields.
        :rtype: dict

        :raises UnsupportedMediaType: if the content type is not a zip one.
        :raises BadRequest: if the body is empty or is not a readable zip archive.
        """
        request = self.context["request"]

        # e.g. "application/zip; charset=binary" → "application/zip"
        content_type = (request.content_type or "").split(";")[0].strip().lower()
        if content_type not in SOURCE_ZIP_MEDIA_TYPES:
            raise UnsupportedMediaType(
                "Content-Type does not match expected type for the sources archive.",
                details={
                    "expected": list(SOURCE_ZIP_MEDIA_TYPES),
                    "got": content_type,
                },
            )

        uploaded_file = attrs.get("file")
        if uploaded_file is None or not uploaded_file.size:
            raise BadRequest("Missing request body.")

        if not zipfile.is_zipfile(uploaded_file):
            raise BadRequest("Request body is not a readable zip archive.")
        # `is_zipfile()` reads through the file: rewind it for whoever stores it.
        uploaded_file.seek(0)

        return attrs


class GalleySerializer(serializers.ModelSerializer):
    """Serialize a galley as it is reported back to the client that triggered its (re)generation."""

    filename = serializers.CharField(source="file.original_filename", read_only=True)

    class Meta:
        model = Galley
        fields = ("type", "label", "sequence", "filename")
        read_only_fields = fields


class RegeneratedGalleysSerializer(serializers.Serializer):
    """Response of `ArticleZipView.put()`: the galleys rebuilt from the uploaded sources."""

    article_id = serializers.IntegerField()
    galleys = GalleySerializer(many=True)
