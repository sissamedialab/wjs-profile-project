"""Errors reported by the wjs_review API.

Raising one of these - instead of returning a ``Response`` - lets a serializer report an error
without knowing which status code the view will answer with, and keeps every error in the
``{"error": {"code": ..., "message": ...}}`` shape that this API's views already use.
"""

from rest_framework import status
from rest_framework.exceptions import APIException


class ApiError(APIException):
    """Base class for the errors this API reports."""

    #: The value served under the "code" key; each subclass pairs it with its own status code.
    error_code = "ERROR"

    def __init__(self, message: str, details: dict = None):
        """
        Build the error payload.

        :param message: what went wrong, for the client to display or log.
        :type message: str
        :param details: anything that helps the client fix the request (e.g. what was expected).
        :type details: dict
        """
        error = {"code": self.error_code, "message": message}
        if details is not None:
            error["details"] = details
        super().__init__({"error": error})


class ResourceNotFound(ApiError):
    """The requested resource does not exist (or the requester may not know that it does)."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "NOT_FOUND"


class BadRequest(ApiError):
    """The request is malformed."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "BAD_REQUEST"


class InvalidGalleyType(ApiError):
    """The requested galley type is not one this API knows about."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "TYPE_NOT_FOUND"


class DataIntegrityConflict(ApiError):
    """The stored data cannot answer the request unambiguously."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "DATA_INTEGRITY_CONFLICT"


class UnsupportedMediaType(ApiError):
    """The request body is not in one of the content types that the entry point accepts."""

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    error_code = "UNSUPPORTED_MEDIA_TYPE"


class GalleyGenerationFailed(ApiError):
    """Jcomassistant could not turn the article's sources into galleys."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "GALLEY_GENERATION_FAILED"
