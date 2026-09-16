TYPE_TO_MIME = {
    "pdf": {"application/pdf"},
    "epub": {"application/epub+zip"},
    "html": {"text/html"},
    "xml": {"application/xml", "text/xml"},
    "doc": {"application/msword"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "odt": {"application/vnd.oasis.opendocument.text"},
    "tex": {"application/x-tex", "text/x-tex"},
    "rtf": {"application/rtf"},
    "other": {"application/octet-stream"},
}

#: Version written in the exported collaborations file, the same as "tabellone.json"'s own.
COLLABORATIONS_EXPORT_VERSION = 1.0

#: Keys of an exported collaboration, in the same order as in "tabellone.json", so that the two
#: files can be compared as they are.
COLLABORATIONS_EXPORT_KEYS = (
    "short_name",
    "full_name",
    "authorList",
    "collaborationList",
    "moretex",
    "email",
    "logo",
    "logoSize",
    "notes",
    "cluster",
    "sample_papers",
)

#: Values accepted by the "public_listing" query parameter of the collaborations entry point, mapped
#: onto the value to filter the collaborations by (None means "do not filter at all").
PUBLIC_LISTING_FILTERS = {
    "all": None,
    "true": True,
    "false": False,
}

#: Value assumed when the "public_listing" query parameter is not given.
PUBLIC_LISTING_DEFAULT = "all"
