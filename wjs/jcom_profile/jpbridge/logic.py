"""Shared lookups for the Jp-SGP bridge views."""

from wjs.jcom_profile.models import Correspondence

from .constants import SGP_SOURCE


def get_sgp_correspondence(account_id):
    """Return the Correspondence(source="sgp") row for this WJS account id.

    Raises Correspondence.DoesNotExist / Correspondence.MultipleObjectsReturned;
    left to the caller, since the two callers respond to these differently.
    """
    return Correspondence.objects.get(account_id=account_id, source=SGP_SOURCE)
