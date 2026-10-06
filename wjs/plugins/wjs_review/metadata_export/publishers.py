"""Per-journal functions that deliver an article's production export zip to its publisher.

Each function's signature is ``(article: submission.models.Article, zip_bytes: bytes) -> None``.
A function is referenced by dotted path from
``settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS`` and resolved via
``django.utils.module_loading.import_string`` -- the same per-journal dynamic-import pattern used
by ``WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS`` and its siblings in ``wjs/defaults/settings.py``.

Each function here is a thin wrapper over ``sftp._send_via_sftp`` -- the actual transport is
flow-agnostic; only the (journal_code, flow) pair varies per function. May raise
``sftp.SFTPSendError``; the caller (``SendProductionXMLToPublisher.run()``) decides what to do
about it.
"""

from submission.models import Article

from . import sftp


def send_zip_to_iop(article: Article, zip_bytes: bytes) -> None:
    """Send an article's production export zip (metadata XML + linked files) to IOP.

    The "accepted-articles" flow -- the only one with a caller today (wjs/specs#2912). The
    payload carries author PII (names, affiliations, funding); it is never logged, here or in
    ``sftp._send_via_sftp``.
    """
    sftp._send_via_sftp(article, zip_bytes, journal_code="JCAP", flow="accepted-articles")
