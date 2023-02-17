"""Include utility functions to be used across the project."""
import base64
import hashlib
import os
import re
import shutil
from uuid import uuid4

from core import files as core_files
from django.conf import settings
from utils.logger import get_logger

logger = get_logger(__name__)


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


def from_pubid_to_eid(pubid):
    """Extract the electronic ID from the publication ID.

    Used in the how-to-cite.

    Adapted from token_jcom/token_jcom.module:token_jcom_contribution_number
    """
    eid = ""
    # Abbiamo tre possibili formati, a seconda dell'età del paper:
    if pubid.find("_") > -1:
        # JCOM_1401_2015_C02 o JCOM_1401_2015_E => dividi sugli "_" e prendi l'ultimo segmento:
        eid = pubid.split("_")[-1]

    elif pubid.find(")") > -1:
        # Jcom1102(2012)A01 o Jcom1102(2012)E => la parte dopo la parentesi:
        # was: pubid[pubid.find(")") + 1:] (but flake8 E203 and black didn't agree on the space before ":")
        eid = pubid.split(")")[-1]

    elif len(pubid) > 4:
        # R020401 (o E0204) in formato tvviicc => 1° e 5-6°:
        eid = pubid[0:1] + pubid[5:]

    else:
        logger.error("Cannot extract EID from %s", pubid)
    return eid


def citation_name_apa(author):
    """Format an author's name in way suitable to be used in APA-like citations.

    :param author: can be an Account or a FrozenAuthor.
    """
    return ""


def abbreviate_first_middle(author, sep=" "):
    """Abbreviate an author's first- and middle-name.

    :param author: can be an Account or a FrozenAuthor.
    :param sep: separator between "parts". E.g.
    - sep=" " ⇨ A. B.-C.
    - sep=""  ⇨ A.B.-C.

    Adapted from PoS's
    [compress_names](https://gitlab.sissamedialab.it/gamboz/pos/-/blob/master/lib/io_lib.pm#L3181)
    but see also
    https://gitlab.sissamedialab.it/gamboz/pos/-/issues/29

    """
    given_names = " ".join((author.first_name or "", author.middle_name or "")).strip()
    # Remove existing "." (usually in middlename)
    given_names, _ = re.subn(r"[. ]+", " ", given_names)
    # Split on space or "-" (for composite names)
    pieces = re.split(r"([ -])", given_names)
    # Keep only the initial letter and the "-"
    initials = [p[0] for p in pieces if p and p != " "]

    abbreviation = ""
    for i in range(len(initials) - 1):
        initial = initials[i]
        next_initial = initials[i + 1]

        abbreviation += initial
        if initial != "-":
            abbreviation += "."
            if next_initial != "-":
                abbreviation += sep
    # Assume that the last initial is a letter, not "-"
    abbreviation += f"{initials[-1]}."
    return abbreviation


def citation_name(author, sep=" "):
    """Generate the "citation name" on an author.

    E.g. Mario Rossi ⇨ Rossi, M.

    :param author: can be an Account or a FrozenAuthor.
    :param sep: passed to abbreviate_first_middle()
    """
    # Author don't have `is_corporate` attribute, only FrozenAuthors do!
    if hasattr(author, "is_corporate") and author.is_corporate:
        return author.corporate_name

    abbreviated_given_names = abbreviate_first_middle(author, sep)
    return f"{author.last_name}, {abbreviated_given_names}"
