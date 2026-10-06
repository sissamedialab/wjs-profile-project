"""
Small, pure, stateless value formatters for the metadata export.

No Django model imports here (the ``dto.DateParts`` import is a plain dataclass, not a model) --
every function takes and returns plain Python values (``str``/``date``/``datetime``) so this module
is independently unit-testable with zero DB/Django involvement.
"""

import datetime
import re

import pycountry

from .dto import DateParts

#: Matches any run of whitespace that includes a line break (CR and/or LF), or repeated plain
#: spaces/tabs -- used by ``to_single_line`` to collapse both into a single space.
_LINE_BREAK_OR_REPEATED_WHITESPACE = re.compile(r"\s*[\r\n]\s*|[ \t]{2,}")

_MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def to_two_letter_language_code(three_letter_code: str) -> str:
    """
    Convert an ISO 639-2/T alpha-3 language code (e.g. "eng") to alpha-2 (e.g. "en").

    Settled (export_spec.md): ``Article.language`` is stored as alpha-3 (confirmed against
    Janeway's own ``LANGUAGE_CHOICES`` and WJS's ``SUBMISSION_ARTICLE_LANGUAGES`` override), while
    the export target's ``article/@lang`` expects alpha-2 -- both sample fixtures show
    ``lang="EN"``. This mirrors the same ``pycountry`` lookup ``wjs_tags.language_alpha2()``
    wraps (``jcom_profile/templatetags/wjs_tags.py``) and the inline use in
    ``synctex/forms.py``/``import_utils.py``.

    Unknown/unmapped codes (including ones with no alpha-2 form at all) are returned unchanged
    rather than raising, so a not-yet-mapped language degrades gracefully instead of breaking the
    export.
    """
    if not three_letter_code:
        return ""
    language = pycountry.languages.get(alpha_3=three_letter_code.upper())
    if language is None or not hasattr(language, "alpha_2"):
        return three_letter_code
    return language.alpha_2


def format_date_dd_mon_yyyy(value: datetime.date | datetime.datetime | None) -> str:
    """Format a date/datetime as ``DD-Mon-YYYY`` (e.g. "02-Jul-2026"); "" if ``value`` is falsy."""
    if not value:
        return ""
    return f"{value.day:02d}-{_MONTH_ABBREVIATIONS[value.month - 1]}-{value.year:04d}"


def to_single_line(text: str) -> str:
    """
    Collapse a (possibly multi-line/HTML-sourced) string to one line, no line breaks.

    Per export_spec.md, ``<abstract>`` must be rendered as one-line text -- ``Article.abstract``
    is a ``JanewayBleachField`` and can contain literal newlines (from a plain-text textarea) or
    block-level HTML (``<p>``/``<br>``) that a browser renders as line breaks. This only
    normalizes whitespace (CR/LF and runs of spaces/tabs collapse to a single space, then the
    result is stripped) -- it does not strip HTML tags, which is a separate, not-yet-requested
    concern.
    """
    if not text:
        return ""
    return _LINE_BREAK_OR_REPEATED_WHITESPACE.sub(" ", text).strip()


def build_date_parts(value: datetime.date | datetime.datetime | None) -> DateParts:
    """
    Build a ``DateParts`` (str year/month/day, no leading zeros) from a date/datetime.

    Returns an empty ``DateParts`` (falsy, all fields "") when ``value`` is falsy/None.
    """
    if not value:
        return DateParts()
    return DateParts(year=str(value.year), month=str(value.month), day=str(value.day))
