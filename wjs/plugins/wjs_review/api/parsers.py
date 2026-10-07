"""Request parsers for the wjs_review API."""

from rest_framework.parsers import FileUploadParser


class RawFileUploadParser(FileUploadParser):
    """Treat a raw (i.e. not multipart-encoded) request body as an uploaded file.

    DRF's stock parsers would refuse a body sent as "application/zip", and reading
    ``request.body`` by hand (as the galley upload does) is capped by Django's
    ``DATA_UPLOAD_MAX_MEMORY_SIZE``. ``FileUploadParser`` instead streams the body through
    Django's upload handlers, so a large archive ends up in a temporary file on disk.

    The media type is left wide open on purpose: the content type is checked by the
    serializer, so that a wrong one is reported in this API's own error format rather than
    in DRF's default "415" body.
    """

    media_type = "*/*"

    def get_filename(self, stream, media_type, parser_context) -> str:
        """Return the name to give to the uploaded file.

        ``FileUploadParser`` refuses to parse a body that it cannot name, but our clients
        send a bare archive with no "Content-Disposition" header. The name is only a
        placeholder anyway: the logic class renames the file before storing it.

        :return: the client-provided file name, or a placeholder when there is none.
        :rtype: str
        """
        return super().get_filename(stream, media_type, parser_context) or "upload"
