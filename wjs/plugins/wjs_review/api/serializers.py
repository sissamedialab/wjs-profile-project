from plugins.wjs_submission.helpers.collaborations import TABELLONE_FIELDS
from plugins.wjs_submission.models import Collaboration
from rest_framework import serializers

from .const import COLLABORATIONS_EXPORT_KEYS, TYPE_TO_MIME

#: Keys of an exported collaboration, in the order they are written: the ones listed in the export
#: order first, then the keys of the import map that are not (yet) listed there.
COLLABORATION_EXPORT_FIELDS: tuple[str, ...] = tuple(
    key for key in COLLABORATIONS_EXPORT_KEYS if key in TABELLONE_FIELDS
) + tuple(key for key in TABELLONE_FIELDS if key not in COLLABORATIONS_EXPORT_KEYS)


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
