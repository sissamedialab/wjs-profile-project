"""Include utility functions to be used across the project."""
import base64
import hashlib
import os
import shutil
from uuid import uuid4

from core import files as core_files
from django.conf import settings


def generate_token(email: str):
    """
    Encode the given email into a token suitable for use in URLs.

    :param email: The user email
    :return: The token as a string
    """
    return base64.b64encode(hashlib.sha256(email.encode("utf-8")).digest()).hex()


PATH_PARTS = [
    "special_issues",
]


# Adapted from core.files.save_file_to_article
def save_file_to_special_issue(
    file_to_handle,
    special_issue,
    owner,
    label=None,
    description=None,
    replace=None,
    is_galley=False,
    save=True,
):
    """Save a file into a special issues's folder with appropriate mime type and permissions.

    :param file_to_handle: the uploaded file object we need to handle
    :param special_issue: the special_issue to which the file belongs
    :param owner: the owner of the file
    :param label: the file's label (or title)
    :param description: the description of the item
    :param replace: the file to which this is a revision or None
    :return: a File object that has been saved in the database
    """
    if isinstance(file_to_handle, str):
        original_filename = os.path.basename(file_to_handle)
    else:
        original_filename = str(file_to_handle.name)

    # N.B. os.path.splitext[1] always returns the final file extension, even in a multi-dotted (.txt.html etc.) input
    filename = str(uuid4()) + str(os.path.splitext(original_filename)[1])
    folder_structure = os.path.join(settings.BASE_DIR, "files", *PATH_PARTS, str(special_issue.id))

    if not os.path.exists(folder_structure):
        core_files.mkdirs(folder_structure)

    if save:
        core_files.save_file_to_disk(file_to_handle, filename, folder_structure)
        file_mime = core_files.file_path_mime(os.path.join(folder_structure, filename))
    else:
        shutil.move(
            os.path.join(folder_structure, original_filename),
            os.path.join(folder_structure, filename),
        )
        file_mime = core_files.guess_mime(filename)

    from core import models

    new_file = models.File(
        mime_type=file_mime,
        original_filename=original_filename,
        uuid_filename=filename,
        label=label,
        description=description,
        owner=owner,
        is_galley=is_galley,
        article_id=None,
    )

    new_file.save()

    return new_file
