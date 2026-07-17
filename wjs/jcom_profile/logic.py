from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from utils.logger import get_logger

from wjs.jcom_profile.models import StaffWorkloadParameters
from wjs.jcom_profile.utils import get_eo_user

logger = get_logger(__name__)


def send_staff_assignment_change(staff_parameter: StaffWorkloadParameters):
    status = _("enabled") if staff_parameter.enabled else _("disabled")
    context = {
        "user": staff_parameter.user.full_name(),
        "status": status,
        "journal": staff_parameter.journal.name,
        "workload": staff_parameter.workload,
    }
    subject = render_to_string("jcom_profile/email/send_staff_assignment_change_subject.txt", context).strip()
    body = render_to_string("jcom_profile/email/send_staff_assignment_change_body.txt", context)

    # Send to the user
    recipient_list = [get_eo_user(staff_parameter.journal)]

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=list(set(recipient_list)),  # Remove duplicates
            fail_silently=True,
        )
        logger.info(f"Email sent for enabled field change: {staff_parameter.user.email} - {status}")
    except Exception as e:
        logger.error(f"Failed to send email for enabled field change: {e}")
