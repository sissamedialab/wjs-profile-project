from .models import PermissionAssignment


def visibility_flags(request):
    """Inject the visibility flags enum.

    :param request: the active request
    :return: dictionary containing permission and binary-permission choices
    """
    return {
        "PermissionType": PermissionAssignment.PermissionType,
        "BinaryPermissionType": PermissionAssignment.BinaryPermissionType,
    }
