"""Countless pagination utilities.

Use when running `COUNT(*)` on a filtered queryset is too expensive
(e.g. the activity page backed by the messages table).
"""

from django.core.paginator import Page, Paginator


class CountlessPaginator(Paginator):
    """
    Paginator that never runs COUNT(*) on the object list.

    Instead of knowing the total number of pages, it detects whether a next
    page exists by fetching one extra record (the "per_page + 1" trick).
    Page objects expose the standard interface needed by templates:
    `has_previous`, `has_next`, `number`, `previous_page_number`,
    `next_page_number`.

    Templates should test `page_obj.paginator.is_countless` instead of
    `page_obj.paginator` when they must distinguish this paginator from a
    standard one.

    .. warning::

        Navigation is limited to previous/next page: the total number of
        records and of pages is unknown, so direct links to arbitrary pages
        (and links beyond the first "hole" past the end of the data) are not
        available.
    """

    is_countless = True

    @property
    def count(self):
        """Total count is unknown: return a best-effort lower bound."""
        return (self._last_page_number - 1) * self.per_page + len(self._last_page_records)

    @property
    def num_pages(self):
        """Total number of pages is unknown; alias of count."""
        return self.count

    @property
    def page_range(self):
        # Without a total, the range of existing pages cannot be computed.
        return range(0)

    def validate_number(self, number):
        """Validate the given 1-based page number (page 0 is treated as 1)."""
        try:
            if isinstance(number, float) and not number.is_integer():
                raise ValueError
            number = int(number)
        except (TypeError, ValueError):
            raise self.invalid_page_exception("Page number is not an integer")
        if number < 1:
            raise self.invalid_page_exception("Page number is less than 1")
        return number

    def _get_page(self, *args, **kwargs):
        return self._last_page

    def page(self, number):
        """
        Return a page fetching per_page + 1 records to detect the next page.

        If more than per_page records are returned, a next page exists and
        the extra record is discarded.
        """
        number = self.validate_number(number)
        bottom = (number - 1) * self.per_page
        # Fetch one extra record (+1) to detect if there is a next page.
        top = bottom + self.per_page + self.orphans + 1
        records = list(self.object_list[bottom:top])
        has_next = len(records) > self.per_page
        page_of_records = records[: self.per_page]
        page = Page(page_of_records, number, self)
        page.has_next = has_next
        # Stash the page for the other "best-effort" accessors.
        self._last_page = page
        self._last_page_number = number
        self._last_page_records = page.object_list
        return page
