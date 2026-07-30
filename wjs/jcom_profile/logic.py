from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from plugins.wjs_review import communication_utils
from utils.logger import get_logger

from wjs.jcom_profile.models import StaffWorkloadParameters
from wjs.jcom_profile.permissions import get_hijacker
from wjs.jcom_profile.utils import get_eo_user

logger = get_logger(__name__)


def send_staff_assignment_change(staff_parameter: StaffWorkloadParameters):
    """
    Emit a Message  when a staff assignment status changes.

    Emit a Message via communication_utils.log_operation about changes to assignment status
    with details such as the user's full name, the status of the assignment,
    the associated journal name, and the workload.

    :param staff_parameter: Contains details about the staff workload and associated
        journal. Must include attributes like user, journal, workload, and enabled.
    :type staff_parameter: StaffWorkloadParameters
    :return: None
    """
    status = _("enabled") if staff_parameter.enabled else _("disabled")
    context = {
        "user": staff_parameter.user.full_name(),
        "status": status,
        "journal": staff_parameter.journal.name,
        "workload": staff_parameter.workload,
    }

    message_subject = render_to_string("jcom_profile/email/send_staff_assignment_change_subject.txt", context).strip()
    message_body = render_to_string("jcom_profile/email/send_staff_assignment_change_body.txt", context)

    try:
        communication_utils.log_operation(
            journal=staff_parameter.journal,
            message_subject=message_subject,
            message_body=message_body,
            recipients=[get_eo_user(staff_parameter.journal)],
            hijacking_actor=get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=False,
        )
        logger.info(f"Email sent for enabled field change: {staff_parameter.user.email} - {status}")
    except Exception as e:
        logger.error(f"Failed to send email for enabled field change: {e}")
