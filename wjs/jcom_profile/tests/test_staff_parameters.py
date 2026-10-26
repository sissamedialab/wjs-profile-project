import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from plugins.wjs_review.models import Message

from wjs.jcom_profile.forms import UpdateAssignmentParametersForm
from wjs.jcom_profile.models import StaffWorkloadParameters


@pytest.mark.parametrize(
    "enabled",
    (True, False),
)
@pytest.mark.django_db
def test_staff_disable_notification(eo_user, editors, journal, enabled):
    status = "enabled" if enabled else "disabled"
    editor = editors[0]
    # Set the value to enable trigger changes on submit
    StaffWorkloadParameters.objects.filter(user=editor, journal=journal).update(enabled=not enabled)
    selection = {"workload": 100, "enabled": enabled}
    form = UpdateAssignmentParametersForm(
        instance=StaffWorkloadParameters.objects.get(user=editor, journal=journal), data=selection
    )
    assert form.is_valid()
    assert form.save()
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert Message.objects.count() == 1
    message = Message.objects.first()
    assert msg.subject == "[JCOM] Assignment Parameters Status Changed"
    assert f"Status: {status}" in msg.body
    assert message.message_type == Message.MessageTypes.SYSTEM
    assert message.subject == "Assignment Parameters Status Changed"
    assert message.verbosity == Message.MessageVerbosity.FULL


@pytest.mark.parametrize("brake_on", (-1, 0, 1, 10))
@pytest.mark.django_db
def test_brake_on_accepts_sentinel_and_non_negative_values(editors, journal, brake_on):
    """-1 (EO-disabled), 0 (no override), and any positive cap override are all valid."""
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    parameters.brake_on = brake_on
    parameters.save()
    parameters.refresh_from_db()
    assert parameters.brake_on == brake_on


@pytest.mark.parametrize("brake_on", (-2, -10))
@pytest.mark.django_db
def test_brake_on_rejects_values_below_sentinel(editors, journal, brake_on):
    """Anything less than -1 is not a valid brake_on value."""
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    parameters.brake_on = brake_on
    with pytest.raises(ValidationError):
        parameters.save()


@pytest.mark.django_db
def test_is_disabled_by_eo_reflects_brake_on_sentinel(editors, journal):
    editor = editors[0]
    parameters = StaffWorkloadParameters.objects.get(user=editor, journal=journal)

    assert editor.jcomprofile.is_disabled_by_eo(journal) is False

    parameters.brake_on = -1
    parameters.save()
    assert editor.jcomprofile.is_disabled_by_eo(journal) is True

    parameters.brake_on = 0
    parameters.save()
    assert editor.jcomprofile.is_disabled_by_eo(journal) is False
