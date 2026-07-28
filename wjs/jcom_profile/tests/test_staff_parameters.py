import pytest
from django.core import mail
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
    assert f"Status: {status}" in mail.outbox[0].body
    assert message.message_type == Message.MessageTypes.SYSTEM
    assert message.subject == "Assignment Parameters Status Changed"
    assert message.verbosity == Message.MessageVerbosity.FULL
