from rest_framework import serializers

from .const import TYPE_TO_MIME


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
