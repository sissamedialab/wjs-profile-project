from .models import PermissionAssignment


def visibility_flags(request):
    """
    This context processor injects the visibility flags enum.

    :param request: the active request
    :return: dictionary containing DATE_FORMAT / DATETIME_FORMAT
    """
    return {
        "PermissionType": PermissionAssignment.PermissionType,
        "BinaryPermissionType": PermissionAssignment.BinaryPermissionType,
    }
