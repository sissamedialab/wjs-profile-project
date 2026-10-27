"""Signals.

Every time a user model instance is created, a corresponding JCOM
profile instance must be created as well.

Every time a staff role (section editor, reviewer, director) is granted, the user gets default
StaffWorkloadParameters for that journal, so that they are not shown as disabled (specs#3093).
Joining the EO group does the same for every journal.

"""

from core.models import Account, AccountRole
from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver
from journal.models import Issue, Journal

from wjs.jcom_profile import constants
from wjs.jcom_profile.models import (
    IssueParameters,
    JCOMProfile,
    StaffWorkloadParameters,
)

STAFF_PARAMETERS_ROLES = (constants.SECTION_EDITOR_ROLE, constants.REVIEWER_ROLE, constants.DIRECTOR_ROLE)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_handler(sender, instance, created, **kwargs):
    """Create the JCOM profile, only if the Account is newly created."""
    if not created:
        return

    # Using save_base skips the save() method of the JCOMProfile model and correctly creates the instance of our
    # subclass without resetting the user data.
    # It ensures no django magic is applied because we are basically creating a duplicate of the original data.
    # https://stackoverflow.com/questions/9821935/django-model-inheritance-create-a-subclass-using-existing-super-class
    return JCOMProfile(janeway_account=instance).save_base(raw=True)


@receiver(post_save, sender=Issue)
def create_special_issue_parameters(sender, instance, created, **kwargs):
    """Create IssueParameters instance for each Issue."""
    IssueParameters.objects.get_or_create(issue=instance)


@receiver(post_save, sender=AccountRole)
def create_staff_parameters_on_role(sender, instance: AccountRole, created: bool, raw: bool = False, **kwargs):
    """
    Create default StaffWorkloadParameters when a staff role is granted.

    Only newly created roles in :py:data:`STAFF_PARAMETERS_ROLES` are considered; existing parameters are never
    modified, and users who already hold a role are not backfilled.
    """
    if not created or raw or instance.role.slug not in STAFF_PARAMETERS_ROLES:
        return
    StaffWorkloadParameters.objects.get_or_create(user=instance.user, journal=instance.journal)


@receiver(m2m_changed, sender=Account.groups.through)
def create_staff_parameters_on_eo_membership(sender, instance, action: str, reverse: bool, pk_set, **kwargs):
    """
    Create default StaffWorkloadParameters for every journal when a user joins the EO group.

    Both sides of the relation are handled: ``user.groups.add(eo)`` (``instance`` is the account, ``pk_set`` holds
    group ids) and ``eo.user_set.add(user)`` (``instance`` is the group, ``pk_set`` holds account ids).

    Existing parameters are never modified. Journals created after the user joined EO get no parameters, as no
    signal fires for them.
    """
    if action != "post_add" or not pk_set:
        return
    if reverse:
        if instance.name != constants.EO_GROUP:
            return
        user_ids = pk_set
    else:
        if not Group.objects.filter(pk__in=pk_set, name=constants.EO_GROUP).exists():
            return
        user_ids = {instance.pk}
    for journal in Journal.objects.all():
        for user_id in user_ids:
            StaffWorkloadParameters.objects.get_or_create(user_id=user_id, journal=journal)
