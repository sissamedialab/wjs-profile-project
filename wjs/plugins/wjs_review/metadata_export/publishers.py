"""Per-journal functions that deliver an article's production export zip to its publisher.

Each function's signature is ``(article: submission.models.Article, zip_bytes: bytes) -> None``.
A function is referenced by dotted path from
``settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS`` and resolved via
``django.utils.module_loading.import_string`` -- the same per-journal dynamic-import pattern used
by ``WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS`` and its siblings in ``wjs/defaults/settings.py``.
"""

from submission.models import Article


def send_zip_to_iop(article: Article, zip_bytes: bytes) -> None:
    """Send an article's production export zip (metadata XML + linked files) to IOP.

    Stub: no transport implemented yet. When real transport is added, the endpoint URL and any
    credentials must live in Django settings (never a per-article/per-journal DB field an admin
    could redirect), the connection must use TLS, and the payload -- which carries author PII
    such as names, affiliations, and funding -- must never be logged.
    """
