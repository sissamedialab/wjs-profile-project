"""Import article from wjapp."""

import datetime
import os
import tarfile
import textwrap
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

import freezegun
import mariadb
import requests
from core import files
from core.middleware import GlobalRequestMiddleware
from core.models import Account
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files import File as DjangoFile
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape
from journal.models import Journal
from plugins.wjs_review import communication_utils
from plugins.wjs_review.logic import (
    AssignToEditor,
    AssignToReviewer,
    AuthorHandleRevisionObsolete,
    DeselectReviewer,
    EditorRevisionRequest,
    EvaluateReview,
    HandleDecision,
    HandleEditorDeclinesAssignment,
    OpenAppeal,
    PermissionAssignment,
    PressNotificationHandler,
    Reminder,
    SubmitReview,
    SupervisorChangeEditorAssignment,
    WithdrawPreprint,
    WorkflowReviewAssignment,
    render_template_from_setting,
)
from plugins.wjs_review.logic__production import (
    AssignTypesetter,
    AuthorSendsCorrections,
    HandleEOSendBackToTypesetter,
    ReadyForPublication,
    RequestProofs,
    TypesettedFilesUpload,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    Message,
    PastEditorAssignment,
    WjsEditorAssignment,
)
from plugins.wjs_review.permissions import is_article_typesetter
from plugins.wjs_review.utils import get_report_form
from review.models import ReviewRound
from submission import models as submission_models
from submission.models import FrozenAuthor
from typesetting.models import GalleyProofing, TypesettingAssignment, TypesettingRound
from utils.logger import get_logger
from utils.setting_handler import get_setting

import wjs.jcom_profile.import_wjapp.import_file_manager as import_file_manager
from wjs.jcom_profile import constants
from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_utils import check_mappings, rome_timezone
from wjs.jcom_profile.import_wjapp.import_exception_list import (
    request_proofs_dedup_galley_proofing_applies,
)
from wjs.jcom_profile.import_wjapp.import_production_hold import (
    release_production_readiness_hold,
)
from wjs.jcom_profile.permissions import get_hijacker, has_eo_role
from wjs.jcom_profile.utils import create_rich_fake_request, get_eo_user

logger = get_logger(__name__)


#
# Begin global variables and functions section
#
# In this section are defined global functions and global variables used by the global funcions

# global variables

# global variable which contains the association wjapp usercod, and wjs account object
# already imported: {...("usercod1": "account1" ), ...}
already_imported_users = {}


#
# SI maps of wjapp (key) and wjs (value) for each journal
#


# JCOMAL
jcomal_map_si = {
    2: 115,  # Medioambiente -- Medioambiente y divulgación
}


# JCOM
jcom_map_si = {
    105: 55,  # Special Issue on citizen science -- Special Issue: Citizen Science, Part I, 2016
    # 105: 57, # Special Issue on citizen science -- Special Issue: Citizen Science, Part II, 2016
    106: 63,  # History of Science Communication -- Special Issue: History of Science Communication, 2017
    107: 70,  # Special Issue on User Experience of Digital... -- Special Issue: User Experience of Digital...
    108: 72,  # Special Issue on Communication at the Intersect.. -- Special Issue: Communication at the Intersect...
    109: 74,  # Special Issue on Stories in Science Communication -- Special Issue: Stories in Science Communication..
    110: 88,  # Third International ECSA Conference -- Special Issue: Third International ECSA Conference, Trieste..
    111: 80,  # COVID-19 and science communication -- Special Issue: COVID-19 and science communication, Part I, 2020
    # 111: 82, # COVID-19 and science communication -- Special Issue: COVID-19 and science communication, Part II, 2020
    112: 85,  # Re-examining Science Communication -- Special Issue: Re-examining Science Communication: models, ...
    113: 91,  # Participatory Science Communication for... -- Special Issue Participatory science communication for...
    114: 93,  # Responsible Science Communication acro... -- Special Issue: Responsible science communication acro...
    115: 109,  # Living Labs Under Construction: Paradigms, ... -- Special Issue: Living labs under construction: ...
    116: 113,  # Science Communication in Higher Educati.. -- Special Issue: Science communication in higher educati..
    117: 117,  # Connecting Science Communication... -- Special Issue: Connecting science communication...
    118: 120,  # Science communication for social justice -- Special Issue: Science communication for social justice
    119: 126,  # Public (dis)trust in science... -- Special Issue: Public (dis)trust in science...
    120: 124,  # Communicating Discovery Science -- Special Issue: Communicating Discovery Science
    121: 129,  # Science Communication in the Age of Artificial... -- Science Communication in the Age of Artificial...
    122: 130,  # Emotions and Science Communication -- Emotions and Science Communication
    123: 131,  # Science in unexpected places -- Science in unexpected places
}

#
# global functions
#


def read_usernotes(target_user_cod, connection):
    """Reads the usernotes from wjapp."""

    cursor_usernotes = connection.cursor(dictionary=True)
    query_usernotes = """
SELECT
un.userNoteCod,
un.userNoteID,
un.userNoteContent,
un.submissionDate,
un.authorCod,
u1.firstname AS author_firstname,
u1.lastname AS author_lastname,
un.targetUserCod,
u2.firstname AS target_firstname,
u2.lastname AS target_lastname
FROM
User_Note un
LEFT JOIN User u1 ON (un.authorCod=u1.userCod)
LEFT JOIN User u2 ON (un.targetUserCod=u2.userCod)
WHERE
targetUserCod = %(user_cod)s
ORDER BY submissionDate DESC
"""
    cursor_usernotes.execute(
        query_usernotes,
        {
            "user_cod": target_user_cod,
        },
    )
    usernotes_rows = cursor_usernotes.fetchall()
    cursor_usernotes.close()
    return usernotes_rows


def get_usernotes(account, usernotes_rows):
    """Concatenate the usernotes in one string."""

    if not usernotes_rows:
        return None

    usernotes = ""
    for note in usernotes_rows:
        usernotes += f"""
{note.get("userNoteContent").strip()}

{note.get("submissionDate")} by {note.get("author_firstname")} {note.get("author_lastname")}


"""
    return usernotes


def account_get_or_create_check_correspondence(
    source, user_cod, last_name, first_name, imported_email, privacy, connection
):
    """Get a user account - check Correspondence and eventually create new account."""

    # ex: source: jcom, jcomal, prophy, ...
    # Check if we know this person form some other journal or by email

    # uses the global variable as "cache"
    # if the wjapp user_cod is in this global variable, it is already
    # been checked and found by this funcion for this article (the journal is fixed for the
    # current imported article). The execution of this management command only imports one article.
    if user_cod in already_imported_users.keys():
        logger.debug(f"found cached user: {already_imported_users[user_cod]}")
        return already_imported_users[user_cod]

    # JCAP uses for the hidden users this email: jcap_hidden_user@medialab.sissa.it
    if imported_email == f"{source}_hidden_user@medialab.sissa.it":
        # using the wjapp userCod in the email the same hidden user is identified
        # in Correspondence in unique way after the hiding action on wjapp.
        # Searching in Correspondence with source and user_cod only can find
        # more than one match (email changes)
        imported_email = f"{user_cod}_{source}_hidden_user@invalid.com"
        logger.debug(f"found wjapp hidden user {user_cod=} {imported_email}")

    account_created = False
    mappings = wjs_models.Correspondence.objects.filter(
        Q(user_cod=user_cod, source=source) | Q(email__iexact=imported_email),
    )
    if mappings.count() == 0:
        # We never saw this person in other journals.
        account, account_created = Account.objects.get_or_create(
            email=imported_email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
            },
        )
        mapping = wjs_models.Correspondence.objects.create(
            user_cod=user_cod,
            source=source,
            email=imported_email,
            account=account,
        )
    elif mappings.count() >= 1:
        # We know this person from another journal
        logger.debug(
            f"wjs mapping exists ({mappings.count()} correspondences) for {user_cod}/{source} or {imported_email}"
        )
        mapping = check_mappings(mappings, imported_email, user_cod, source)

    account = mapping.account
    # `used` indicates that this usercod from this source
    # has been used to create the core.Account record
    if account_created:
        mapping.used = True
        mapping.save()

    # to activate hijack function
    if not account.is_active:
        account.is_active = True
        account.save()

    # Refresh the account to ensure the corresponding JCOMProfile exists,
    # otherwise we can get duplicated-email key-error when the cached object is saved.
    # As site notes, remember that Account.email is a case-insensitive field
    # (see https://www.postgresql.org/docs/current/citext.html)
    # while Correspondence.email is a simple EmailField.
    # Also, emails are normalized in Account.clean(),
    # but clean() is called by a ModelForm.is_valid() and not by Model.save().
    account.refresh_from_db()

    # set gdpr checkbox. privacy in wjapp can be 'Y', 'N' or empty string
    if not account.jcomprofile.gdpr_checkbox:
        if privacy == "Y":
            account.jcomprofile.gdpr_checkbox = True
        elif privacy == "N":
            account.jcomprofile.gdpr_checkbox = False
        else:
            # if we don't have a clear value we do nothing (which generally result in
            # the checkbox to be false, which is safe)
            pass
        account.jcomprofile.save()
        account.save()

    # import of user notes
    # E.g. JCOM_008A_0324
    # Only fill usernotes in if still empty - same additive-only rule as gdpr_checkbox above.
    # A user already imported (from this source or another journal) is left alone: whatever is
    # already there may be wjapp data from a previous run, or an EO's own manual correction made
    # since, and either way a later, unrelated article touching the same person must not clobber
    # it.
    if not account.jcomprofile.usernotes and (usernotes_rows := read_usernotes(user_cod, connection)):
        usernotes = get_usernotes(account, usernotes_rows)
        account.jcomprofile.usernotes = usernotes
        account.jcomprofile.save()
        logger.debug(f"saved {len(usernotes_rows)} usernotes for {account.id} {account.full_name()}")
        account.save()

    # saves the account in the global variable used as cache
    already_imported_users[user_cod] = account
    return account


def newlines_text_to_html(message: str) -> str:
    """Format Document_Layer message read from wjapp."""
    # TBV: format other new-line styles?
    #      Document_Layer messages/report from jcom jcomal are text message
    if not message:
        return ""
    message = message.strip()
    message = message.replace("\r\n", "<br>")
    message = message.replace("\n", "<br>")
    message = message.replace("\r", "<br>")
    return message


def file_from_response(response: requests.Response, name: str) -> DjangoFile:
    """Extract a (django) File object from the given response, and give it the given name."""
    return DjangoFile(BytesIO(response.content), name)


def build_targz_archive_from_tex_response(response: requests.Response, name: str) -> DjangoFile:
    """Extract a tex File object from the response, and returns a dj file tar.gz."""

    tar_buffer = BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        tar_info = tarfile.TarInfo(f"{name}.tex")
        tar_info.size = len(response.content)
        tar.addfile(tar_info, BytesIO(response.content))

    tar_buffer.seek(0)
    logger.debug(f"generated tar.gz archive from tex production submission for {name}")

    return DjangoFile(tar_buffer, f"{name}.tar.gz")


def noop(*args, **kwdargs):
    """Do nothing.

    Used to disabled WJS automated messages.
    """
    pass


def noop_true(*args, **kwdargs):
    """return True

    Used to disabled WJS conditions
    """
    return True


def truncate_with_ellipsis(s):
    return textwrap.shorten(s, width=6, placeholder="...")


# JCAP's pre-loaded Keyword catalog (see setup-docs/configure_submission's
# `import_keywords_json .../jcap-kwds.json`) uses Capitalized-first spelling; wjapp's own raw
# keyword strings are lowercase-first. 117 entries — maps old (lowercase-first) keyword to
# normalized version. Used by both the article importer (set_keywords) and the standalone editor
# importer (import_editor_keywords.sync_editor) so the two agree on which pre-loaded Keyword row a
# given wjapp string matches, instead of the editor path creating an un-normalized duplicate.
JCAP_KEYWORD_NORMALIZATION = {
    "astrophysical fluid dynamics": "Astrophysical fluid dynamics",
    "cosmic magnetic fields theory": "Cosmic magnetic fields theory",
    "extragalactic magnetic fields": "Extragalactic magnetic fields",
    "galactic magnetic fields": "Galactic magnetic fields",
    "magnetic fields": "Magnetic fields",
    "millisecond pulsars": "Millisecond pulsars",
    "pulsar magnetosphere": "Pulsar magnetosphere",
    "radio pulsars": "Radio pulsars",
    "solar physics": "Solar physics",
    "cosmological parameters from CMBR": "Cosmological parameters from CMBR",
    "gravitational waves and CMBR polarization": "Gravitational waves and CMBR polarization",
    "integrated Sachs-Wolfe effect": "Integrated Sachs-Wolfe effect",
    "non-gaussianity": "Non-gaussianity",
    "reionization": "Reionization",
    "cluster counts": "Cluster counts",
    "dark energy experiments": "Dark energy experiments",
    "dark energy theory": "Dark energy theory",
    "dark matter detectors": "Dark matter detectors",
    "dark matter experiments": "Dark matter experiments",
    "dark matter simulations": "Dark matter simulations",
    "dark matter theory": "Dark matter theory",
    "galaxy clustering": "Galaxy clustering",
    "rotation curves of galaxies": "Rotation curves of galaxies",
    "supernova type Ia - standard candles": "Supernova type Ia - standard candles",
    "weak gravitational lensing": "Weak gravitational lensing",
    "alternatives to inflation": "Alternatives to inflation",
    "axions": "Axions",
    "baryon asymmetry": "Baryon asymmetry",
    "big bang nucleosynthesis": "Big bang nucleosynthesis",
    "cosmic singularity": "Cosmic singularity",
    "cosmological applications of theories with extra dimensions": "Cosmological applications of theories with extra dimensions",  # noqa E501
    "cosmological perturbation theory": "Cosmological perturbation theory",
    "cosmological phase transitions": "Cosmological phase transitions",
    "cosmology of theories beyond the SM": "Cosmology of theories beyond the SM",
    "cosmology with extra dimensions": "Cosmology with extra dimensions",
    "extra dimensions": "Extra dimensions",
    "inflation": "Inflation",
    "initial conditions and eternal universe": "Initial conditions and eternal universe",
    "leptogenesis": "Leptogenesis",
    "particle physics - cosmology connection": "Particle physics - cosmology connection",
    "physics of the early universe": "Physics of the early universe",
    "primordial black holes": "Primordial black holes",
    "primordial magnetic fields": "Primordial magnetic fields",
    "quantum cosmology": "Quantum cosmology",
    "quantum gravity phenomenology": "Quantum gravity phenomenology",
    "recombination": "Recombination",
    "string theory and cosmology": "String theory and cosmology",
    "supersymmetry and cosmology": "Supersymmetry and cosmology",
    "transplanckian physics": "Transplanckian physics",
    "dwarfs galaxies": "Dwarfs galaxies",
    "feedback in galaxies": "Feedback in galaxies",
    "galaxy dynamics": "Galaxy dynamics",
    "galaxy evolution": "Galaxy evolution",
    "galaxy formation": "Galaxy formation",
    "galaxy morphology": "Galaxy morphology",
    "galaxy surveys": "Galaxy surveys",
    "high redshift galaxies": "High redshift galaxies",
    "hydrodynamical simulations": "Hydrodynamical simulations",
    "massive black holes": "Massive black holes",
    "semi-analytic modeling": "Semi-analytic modeling",
    "gravitational wave detectors": "Gravitational wave detectors",
    "gravitational waves / experiments": "Gravitational waves / experiments",
    "gravitational waves / sources": "Gravitational waves / sources",
    "gravitational waves / theory": "Gravitational waves / theory",
    "gravity": "Gravity",
    "modified gravity": "Modified gravity",
    "primordial gravitational waves  (theory)": "Primordial gravitational waves (theory)",  # also fixes double space # noqa E501
    "quantum black holes": "Quantum black holes",
    "quantum field theory on curved space": "Quantum field theory on curved space",
    "absorption and radiation processes": "Absorption and radiation processes",
    "accretion": "Accretion",
    "active galactic nuclei": "Active galactic nuclei",
    "cosmic ray experiments": "Cosmic ray experiments",
    "cosmic ray theory": "Cosmic ray theory",
    "cosmic rays detectors": "Cosmic rays detectors",
    "gamma ray burst experiments": "Gamma ray burst experiments",
    "gamma ray bursts theory": "Gamma ray bursts theory",
    "gamma ray detectors": "Gamma ray detectors",
    "gamma ray experiments": "Gamma ray experiments",
    "gamma ray theory": "Gamma ray theory",
    "particle acceleration": "Particle acceleration",
    "ultra high energy cosmic rays": "Ultra high energy cosmic rays",
    "baryon acoustic oscillations": "Baryon acoustic oscillations",
    "cosmic flows": "Cosmic flows",
    "cosmic web": "Cosmic web",
    "cosmological parameters from LSS": "Cosmological parameters from LSS",
    "cosmological simulations": "Cosmological simulations",
    "galaxy clusters": "Galaxy clusters",
    "gravitational lensing": "Gravitational lensing",
    "intergalactic media": "Intergalactic media",
    "power spectrum": "Power spectrum",
    "redshift  surveys": "Redshift surveys",  # also fixes double space # noqa E501
    "superclusters": "Superclusters",
    "cosmological neutrinos": "Cosmological neutrinos",
    "double beta decay": "Double beta decay",
    "neutrino astronomy": "Neutrino astronomy",
    "neutrino detectors": "Neutrino detectors",
    "neutrino experiments": "Neutrino experiments",
    "neutrino masses from cosmology": "Neutrino masses from cosmology",
    "neutrino properties": "Neutrino properties",
    "neutrino theory": "Neutrino theory",
    "solar and atmospheric neutrinos": "Solar and atmospheric neutrinos",
    "supernova neutrinos": "Supernova neutrinos",
    "ultra high energy photons and neutrinos": "Ultra high energy photons and neutrinos",
    "astrophysical black holes": "Astrophysical black holes",
    "core-collapse supernovae": "Core-collapse supernovae",
    "first stars": "First stars",
    "massive stars": "Massive stars",
    "neutron stars": "Neutron stars",
    "star explosions": "Star explosions",
    "star formation": "Star formation",
    "stars": "Stars",
    "supernova dynamics": "Supernova dynamics",
    "supernova evolution": "Supernova evolution",
    "supernova remnants": "Supernova remnants",
    "supernovas": "Supernovas",
    "white and brown dwarfs": "White and brown dwarfs",
}


def normalize_keyword(journal_code: str, kwd_word: str) -> str:
    """Normalize a raw wjapp keyword string before matching/creating a WJS Keyword.

    - JCOMAL: wjapp's keyword string packs all three languages separated by ";" - keep the
      first (English).
    - JCAP: wjapp's raw strings are lowercase-first; WJS's pre-loaded Keyword catalog (see
      JCAP_KEYWORD_NORMALIZATION above) is Capitalized. Unrecognized strings pass through
      unchanged (and will be created as new Keyword rows by the caller - a sign the mapping
      above needs a new entry).
    - Other journals: no normalization.
    """
    if journal_code.upper() == "JCOMAL":
        return kwd_word.split(";")[0].strip()
    if journal_code.upper() == "JCAP":
        return JCAP_KEYWORD_NORMALIZATION.get(kwd_word, kwd_word)
    return kwd_word


def get_reminders_for_article(article, only_enabled=False, code_startswith=None):
    """Get reminders related to an article via WorkflowReviewAssignment or WjsEditorAssignment or similar."""

    article_reminders = Q(
        content_type=ContentType.objects.get_for_model(submission_models.Article),
        object_id=article.pk,
    )
    review_assignments = WorkflowReviewAssignment.objects.filter(article=article).values_list("pk")
    reviewer_reminders = Q(
        content_type=ContentType.objects.get_for_model(WorkflowReviewAssignment),
        object_id__in=review_assignments,
    )
    editor_assignments = WjsEditorAssignment.objects.filter(article=article).values_list("pk")
    editor_reminders = Q(
        content_type=ContentType.objects.get_for_model(WjsEditorAssignment),
        object_id__in=editor_assignments,
    )
    revision_requests = EditorRevisionRequest.objects.filter(article=article).values_list("pk")
    author_reminders = Q(
        content_type=ContentType.objects.get_for_model(EditorRevisionRequest),
        object_id__in=revision_requests,
    )

    qs = Reminder.objects.filter(article_reminders | editor_reminders | reviewer_reminders | author_reminders)
    if only_enabled:
        qs = qs.filter(disabled=False)
    if code_startswith:
        qs = qs.filter(code__startswith=code_startswith)
    return qs


#
# End global variables and functions section
#


@dataclass
class ImportPermissionsManager:
    """Data class that manages the import of all the visibility permissions from wjapp.

    In wjapp when EDREP, REREP or CVLETT are created,
    several rows are added to User_Rights:
    - one for the "author" for the document (userType=author; e.g. for a REREP, the reviewer)
    - one for the "recipient" of the document (userType=recipient; e.g. for a REREP, the editor)
    - several other rows with userType=reader for other actors
      (e.g., for CVLETT, one row for each reviewer)

    User_Right thus links a user, a document(layer), and his visibility permissions.

    Mapping with wjs permissions types:
    in wjapp User_Rights:
    - when userType != "reader" for document-layers of type EDREP|REREP -> default permissions - nothing to do
    - when userType="reader", authorNameVisibility=0  (author name not visible) -> NO_NAMES
    - when userType="reader", authorNameVisibility=1  (author name visible) -> ALL
    - NB: for CVLETT, when the row userType="reader" is _missing_ -> DENY

    Objects interested:
    - CVLETT
      - data stored in EditorRevisionRequest.author_note/cover_letter_file
      - non-default permissions stored:
        - for reviewers as PermissionAssignment; see specs#1305
        - for (new) editors, as PermissionAssignment

    - EDREP
      - data stored in EditorDecision.decision_editor_report (referenced by ERR.editor_decision)
      - non-default permissions stored as PermissionAssignments with target=EditorDecision

    - REREP
      - data stored in WorflowReviewAssignment.report_form_answers
        which is a json field, and contains:
        - editor_cover_letter (cover letter REREP)
        - author_review (text REREP)
      - non-default permissions stored as PermissionAssignments

     Implementation:

     a) during the action import, when EDREP REREP CVLETT are saved, we keep a global nested dict with
        (documentLayerCod, {wjs_obj, type})
     b) at the end of article import:
        1. we read from wjapp the list of the extra permissions for the EDREP REREP CVLETT of the article
        2. the extra permission is set for each type
           CVLETT needs also to be set as DENY if the referee does not appear in the visibility rights of wjapp
           because this case means that the visibility right has been removed
     c) during the article import we also keep a global dict with (userCod, account_obj) to optimize the article
        users check. This dict is updated (and used as cache) by the global
        function account_get_or_create_check_correspondence()
    """

    connection: mariadb.Connection
    session: requests.sessions.Session
    journal: Journal
    preprintid: str
    article: submission_models.Article
    imported_doclayer_check_visibility: dict

    def run(self):
        """Import wjapp permissions for the imported article."""

        # paper that can be used as import test: JCOM_001A_0924

        for key, value in self.imported_doclayer_check_visibility.items():
            logger.debug(
                f"import permission documentLayerCod {key} class: {value['obj'].__class__} type: {value['type']}"
            )

            # from obj define content_type_id
            content_type = ContentType.objects.get_for_model(value["obj"])

            # read existing wjapp permission "reader" for documentLayerCod
            readers_rows = self.read_wjapp_permissions(key)
            logger.debug(f"found wjapp user rights readers: {readers_rows}")
            readers = []
            for r in readers_rows:
                # get reader account
                account = account_get_or_create_check_correspondence(
                    self.journal.code.lower(),
                    r["userCod"],
                    r["lastname"],
                    r["firstname"],
                    r["email"],
                    r["privacy"],
                    self.connection,
                )
                readers.append(account)

            # Manage reviewers which do not have visibility rights on cover letter
            # in wjapp the visibility right for this user has been removed
            # (i.e. it is a "missing" row in User_Rights)
            #
            # the ERR contains the review round of the version before the author revision.
            # The next review round is created by the logic of AuthorHandleRevision.
            #
            # Foreach wjs reviewer of the wra related to the new author version, we set
            # permission to DENY if in wjapp is not reader of the imported CVLETT
            if value["type"] in ("CVLETT"):
                revision_review_round = value["obj"].review_round.round_number + 1
                rr = ReviewRound.objects.get(article=self.article, round_number=revision_review_round)
                for wra in WorkflowReviewAssignment.objects.filter(article=self.article, review_round=rr):
                    if wra.reviewer not in readers:
                        # deny permission for cvlett
                        permission_type = PermissionAssignment.PermissionType.DENY
                        permission_secondary_type = PermissionAssignment.PermissionType.DENY
                        pa, created = PermissionAssignment.objects.get_or_create(
                            content_type_id=content_type.pk,
                            object_id=value["obj"].pk,
                            user=wra.reviewer,
                            defaults={
                                "permission": permission_type.value,
                                "permission_secondary": permission_secondary_type.value,
                            },
                        )
                        if not created:
                            # if existing only the permission secondary is changed
                            pa.permission_secondary = permission_secondary_type.value
                            pa.save()

            # add rights from wjapp present in User_Rights
            for reader_account in readers:
                # set wjs permission for the reader with PermissionAssignment get or create
                # i.e. JCOM_001A_0924 for CVLETT reader
                if value["type"] in ("REREP", "EDREP"):
                    if r["authorNameVisibility"]:
                        permission_type = PermissionAssignment.PermissionType.ALL
                    else:
                        permission_type = PermissionAssignment.PermissionType.NO_NAMES

                    # sets only primary permission if existing or not
                    pa, created = PermissionAssignment.objects.get_or_create(
                        content_type_id=content_type.pk,
                        object_id=value["obj"].pk,
                        user=reader_account,
                        defaults={
                            "permission": permission_type.value,
                        },
                    )
                    if not created:
                        pa.permission = permission_type.value
                        pa.save()

                elif value["type"] in ("CVLETT"):
                    # NOTE: in wjapp the first versions has no cover letter, not necessary
                    # to manage the case (the cover letter in first version would be related
                    # to Article/ArticleWorkflow object)

                    # NOTE: this does not change the editor report PA for the user which are
                    # defined on the object EditorRevisionRequest.editor_decision

                    # set permission only for cvlett
                    permission_type = PermissionAssignment.PermissionType.DENY

                    # EditorRevisionRequest secondary permission manages the author cover letter
                    permission_secondary_type = PermissionAssignment.PermissionType.ALL

                    # TBV: the permission is created both for editor or reviewer user because if already exists
                    # is only updated, otherwise is created, but coherently with the permissions logic.
                    # In the case that the user is the reviewer and not the new editor, could be *not* necessary
                    # to set the permission (set by deafult).
                    pa, created = PermissionAssignment.objects.get_or_create(
                        content_type_id=content_type.pk,
                        object_id=value["obj"].pk,
                        user=reader_account,
                        defaults={
                            "permission": permission_type.value,
                            "permission_secondary": permission_secondary_type.value,
                        },
                    )
                    if not created:
                        # if existing only the permission secondary is changed
                        pa.permission_secondary = permission_secondary_type.value
                        pa.save()

                # check which permissions have been set for the object
                custom_permission = PermissionAssignment.objects.filter(
                    user=reader_account,
                    content_type=content_type,
                    object_id=value["obj"].pk,
                )
                for cp in custom_permission:
                    logger.debug(
                        f"PA existing - user: {cp.user} CT: {cp.content_type} obj: {cp.object_id} "
                        f"primary: {cp.permission} secondary: {cp.permission_secondary}"
                    )

    def read_wjapp_permissions(self, document_layer_cod):
        """Read the "reader" rows for the document_layer_cod."""

        cursor_all_reader_rights = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )
        query_all_reader_rights = """
SELECT
ur.documentLayerCod,
ur.userCod,
ur.userType,
ur.authorNameVisibility,
u.lastname,
u.firstname,
u.email,
u.privacy
FROM User_Rights ur
LEFT JOIN User u USING (userCod)
WHERE
    ur.documentLayerCod = (%(document_layer_cod)s)
AND ur.userType='reader'
"""
        cursor_all_reader_rights.execute(
            query_all_reader_rights,
            {
                "document_layer_cod": document_layer_cod,
            },
        )

        all_reader_rights = cursor_all_reader_rights.fetchall()
        cursor_all_reader_rights.close()
        return all_reader_rights


@dataclass
class ImportCorrespondenceManager:
    """Data class that manages the import of all the correspondence of the wjapp imported version."""

    # short description of all the correspondence types (all the wjapp journals)
    #
    # DOC LAYER EMAIL: EMAIL
    # DOC LAYER COVERLETTER: CVLETT
    # DOC LAYER REFEREE REPORT:REREP
    # DOC LAYER EDITOR REPORT:EDREP
    # DOC LAYER ANNOTATION:ANNOT
    # DOC LAYER TPS ANNOTATION:TPSAN
    # DOC LAYER PM ANNOTATION:PMANN
    # DOC LAYER PUM ANNOTATION: PUMANN
    # DOC LAYER AU ANNOTATION: AUANN
    # DOC LAYER ED ANNOTATION: EDANN
    #
    # TO ADMIN EMAIL: TOADE
    # FROM ADMIN EMAIL: FRADE
    # ED TO AUT EMAIL: ETOAE
    # AUT TO ED EMAIL: ATOEE
    # TO PM EMAIL: TOPME
    # TO PUM EMAIL: TOPUM
    # FROM PM EMAIL: FRPME
    # FROM PUM EMAIL: FRPUM
    # TO DIR EMAIL: TODIE
    # FROM DIR EMAIL: FRDIE
    # TO ASSISTDIR EMAIL: TOASDIE
    # FROM ASSISTDIR EMAIL: FRASDIE
    # FROM SUD EMAIL: FRSUD
    # TO SUD EMAIL: TOSUD
    # FROM MOB EMAIL: FRMOB
    # TO MOB EMAIL: TOMOB
    #
    # DOC LAYER REMINDER: REMIN
    #
    # First distintion for reminders
    # DOC LAYER EDITOR REMINDER: EDREM
    # DOC LAYER REFEREE REMINDER: REREM
    # DOC LAYER AUTHOR REMINDER: AUREM
    #
    # Second distintion for reminders
    # DOC LAYER EDITOR REFEREE REMINDER: EREMR
    # DOC LAYER EDITOR AUTHOR REMINDER : EREMA
    # DOC LAYER ADMIN EDITOR REMINDER: AREME
    # DOC LAYER ADMIN REFEREE REMINDER: AREMR
    # DOC LAYER ADMIN AUTHOR REMINDER: AREMA
    # DOC LAYER PM AUTHOR REMINDER: PREMA
    # DOC LAYER PM TYPESETTER REMINDER: PREMT

    connection: mariadb.Connection
    session: requests.sessions.Session
    journal: Journal
    preprintid: str
    article: submission_models.Article
    imported_version_num: int
    imported_version_cod: int
    importfiles: bool
    imported_document_layer_cod_list: list

    def run(self):
        """Import wjapp correspondence for the imported_version."""

        # The management of all the types implies where it is necessary
        # to set author or recipient coeherently with the
        # type before the message creation because the user data are not
        # read from wjapp database

        # to be used in case of runtime problem on a specific type
        self.types_skipped = []

        # types message "to admin", in wjs added eo_user as recipient
        # TODO: manage files attached to AUANN
        self.types_to_admin_list = [
            "AUANN",  # production info sent by author to eo_user can have files attached
            "TOADE",
            "TOPME",  # to pm (in wjs to eo_user)
            "TPSAN",  # typ annotation (in wjs to eo_user)
        ]

        # types message "from admin", in wjs added eo_user as author
        # FRPME: e.g. JCOM_002A_1216, JCOM_016A_0418
        # TBV: TOPUM needs jcom publication manager user jcom-pum@jcom.sissa.it but set eo_user
        self.types_from_admin_list = [
            "FRPME",  # from pm (in wjs from eo_user) to someone (author, typesetter, ...)
            "FRADE",
            "AREMA",
            "AREME",
            "AREMR",
            "PREMA",  # pm (in wjs from eo_user) auth reminder
            "PREMT",  # pm (in wjs from eo_user) typesetter reminder
            "PMANN",  # pm (in wjs from eo_user) annotation
            "TOPUM",  # from eo_user to publisher jcom publication manager user jcom-pum@jcom.sissa.it
        ]

        # types which have always author and recipient taken from wjapp database
        self.types_with_auth_and_recipient = [
            "ATOEE",
            "EDANN",
            "EMAIL",
            "EREMA",
            "EREMR",
            "ETOAE",
            "FRASDIE",
            "FRDIE",
            "TOASDIE",
            "TODIE",
        ]

        # PUMANN can have no authors: JCOM_002N_0724
        self.types_with_auth_OR_recipient = [
            "PUMANN",
            "FRPUM",
        ]

        self.managed_types_list = (
            self.types_with_auth_and_recipient
            + self.types_with_auth_OR_recipient
            + self.types_from_admin_list
            + self.types_to_admin_list
            + self.types_skipped
        )

        for m in self.read_all_messages_of_imported_version():
            # as wjs msg.recipient added from wjapp: recipient, reader, readerCC
            # readerBCC are excluded by the query on wjapp
            (author_from_wjapp, message_recipients_no_bcc) = self.read_message_author_recipients(m["documentLayerCod"])

            if (
                author_from_wjapp
                and message_recipients_no_bcc
                and (m["documentLayerType"] in self.types_with_auth_and_recipient)
            ):
                # the messages "Referee confirms assignment for" has to appear as system messages in wjs
                if m["documentLayerType"] == "EMAIL" and any(
                    (m["documentLayerSubject"] or "").startswith(sub)
                    for sub in [
                        "Referee confirms assignment for",
                        "Author selects coauthor for document",
                        "Referee declines assignment for",
                    ]
                ):
                    message_type = Message.MessageTypes.SYSTEM
                else:
                    message_type = Message.MessageTypes.USER
            elif m["documentLayerType"] in ["TPSAN", "PMANN", "AUANN", "AREMA", "AREME", "AREMR", "PREMA", "PREMT"]:
                message_type = Message.MessageTypes.SYSTEM
            else:
                message_type = Message.MessageTypes.USER

            # managed types
            if m["documentLayerType"] not in self.managed_types_list:
                raise RuntimeError(
                    f"msg {m['documentLayerCod']} type not managed {self.article.id} {m['documentLayerSubject']}"
                    f" {m['documentLayerType']}"
                )

            if m["documentLayerType"] in self.types_skipped:
                # demoted to warning for JHEPST/JCAPST, if considered error, the execution must be stopped
                # with an exception
                logger.warning(
                    f"msg {m['documentLayerCod']} type skipped {self.article.id} {m['documentLayerSubject']}"
                    f" {m['documentLayerType']}"
                )
                continue

            if m["documentLayerType"] in self.types_from_admin_list:
                # author set directly, not read from wjapp
                author = get_eo_user(self.journal)
            elif not author_from_wjapp and (m["documentLayerType"] in self.types_with_auth_OR_recipient):
                # case like JCOM_002N_0724
                author = get_eo_user(self.journal)
            else:

                # Note: only for jhep/jcap stress test
                if self.journal.code in ["JHEP", "JCAP"] and not author_from_wjapp:
                    author = get_eo_user(self.journal)
                    logger.debug(f"added author JST:{author}")
                else:
                    if not author_from_wjapp:
                        raise RuntimeError(
                            f"msg {m['documentLayerCod']} Missing author. type {m['documentLayerType']}"
                        )

                    # author data comes from wjapp
                    author = account_get_or_create_check_correspondence(
                        self.journal.code.lower(),
                        author_from_wjapp[0]["userCod"],
                        author_from_wjapp[0]["lastname"],
                        author_from_wjapp[0]["firstname"],
                        author_from_wjapp[0]["email"],
                        author_from_wjapp[0]["privacy"],
                        self.connection,
                    )

            document_layer_subject = (
                m["documentLayerSubject"]
                if m["documentLayerSubject"]
                else truncate_with_ellipsis(m["documentLayerText"])
            )
            with freezegun.freeze_time(
                rome_timezone.localize(m["submissionDate"]),
            ):
                msg = Message.objects.create(
                    actor=author,
                    subject=document_layer_subject,
                    body=newlines_text_to_html(m["documentLayerText"]),
                    content_type=ContentType.objects.get_for_model(self.article),
                    object_id=self.article.id,
                    message_type=message_type,
                )
                logger.debug(f"msg {m['documentLayerCod']} imported: {document_layer_subject}")

                # recipient management

                # if not message_recipients_no_bcc or "recipient" not in message_recipients_no_bcc.values():
                if m["documentLayerType"] in self.types_to_admin_list:
                    msg.recipients.add(get_eo_user(self.journal))
                    logger.debug(f"msg {m['documentLayerCod']} add recipient eo_user {document_layer_subject}")

                for msg_rec in message_recipients_no_bcc:
                    # Note: only for jhep/jcap stress test
                    if self.journal.code in ["JHEP", "JCAP"] and not msg_rec["userCod"]:
                        recipient = get_eo_user(self.journal)
                        logger.debug(f"added recipient JST:{recipient}")
                    else:
                        recipient = account_get_or_create_check_correspondence(
                            self.journal.code.lower(),
                            msg_rec["userCod"],
                            msg_rec["lastname"],
                            msg_rec["firstname"],
                            msg_rec["email"],
                            msg_rec["privacy"],
                            self.connection,
                        )
                    if recipient not in msg.recipients.all():
                        msg.recipients.add(recipient)

                # management of special cases
                # TBV: correct management of this type?
                if not msg.recipients.exists() and m["documentLayerType"] in ("FRASDIE", "FRDIE", "TOPUM"):
                    msg.recipients.add(get_eo_user(self.journal))

                if not msg.recipients.exists() and m["documentLayerType"] in self.types_with_auth_OR_recipient:
                    msg.recipients.add(get_eo_user(self.journal))

                special_cases_no_recipients = (255874, 253593, 254104, 253252, 253253, 249057, 250742, 251797)
                if m["documentLayerCod"] in special_cases_no_recipients:
                    msg.recipients.add(get_eo_user(self.journal))
                    logger.debug(f"add recipient eo user for special case {m['documentLayerCod']}")

                # error if no recipients at all from wjapp and not added eo_user
                if not msg.recipients.all():
                    # Note: Only for jhep/jcap stress test
                    if self.journal.code in ["JHEP", "JCAP"]:
                        msg.recipients.add(get_eo_user(self.journal))
                        logger.debug(f"add recipient eo for {self.journal.code.upper()}ST")
                    else:
                        raise RuntimeError(
                            f"msg {m['documentLayerCod']} without recipients: {self.article.id}"
                            f" {document_layer_subject} {m['documentLayerType']} {message_recipients_no_bcc=}"
                        )

        logger.debug(f"imported correspondence for {self.preprintid}/{self.imported_version_num}")

    def read_all_messages_of_imported_version(self):
        """Read all the messages of the imported version."""

        cursor_all_messages = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # read all messages for imported version
        # Please note that 'REREP', 'EDREP', 'CVLETT' are reviewer and editor reports and
        # author's cover letter, which are managed elsewhere.
        #
        # 'AUANN' is the author annotation, i.e. the corrections sent by the author to the typesetter.
        # 'AUANN' text is contained in Document_Layer (documentLayerText), but can be associated
        # to a file (zip, pdf, jpg, ...), which is saved in the wjapp archive in
        # "preprintid/version/AUANN/documentLayerID/documentLayerID.file_extension".
        # The file extension is not saved in the wjapp database.
        #
        # E.g. in JCOM_017A_0624/4/
        #
        # AUANN/JCOM_017A_0624_AUANN004381124/JCOM_017A_0624_AUANN004381124.zip
        query_all_messages = """
SELECT
dl.documentLayerCod,
dl.documentLayerSubject,
dl.documentLayerText,
dl.documentLayerOnlyTex,
dl.documentLayerType,
dl.submissionDate
FROM Document_Layer dl
WHERE
    versioncod=%(imported_version_cod)s
AND dl.documentLayerType NOT IN ('REREP', 'EDREP', 'CVLETT', 'AUANN')
ORDER BY dl.submissionDate
"""
        cursor_all_messages.execute(
            query_all_messages,
            {
                "imported_version_cod": self.imported_version_cod,
            },
        )

        all_messages = cursor_all_messages.fetchall()
        cursor_all_messages.close()
        all_messages_not_yet_imported = []
        for m in all_messages:
            if m["documentLayerCod"] not in self.imported_document_layer_cod_list:
                all_messages_not_yet_imported.append(m)
                logger.debug(
                    f"msg found: {m['documentLayerCod']} {m['documentLayerType']} {m['documentLayerSubject']}"
                )
            else:
                logger.debug(
                    f"msg already imported: {m['documentLayerCod']} {m['documentLayerType']} "
                    f"{m['documentLayerSubject']}"
                )

        if not all_messages_not_yet_imported:
            logger.warning(f"Found 0 messages for {self.preprintid}/{self.imported_version_num}")

        return all_messages_not_yet_imported

    def read_message_author_recipients(self, document_layer_cod):
        """Read the author and all the recipients of a message but BCC."""
        # WJS does not allow for Bcc messages, so we decided to silently ignore them.
        # The alternative (import the Bcc recipients as Cc) is not viable,
        # because we should not disclose an info that the message's author considered private.
        cursor_all_message_author_recipients = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        query_all_message_author_recipients = """
SELECT
ur.documentLayerCod,
ur.userCod,
ur.userType,
u.lastname,
u.firstname,
u.email,
u.privacy
FROM User_Rights ur
LEFT JOIN User u USING (userCod)
WHERE
    ur.documentLayerCod = (%(document_layer_cod)s)
AND ur.userType!='readerBCC'
"""
        cursor_all_message_author_recipients.execute(
            query_all_message_author_recipients,
            {
                "document_layer_cod": document_layer_cod,
            },
        )
        if cursor_all_message_author_recipients.rowcount == 0:
            # Note: only for JHEP/JCAP ST
            if self.journal.code in ["JHEP", "JCAP"]:
                logger.debug(
                    f"Found {cursor_all_message_author_recipients.rowcount} users for message {document_layer_cod}"
                    f" {self.preprintid}/{self.imported_version_num}"
                )
            else:
                logger.error(
                    f"Found {cursor_all_message_author_recipients.rowcount} users for message {document_layer_cod}"
                    f" {self.preprintid}/{self.imported_version_num}"
                )
                raise ValueError("Recipients not found")
            all_message_author_recipients = []
        else:
            all_message_author_recipients = cursor_all_message_author_recipients.fetchall()

        author = [a for a in all_message_author_recipients if a["userType"] == "author"]
        assert len(author) in {0, 1}
        recipients = [r for r in all_message_author_recipients if r["userType"] != "author"]

        cursor_all_message_author_recipients.close()
        return (author, recipients)


@dataclass
class BaseActionManager:
    """Data class that manages one action."""

    # one of the records returned by of the read_history_data() / action-history
    # item (agent, target, version_code, etc.)
    action: dict
    connection: mariadb.Connection
    session: requests.sessions.Session
    journal: Journal
    preprintid: str
    publicationid: str
    document_revision_dead_line: datetime
    article: submission_models.Article
    imported_version_num: int
    imported_version_cod: int
    imported_version_state_cod: int
    importfiles: bool
    imported_document_layer_cod_list: list
    action_triggers_import_files: bool
    """flag used when not all actions of a family have to import files"""
    imported_doclayer_check_visibility: dict
    url_base: str  # = getattr(settings, "WJAPP_JCOM_BASE_URL", None)
    current_archive: str
    old_archive: str

    def run(self):
        raise NotImplementedError

    def get_current_editor(self):
        return WjsEditorAssignment.objects.get_current(self.article).editor

    def check_editor_set(self):
        if not self.get_current_editor():
            logger.error(f"editor not set for {self.preprintid} {self.article.id}")
            self.connection.close()
            raise Exception

    def fix_pre_withdrawn_state(self):
        """Fix the state before author/admin withdrawn action."""

        # note: before the admin/author withdrawn action the editor can be set or not.
        # E.g. editor not set: JCAP_012P_0810
        # The wjs action WithdrawPreprint manages both cases (editor set or not)
        # In wjapp, in some cases, the action withdrawn can follow directly the submission
        # with failed editor assignement without the ED_REF_DOC action
        # therefore the state must be forced (e.g. JCAP_029P_0211)
        try:
            self.check_editor_set()
        except WjsEditorAssignment.DoesNotExist:
            self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
            logger.debug(
                f"missing editor before withdrawn {self.preprintid} / {self.article.id}"
                f" state forced to {self.article.articleworkflow.state}"
            )

    def read_attachments_data(self):
        """Read wjapp attachments data for the imported version."""

        cursor_attachments = self.connection.cursor(dictionary=True)
        query_attachments = """
SELECT
attachID,
attachTitle,
attachDescription,
attachType,
attachFormat,
submissionDate
FROM Attachment
WHERE
    versioncod=%(imported_version_cod)s
ORDER BY submissionDate
"""
        cursor_attachments.execute(
            query_attachments,
            {
                "imported_version_cod": self.imported_version_cod,
            },
        )
        attachments_data = cursor_attachments.fetchall()
        cursor_attachments.close()
        return attachments_data

    def read_reminder_data(self):
        """Read wjapp reminder data for the imported action.
        Wjapp reminders data are related to the single actHistCod"""

        cursor_reminder = self.connection.cursor(dictionary=True)
        query_reminder = """
SELECT
reminderCod,
reminderDate,
actHistCod,
performerCod,
availabilityChecker,
type,
enabled,
sent,
disableAllowedRoles
FROM Reminder
WHERE
    actHistCod=%(actHistCod)s
ORDER BY reminderDate
"""
        cursor_reminder.execute(
            query_reminder,
            {
                "actHistCod": self.action["actHistCod"],
            },
        )
        reminder_data = cursor_reminder.fetchall()
        cursor_reminder.close()
        return reminder_data

    def check_and_fix_action_reminder(self, wjapp_type, wjs_type, reminder_number):
        """Check if wjapp reminder for an action is present and fix due date if necessary.

        Wjapp permits to set 2 or 3 reminder, depending on the action type,
        but the single configuration of a wjapp system can use less reminders.
        For example, wjapp allows up to 3 "New_submission" reminders
        but the jcom cofiguration sets only 2 "New_submission" reminders,
        while the configuration of jhep sets 3 New_submission submission reminders.

        Therefore calling this funcion is used the maximum reminder_number to manage
        the reminder type independently from the configuration of the single instance
        (the loop iteration happens only if the reminder is found on wjapp).

        In any case the reminder date is changed only if the reminder is present for
        the specific action in wjapp and if the logic of wjs creates the reminder for
        the corresponding wjs action

        Associations in import among wjapp reminder and wjs reminder:

        wjapp reminder <-> wjs reminder

        - pending_minor_revision <-> AUMIR
        - pending_revision <-> AUMJR
        - EditorAssignment
            - New_submission <-> EDSR
            - ed_selected_by_ed <-> EDSR
            - ed_selected_by_EO <-> EDSR
            - major_revision <-> EDSR
            - minor_revision <-> EDSR
        - REF_SEND_REP
            - only_ref_sent_rep <-> EDMD
            - last_ref_sent_rep <-> EDMD
        - ReviewerDeclineAction
            - ref_declined_ed_decision  <-> EDMD
            - Ref_decline <-> EDSR
        - DeselectReviewerAction
            - ed_removed_ed_decision <-> EDMD
        - ED_ACT_AS_REF
            - i_will_review <-> REWR

        not imported:
            - Ref_removed -> it means that the editor is still waiting for reports
            - appeal_confirm_ed_revised
            - appeal_confirm_ed_confirm
            - appeal_reset_ed
            - pending_appeal
            - wait_copyright
        """

        # TBV: when wjs sets 3 reminder of type YYYY and wjapp has 2 corresponding reminder with fixed date,
        # YYYY3 due_date can be earlier than YYYY1 and YYY2.
        # Chosen to remove YYYY3. Better to shift it with some criteria?
        # i.e. JCOM_028A_0724
        # TODO: FIXME for JHEP/JCAP: (e.g. YYYY3.due_date = YYYY2.due_date + 2days)
        # TODO: ATM the code ignores the case when we have more reminders in wjapp that in wjs
        #       this is because the configuration of JCOM does not allow it.

        wjapp_reminder_list = [
            r_wjapp for r_wjapp in self.read_reminder_data() if r_wjapp["type"].startswith(wjapp_type)
        ]
        if not wjapp_reminder_list:
            return

        wjs_reminder_list = []
        for r_wjs in get_reminders_for_article(self.article, code_startswith=f"{wjs_type}"):
            wjs_reminder_list.append(r_wjs)
        if not wjs_reminder_list:
            return

        # This flag indicates if any reminder of the type under examination has been changed.
        # This is used to decide if we should delete reminders that exists in wjs but did not exist in wjapp.
        wjs_reminder_type_changed = False
        for i in range(1, reminder_number + 1):
            for reminder in wjs_reminder_list:
                if reminder.code == f"{wjs_type}{i}":
                    logger.debug(f"exists wjs reminder for {self.article.id}: {reminder} {reminder.date_due}")
                    wjs_reminder_match = False
                    for wjapp_reminder in wjapp_reminder_list:
                        if wjapp_reminder["type"] == f"{wjapp_type}_{i}":
                            logger.debug(
                                f"found wjapp reminder for {self.preprintid} action {self.action['actionID']} "
                                f" {self.action['actHistCod']} {wjapp_reminder['type']} "
                                f"{wjapp_reminder['reminderDate'].date()}"
                            )
                            if reminder.date_due != wjapp_reminder["reminderDate"].date():
                                logger.debug(
                                    f"reminder {reminder.code} due date changed from {reminder.date_due} "
                                    f"to {wjapp_reminder['reminderDate'].date()}"
                                )
                                reminder.date_due = wjapp_reminder["reminderDate"].date()
                                reminder.save()
                                wjs_reminder_type_changed = True
                                if reminder.code == "REWR1":
                                    wra = WorkflowReviewAssignment.objects.get(pk=reminder.object_id)
                                    wra.date_due = wjapp_reminder["reminderDate"].date()
                                    wra.save()
                                    logger.warning(f"changed RA.date_due as REWR1: {wra.date_due} for RA {wra}")

                            else:
                                logger.debug(
                                    f"reminder {reminder.code} due date NOT changed because equal {reminder.date_due} "
                                    f"{wjapp_reminder['reminderDate'].date()}"
                                )
                            if wjapp_reminder["sent"]:
                                reminder.date_sent = wjapp_reminder["reminderDate"]
                                logger.debug(f"reminder date_sent added: {reminder}")
                                reminder.save()
                            if not wjapp_reminder["enabled"]:
                                reminder.disable = True
                                logger.warning(f"reminder disabled: {reminder}")
                                reminder.save()
                            wjs_reminder_match = True

                    if not wjs_reminder_match and wjs_reminder_type_changed:
                        reminder.delete()
                        logger.warning(
                            f"DELETED wjs reminder {self.article.id}: {reminder.code} {reminder.date_due} "
                            f"not found wjapp reminder: {wjapp_type}_{i} for {self.preprintid} action "
                            f"{self.action['actionID']} because changed wjs reminder {wjs_type}"
                        )
                    else:
                        logger.debug(
                            f"NOT deleted {not wjs_reminder_match=} {wjs_reminder_type_changed=} "
                            f"{reminder.code=} {wjapp_reminder['type']=} {wjapp_type=}"
                        )

    def check_and_fix_all_reminder(self):
        # this method has to be be overriden, if necessary to check specific reminders
        pass


@dataclass
class EditorAssignmentAction(BaseActionManager):
    """Manages editor assignment action."""

    def run(self):
        """Editor assignment management."""

        # these map (roughly) to EditorAssignment
        editor_cod = self.action["targetCod"]
        editor_lastname = self.action["targetLastname"]
        editor_firstname = self.action["targetFirstname"]
        editor_email = self.action["targetEmail"]
        editor_privacy = self.action["targetPrivacy"]
        editor_assign_date = self.action["actionDate"]
        editor_maxworkload = self.action["targetEditorWorkload"]

        # In wjapp the admin ADMIN_ASS_N_ED is done with two acions:
        # 1. "admin" resets editor "editor" for "preprintid" -> creates new version copying the files
        # 2. "admin selects editor "new editor" for "preprintid"
        #
        # This case in the import does not generate a new version in wjs, only a
        # SupervisorChangeEditorAssignment for action 2
        #
        # there are wjapp actions SYS_ASS_ED with editor assigned None
        # example: JCOM_003A_0424 version 2

        # TO BE VERIFIED: JCOM_001E_0318 - only 1 strange case in jcom of reset editor with editor directly assigned
        if editor_cod:
            # attribute editor added
            self.set_editor(
                editor_cod,
                editor_lastname,
                editor_firstname,
                editor_email,
                editor_assign_date,
                editor_privacy,
            )

            # added attribute editor parameters
            editor_parameters = self.read_editor_parameters(editor_cod)
            self.set_editor_parameters(editor_parameters, editor_maxworkload)

        # File import must not depend on an editor actually being assigned: it is driven only by
        # the version/action context (self.imported_version_cod etc.), already set regardless of
        # editor_cod. Keeping this outside the "if editor_cod:" block above fixes SYS_ASS_ED firing
        # with no editor target (wjapp couldn't auto-assign one) - previously files were silently
        # never imported for that version (e.g. JCOM_003Y_0821, JCAP_017P_0115), unless some later
        # unconditional action re-imported them. It also fixes the same gap for SYS_ASS_ED "reused"
        # after an admin reset-editor (target None again, see comment above): that reused call now
        # imports its own (new) version's files directly, instead of being skipped like the base
        # version's files were before.
        #
        # Confirmed via SQL that no wjapp versionCod ever has more than one SYS_ASS_ED in its
        # Action_History, so this can't double-import the same version's files; import_version_files()
        # is also idempotent by construction (clears source/manuscript files before re-adding) even
        # if that were to change.
        if self.action_triggers_import_files and self.importfiles:
            import_file_manager.ImportFileManager(self).import_version_files()

    # TODO: check why new review_round is not created
    def set_editor(
        self, editor_cod, editor_lastname, editor_firstname, editor_email, editor_assign_date, editor_privacy
    ):
        """Assign the editor.

        Also create the editor's Account if necessary.
        """
        self.editor_to_assign = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            editor_cod,
            editor_lastname,
            editor_firstname,
            editor_email,
            editor_privacy,
            self.connection,
        )

        # An account must have the "section-editor" role on the journal to be able to be assigned as editor of an
        # article.
        if not self.editor_to_assign.check_role(self.journal, "section-editor", staff_override=False):
            self.editor_to_assign.add_account_role("section-editor", self.journal)

        logger.debug(
            f"Assigning {self.editor_to_assign.last_name} {self.editor_to_assign.first_name} onto {self.article.pk}"
        )

        self.ed_assign_request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        GlobalRequestMiddleware.process_request(self.ed_assign_request)

        with freezegun.freeze_time(
            rome_timezone.localize(editor_assign_date),
        ):
            self.run_business_logic()

        self.article.refresh_from_db()
        return self.editor_to_assign

    def run_business_logic(self):
        """Default editor-assignment logic.

        This method can be overridden by derived classes to tweak the assignment operation.
        """

        # Manually move into a state where editor assignment can take place
        # TODO: check if this is not the case already...
        self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        self.article.articleworkflow.save()

        AssignToEditor(
            article=self.article,
            editor=self.editor_to_assign,
            request=self.ed_assign_request,
        ).run()
        self.article.save()
        self.check_and_fix_all_reminder()

    def read_editor_parameters(self, editor_cod):
        """Read editor parameters."""

        # Note mar 2 lug 2024:
        # in jcom only 1 editor has keywords
        cursor_editor_parameters = self.connection.cursor(dictionary=True)
        query_editor_parameters = """
SELECT
ek.editorCod,
ek.keywordCod,
ek.keywordWeight,
kw.keywordName
FROM Editor_Keyword ek
LEFT JOIN Keyword kw USING (keywordCod)
WHERE editorCod=%(editor_cod)s
"""
        editor_parameters = cursor_editor_parameters.execute(query_editor_parameters, {"editor_cod": editor_cod})
        editor_parameters = cursor_editor_parameters.fetchall()
        cursor_editor_parameters.close()
        return editor_parameters

    def set_editor_parameters(self, editor_parameters, editor_maxworkload):
        """Set the editor parameters.

        - max-workload (StaffWorkloadParameters workload)
        - keyword      (EditorKeyword into StaffWorkloadParameters keywords)
        - kwd weight   (EditorKeyword weight)
        """

        if editor_parameters:
            assignment_parameters, eap_created = wjs_models.StaffWorkloadParameters.objects.get_or_create(
                user=self.get_current_editor(),
                journal=self.journal,
            )
        else:
            return

        if not editor_maxworkload:
            editor_maxworkload = 1
            logger.warning(
                f"{self.journal.code} ST: Missing editor max workload, forced to 1 "
                f"{self.article.id} / {self.preprintid}"
            )
            # import JHEP/JCAP ST exception not raised ValueError("Missing editor max workload")

        if editor_maxworkload == 9999:
            logger.warning(f"Workload of {editor_maxworkload} found. Verify WJS implementation of assignment funcs!")

        assignment_parameters.workload = editor_maxworkload
        assignment_parameters.save()

        # delete all existing editor kwds
        wjs_models.StaffKeyword.objects.filter(parameters=assignment_parameters).delete()

        # create all new editor kwds
        for ep in editor_parameters:
            kwd_word = normalize_keyword(self.journal.code, ep["keywordName"].strip())
            kwd_weight = ep["keywordWeight"]
            logger.debug(f"Editor parameter: {kwd_word} {kwd_weight}")
            keyword, created = submission_models.Keyword.objects.get_or_create(word=kwd_word)
            if created:
                logger.warning(f'Created keyword "{kwd_word}" for editor {self.get_current_editor()}. Please check!')
            wjs_models.StaffKeyword.objects.create(
                parameters=assignment_parameters,
                keyword=keyword,
                weight=kwd_weight,
            )

        return

    def deselect_editor_as_reviewer(self):
        """Deselects old editor from reviewer if exist."""

        # otherwise failes in case first submission not assigned to editor
        # i.e.: JCOM_001CR_0821
        current_editor_review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=self.get_current_editor(),
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()

        if current_editor_review_assignment:
            request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
            request.user = self.get_current_editor()

            # the automated message is disabled
            DeselectReviewer._log_operation = noop
            DeselectReviewer(
                assignment=current_editor_review_assignment,
                actor=current_editor_review_assignment.editor,
                request=request,
                send_reviewer_notification=False,
                form_data={
                    "notification_subject": "",
                    "notification_body": "",
                },
            ).run()
            current_editor_review_assignment.date_accepted = None
            current_editor_review_assignment.date_declined = None
            current_editor_review_assignment.save()


@dataclass
class SYS_ASS_ED(EditorAssignmentAction):  # noqa N801
    """Manages action "system assigns to editor"."""

    # this action in wjapp has two different meaning.
    # In the first version the agent is the author and the target is the editor which can be defined or not
    # in this case we want to add the author to all authors

    # In next versions the same action has been "reused" after a reset editor by admin action which is done by
    # an administrator and generates a new version copying the same files.
    # In this case the agent is the admin and the target is null, e.g. JCOM_010A_1123
    # and we do not want to add the agent (admin) to all authors.

    def __post_init__(self):
        """Enables attribute to import files for this action"""
        self.action_triggers_import_files = True

        if self.imported_version_num == 1:
            author = account_get_or_create_check_correspondence(
                self.journal.code.lower(),
                self.action["agentCod"],
                self.action["agentLastname"],
                self.action["agentFirstname"],
                self.action["agentEmail"],
                self.action["agentPrivacy"],
                self.connection,
            )

            # The action SYS_ASS_ED in wjapp "version 1" happens when the author submits the contribution.
            # It has always the agentCod (the author) also if sometimes the target can be null (editor not found).
            #
            # This is the first point where can be added to the authors list the author of the first submission
            # of the contribution (version 1) when the agent is not the corresponding author at
            # the moment of the import.
            #
            # The problem has to be managed only for the author of the version 1 because the other coauthors are
            # added with the actions SelectCoauthorAction which are already managed
            #
            # example of SYS_ASS_ED used after reset editor but not in version 1
            # where the agent is not an author but an eo in charge and then must not
            # be added to all authors: JCOM_028A_1024

            if author != self.article.correspondence_author:
                author.snapshot_as_author(self.article)
                if not author.check_role(self.journal, "author", staff_override=False):
                    author.add_account_role("author", self.journal)

            with freezegun.freeze_time(
                self.article.date_submitted,
            ):
                request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
                request.user = author
                context = {
                    "article": self.article,
                    "request": request,
                }
                message_subject = render_template_from_setting(
                    setting_group_name="email_subject",
                    setting_name="subject_submission_acknowledgement",
                    journal=self.journal,
                    request=request,
                    context=context,
                    template_is_setting=True,
                )
                communication_utils.log_operation(
                    article=self.article,
                    message_subject=message_subject,
                    message_body="",
                    recipients=[author],
                    flag_as_read=True,
                    flag_as_read_by_eo=True,
                )

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for SYS_ASS_ED if exists in wjs"""

        self.check_and_fix_action_reminder("New_submission", "EDSR", 3)


class ED_SEL_N_ED(EditorAssignmentAction):  # noqa N801
    """Manages action "editor selects new editor"."""

    def run_business_logic(self):
        """Editor selects new editor."""

        self.deselect_editor_as_reviewer()

        # e.g. JCOM_010A_1123
        SupervisorChangeEditorAssignment._log_past_editor = noop
        AssignToEditor._log_operation = noop
        SupervisorChangeEditorAssignment(
            article=self.article,
            assignment=WjsEditorAssignment.objects.filter(article=self.article).latest(),
            new_editor=self.editor_to_assign,
            request=self.ed_assign_request,
        ).run()
        self.article.save()
        self.check_and_fix_all_reminder()

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminder for ED_SEL_N_ED if exists in wjs"""

        self.check_and_fix_action_reminder("ed_selected_by_ed", "EDSR", 3)


class ADMIN_ASS_N_ED(EditorAssignmentAction):  # noqa N801
    """Manages action "admin assigns new editor"."""

    def run_business_logic(self):
        """Admin selects new editor."""

        # TODO: problem with paper JCOM_2205_2023_A01 JCOM_005A_0523

        # TBV: problem with JCOM_008A_0125 reset editor after decision
        #       possible solution: create new review round
        # code taken from:
        # wjs/plugins/wjs_review/events/handlers.py -> restart_review_process_after_revision_submission
        #
        # Note:
        # the reset ed ADMIN_RESETS_ED happens in wjapp JCOM_008A_0125/1 history,
        # but the current action ADMIN_ASS_N_ED happens in JCOM_008A_0125/2 history.
        # In the normal paper import ADMIN_ASS_N_ED does not create in wjs a new version,
        # but for this case, decision already taken, it is necessary to create a new review
        # round before SupervisorChangeEditorAssignment

        special_cases = {
            ("JCOM_008A_0125", 2, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
            ("JCOM_004N_1021", 2, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
            ("JCAP_003P_1104", 2, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
        }
        if (self.preprintid, self.imported_version_num, self.article.articleworkflow.state) in special_cases:

            logger.warning(
                f"fix of broken wjapp data state not compatible with ADMIN_ASS_N_ED: {self.preprintid} "
                f"AW.state: {self.article.articleworkflow.state}"
            )

            # Solved adding a AuthorHandleRevision, so a new review round is created following the logic.
            # After the action the files are reimported, so wjs version 1 and 2 are equal.
            # A problem can be that in the paper results a major revision submission not existing
            # in wjapp, but it is like a "confirm version". The editorial office can add a note for this paper.
            request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
            request.user = self.article.correspondence_author

            revision_request = EditorRevisionRequest.objects.get(article=self.article)
            form_data = {"author_note": revision_request.author_note}
            service = AuthorHandleRevisionObsolete(
                revision=revision_request,
                form_data=form_data,
                user=self.article.correspondence_author,
                request=request,
            )
            service.run()

            # do again import_files because there is a new review round
            if self.importfiles:
                import_file_manager.ImportFileManager(self).import_version_files()

        if not WjsEditorAssignment.objects.filter(article=self.article).exists():
            # i.e. JCOM_005A_0523 editor decline
            # i.e: JCOM_001CR_0821: case admin chooses editor after first submission without editor assingment

            # Manually move into a state where editor assignment can take place
            if self.article.articleworkflow.state != ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED:
                logger.warning(
                    f"Changing article {self.article.id}/{self.preprintid} state "
                    f"from {self.article.articleworkflow.state} to "
                    f"{ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED} during ADMIN_ASS_N_ED. Please check"
                )
                self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
            self.article.articleworkflow.save()

            AssignToEditor(
                article=self.article,
                editor=self.editor_to_assign,
                request=self.ed_assign_request,
            ).run()
            self.article.save()
            self.check_and_fix_all_reminder()
            logger.warning(
                f"{self.preprintid} {self.action['actionID']} admin assigns to "
                f"new editor {self.editor_to_assign} after the first submission without editor assignment "
                "or after editor declines",
            )

        else:
            # normal case, admin assigns to a new editor and exists an WjsEditorAssignment

            # special case: co-author chosen as editor
            special_cases = {
                ("JCOM_005C_0819", 2, 272888),
                ("JCOM_006C_0819", 2, 272894),
            }
            if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in special_cases:
                logger.warning(
                    f"fix for {self.preprintid}/{self.imported_version_num} "
                    f" removed author {self.editor_to_assign} (coauthor as editor known case)"
                )
                FrozenAuthor.objects.filter(article=self.article, author=self.editor_to_assign).delete()

            self.deselect_editor_as_reviewer()

            # e.g. JCOM_010A_1123
            SupervisorChangeEditorAssignment._log_past_editor = noop
            AssignToEditor._log_operation = noop
            SupervisorChangeEditorAssignment(
                article=self.article,
                assignment=WjsEditorAssignment.objects.filter(article=self.article).latest(),
                new_editor=self.editor_to_assign,
                request=self.ed_assign_request,
            ).run()
            self.article.save()
            self.check_and_fix_all_reminder()

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminder for ADMIN_ASS_N_ED if exists in wjs"""

        self.check_and_fix_action_reminder("ed_selected_by_EO", "EDSR", 3)


class AdminOpensAppealAction(BaseActionManager):  # noqa N801
    """Admin opens appeal: wjapp action "admin accepts appeal"."""

    def run(self):
        """Admin opens appeal."""
        self.check_editor_set()

        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_appeal_revision_days",
            self.journal,
        ).process_value()

        # we need to get the admin user who did the action and the open-appeal action is done by this user
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]
        admin_opens_appeal_date = self.action["actionDate"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = admin

        with freezegun.freeze_time(
            rome_timezone.localize(admin_opens_appeal_date),
        ):
            logger.debug(f"Admin {admin} opens appeal {self.article.pk}")

            # the related message is imported with the general correspondence
            # the automated message is disabled
            OpenAppeal._log_author = noop
            # check_past_rejection=False: WJS now forbids opening a further appeal once a paper
            # has already been rejected more than once (see OpenAppeal.check_past_rejection), but
            # wjapp's older rules allowed it - e.g. appealing again to the same editor who
            # rejected the paper. We are replaying a wjapp action that already happened under
            # those older rules, so the current, stricter policy must not be applied retroactively.
            OpenAppeal(
                new_editor=self.get_current_editor(),
                article=self.article,
                request=request,
                check_past_rejection=False,
            ).run()


class ADMIN_ACC_APP(AdminOpensAppealAction):  # noqa N801
    """Manages wjapp action "admin accepts appeal"."""


class ADMIN_ACC_APP_NEW_ED(AdminOpensAppealAction):  # noqa N801
    """Manages wjapp action "admin accepts appeal with new editor"."""


class AU_WITHD_DOC(BaseActionManager):  # noqa N801
    """Author withdraws paper: wjapp action "author withdraws document"."""

    def run(self):
        # Author withdraws paper

        noop_cases = {
            ("JCOM_006A_0623", 1, 291617),
            ("JCOM_004N_0923", 2, 295231),
            ("JCOM_016A_0924", 1, 299629),
            ("JCOM_003N_1224", 1, 300878),
            ("JCOM_003N_1224", 1, 300879),
            ("JCOMAL_001R_1223", 1, 4664),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} AU_WITHD_DOC "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        self.fix_pre_withdrawn_state()
        self.author_withdrawn(self.read_author_withdrawn_message())

    def author_withdrawn(self, author_withdrawn_message):
        """Author withdraws preprint."""

        author_cod = self.action["agentCod"]
        author_lastname = self.action["agentLastname"]
        author_firstname = self.action["agentFirstname"]
        author_email = self.action["agentEmail"]
        author_privacy = self.action["agentPrivacy"]
        author_withdrawn_date = self.action["actionDate"]

        # note: the "agentEmail" could be different from correspondence_author.email
        # because in general the agentEmail could be present in Correspondence but not
        # in the user account
        author = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            author_cod,
            author_lastname,
            author_firstname,
            author_email,
            author_privacy,
            self.connection,
        )

        # account_get_or_create_check_correspondence returns the cached value for the corr auth.
        # the assert assures that the wjapp data are consistent
        assert author.id == self.article.correspondence_author.id

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = author

        with freezegun.freeze_time(
            rome_timezone.localize(author_withdrawn_date),
        ):
            logger.debug(f"Author {author} withdraws {self.article}")

            # it is saved in TextField therefore not converted to html
            withdrawn_subject = author_withdrawn_message.get("documentLayerSubject")
            withdrawn_message = newlines_text_to_html(author_withdrawn_message.get("documentLayerText"))

            self.imported_document_layer_cod_list.append(author_withdrawn_message.get("documentLayerCod"))

            # Press notification isn't configured for every journal (e.g. JCAP) - that's expected,
            # not an import bug, and imported (old) withdrawals must not send any email regardless.
            PressNotificationHandler.notification_journalpress = noop
            WithdrawPreprint(
                workflow=self.article.articleworkflow,
                request=request,
                form_data={
                    "notification_subject": withdrawn_subject,
                    "notification_body": withdrawn_message,
                },
            ).run()

    def read_author_withdrawn_message(self):
        """Read author withdraws message."""

        cursor_author_withdrawn_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (5") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        query_author_withdrawn_message = """
SELECT
dl.documentLayerSubject,
dl.documentLayerCod,
dl.documentLayerText,
dl.documentLayerType
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType IN ('ATOEE', 'TOADE')
AND ur.userType='author'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 11 SECOND)
ORDER BY dl.submissionDate
"""
        cursor_author_withdrawn_message.execute(
            query_author_withdrawn_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_author_withdrawn_message.rowcount != 1:

            noop_cases = {
                ("JCOM_006A_0623", 57586, 12533),
                ("JCOM_004N_0923", 58000, 12876),
                ("JCOM_016A_0924", 58626, 12188),
                ("JCOM_003N_1224", 58899, 13787),
                # duplicate ATOEE withdraw message resent 3 seconds later (documentLayerCod
                # 236546/236547), same subject/author list - action's own actionDate matches the
                # first message exactly.
                ("JCAP_083P_0618", 40591, 5768),
            }
            if (self.preprintid, self.imported_version_cod, self.action["agentCod"]) in noop_cases:
                logger.warning(
                    f"Skipped duplicated author withdrawn message for agent {self.action['agentCod']} "
                    f"and {self.preprintid} version cod: {self.imported_version_cod} (duplicated - known case)."
                )
                author_withdrawn_message = cursor_author_withdrawn_message.fetchone()
            elif self.preprintid == "JCAP_003P_1124" and self.imported_version_num == 2:
                author_withdrawn_message = cursor_author_withdrawn_message.fetchall()[-1]
                logger.debug(
                    f"Found 2 author withdrawn msg taken the last one {self.preprintid}/{self.imported_version_num}"
                    f" {author_withdrawn_message.get('documentLayerCod')}"
                )
            else:
                logger.error(
                    f"Found {cursor_author_withdrawn_message.rowcount} author withdrawn messages"
                    f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
                )
                author_withdrawn_message = None
                # added for JHEP/JCAP ST
                raise ValueError("Not found author withdrawn messages")
        else:
            author_withdrawn_message = cursor_author_withdrawn_message.fetchone()
            logger.debug(
                f"Found auth withdrawn msg doclayerType: {author_withdrawn_message.get('documentLayerType')} "
                f"{self.preprintid}/{self.imported_version_num}"
            )
        cursor_author_withdrawn_message.close()
        return author_withdrawn_message


class AdminWithdrawn(BaseActionManager):  # noqa N801
    """Admin withdraws paper."""

    def run(self):
        # Admin withdraws paper e.g. JCOM_005A_0224

        self.fix_pre_withdrawn_state()

        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]
        admin_withdrawn_date = self.action["actionDate"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)

        # admin can not withdrawn in wjs
        request.user = self.article.correspondence_author

        with freezegun.freeze_time(
            rome_timezone.localize(admin_withdrawn_date),
        ):
            logger.debug(f"Admin {admin} withdraws {self.article}")

            # message imported as correspondence because this action is executed by the author, not the admin
            WithdrawPreprint._log_supervisor = noop
            WithdrawPreprint._log_typesetter = noop
            DeselectReviewer._log_operation = noop
            # Press notification isn't configured for every journal (e.g. JCAP) - that's expected,
            # not an import bug, and imported (old) withdrawals must not send any email regardless.
            PressNotificationHandler.notification_journalpress = noop
            WithdrawPreprint(
                workflow=self.article.articleworkflow,
                request=request,
                form_data={
                    "notification_subject": "NOT IMPORTED",
                    "notification_body": "NOT IMPORTED",
                },
            ).run()

            communication_utils.log_operation(
                article=self.article,
                message_subject="Withdrawn",
                message_body="",
                actor=admin,
                recipients=[admin],
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )


class ADMIN_WITHD_DOC(AdminWithdrawn):  # noqa N801
    """Admin withdraws paper: wjapp action "admin withdraws document"."""


class ADMIN_WITHR_APP(AdminWithdrawn):  # noqa N801
    """Admin withdraws appeal: wjapp action "admin withdraw appeal"."""


class ED_REF_DOC(BaseActionManager):  # noqa N801
    """Editor declines assignment: wjapp action "editor refuses document"."""

    def run(self):
        # Editor declines assignment

        # Known cases of "dirty data" on wjapp, when this action had no effect.
        # We identify them by a triplet made of preprint id, version number and action-history code
        # (the first two are just human-friendly pointers 🙂).
        # related to a correction of double editor decline message
        noop_cases = {
            ("JCOM_009A_0123", 1, 290536),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ED_REF_DOC "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        self.check_editor_set()
        self.editor_declines(self.read_editor_decline_message())

    def editor_declines(self, editor_decline_message):
        """Editor declines."""

        editor_declines_date = self.action["actionDate"]
        editor = self.get_current_editor()

        # i.e. JCOM_012A_0920, a direct assert equal on agentLastname == editor.last_name
        # "Reynoso-Haynes" is different from "Reynoso Haynes"
        # (same person) is too strict it is necessary to check the accounts
        agent_cod = self.action["agentCod"]
        agent_lastname = self.action["agentLastname"]
        agent_firstname = self.action["agentFirstname"]
        agent_email = self.action["agentEmail"]
        agent_privacy = self.action["agentPrivacy"]

        agent = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            agent_cod,
            agent_lastname,
            agent_firstname,
            agent_email,
            agent_privacy,
            self.connection,
        )
        assert agent.id == editor.id

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = editor

        with freezegun.freeze_time(
            rome_timezone.localize(editor_declines_date),
        ):
            logger.debug(f"Editor {editor} declines {self.article.pk}")

            # it is saved in TextField therefore not converted to html
            decline_message = editor_decline_message.get("documentLayerText")

            self.imported_document_layer_cod_list.append(editor_decline_message.get("documentLayerCod"))

            HandleEditorDeclinesAssignment(
                assignment=WjsEditorAssignment.objects.get_all(self.article.articleworkflow).get(editor=editor),
                editor=editor,
                request=request,
                form_data={
                    "decline_reason": PastEditorAssignment.DeclineReasons.BUSY.label,
                    "decline_text": decline_message,
                },
            ).run()

    def read_editor_decline_message(self):
        """Read Editor decline assignment message."""

        cursor_editor_decline_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (15") respect to the action_date

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        # wrong message data in source database
        wrong_editor_decline_messages = {
            ("JCAP_033P_0622", 57747, 369): {
                "documentLayerCod": 349064,
                "documentLayerId": "JCAP_033P_0622_TOADE006280622",
                "documentLayerText": " --- ",
            },
            ("JCAP_019P_0822", 58387, 1913): {
                "documentLayerCod": 353596,
                "documentLayerId": "JCAP_019P_0822_TOADE003980822",
                "documentLayerText": " --- ",
            },
        }

        key = (self.preprintid, self.imported_version_cod, self.action["agentCod"])
        if editor_decline_message := wrong_editor_decline_messages.get(key):
            logger.debug(
                f"wrong source editor decline message (same data of previous editor decline message) "
                f"text cleaned: {self.preprintid}/{self.imported_version_num}"
            )
            return editor_decline_message

        query_editor_decline_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerId,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='TOADE'
AND ur.userType='author'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 19 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_008A_0622"
            and self.imported_version_cod == 56883
            and self.action["agentCod"] == 8502
        ):
            action_date = "20220623140311"
            logger.debug(f"fixed editor decline msg timing {self.preprintid}")

        cursor_editor_decline_message.execute(
            query_editor_decline_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
            },
        )
        if cursor_editor_decline_message.rowcount == 1:
            editor_decline_message = cursor_editor_decline_message.fetchone()
        else:
            noop_cases = {
                ("JCOM_009A_0123", 57327, 8475),
            }
            if (self.preprintid, self.imported_version_cod, self.action["agentCod"]) in noop_cases:
                logger.warning(
                    f"Skipped duplicated editor decline message for agent {self.action['agentCod']} "
                    f"and {self.preprintid} version cod: {self.imported_version_cod} (duplicated - known case)."
                )
                editor_decline_message = cursor_editor_decline_message.fetchone()

            elif cursor_editor_decline_message.rowcount > 1:
                editor_decline_message = cursor_editor_decline_message.fetchone()
                logger.warning(
                    f"Found closer in time of {cursor_editor_decline_message.rowcount} editor decline messages"
                    f" {self.preprintid}/{self.imported_version_num} {editor_decline_message['documentLayerId']}"
                    f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
                )
            else:
                logger.error(
                    f"Found {cursor_editor_decline_message.rowcount} editor decline messages"
                    f" {self.preprintid}/{self.imported_version_num}"
                    f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
                )
                editor_decline_message = None
                # added for JHEP/JCAP ST
                raise ValueError("Missing editor decline message")

        cursor_editor_decline_message.close()
        return editor_decline_message


class ED_ACT_AS_REF(BaseActionManager):  # noqa N801
    """Editor assigns her/him self as reviewer "editor acts as referee"."""

    def run(self):

        # note:
        # - JCOM_009A_0123/2
        #   has two immediately successive actions: i will review
        #   - 08 May 2023 07:31 Editor Elaine Reynoso-Haynes will review JCOM_009A_0123 by him/herself.
        #   - 08 May 2023 07:26 Editor Elaine Reynoso-Haynes will review JCOM_009A_0123 by him/herself.
        #   also if this is not blocked by transition conditions, the first action is skipped because would
        #   cause the first RA to be visible and "deselected"
        noop_cases = {
            ("JCOM_002Y_0216", 1, 261767),  # assigned to ed and ed did I-will-review, but ed is also author
            ("JCOM_001E_0318", 1, 267325),  # assigned to ed and ed did I-will-review, but ed is also author
            ("JCOM_001E_0915", 1, 260742),  # assigned to ed and ed did I-will-review, but ed is also author
            ("JCOM_009A_0123", 2, 291251),
            ("JCOMAL_001A_1018", 2, 10),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ED_ACT_AS_REF for {self.preprintid}/"
                f"{self.imported_version_num} (impossible action edt eq auth/repeated action - known case)."
            )
            return

        self.check_editor_set()
        editor = reviewer = self.get_current_editor()

        # in wjapp there are cases of ED_ACT_AS_REF for the same reviewer
        # without the reviewer being removed in between (e.g. JCAP_003P_1213/4)
        if WorkflowReviewAssignment.objects.filter(
            article=self.article,
            reviewer=editor,
            is_complete=False,
        ).exists():
            logger.warning(
                f"Skip because already existing editor review assignment of {self.article.id} to reviewer {editor.id}"
                f" {editor.full_name()} {editor.email}"
            )
            return

        logger.debug(f"Creating review assignment of {self.article.id} to editor as reviewer")

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = editor

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            # default message from settings
            # TODO: verify message sent by the logic

            # wjapp does not record a due-date, so we set a fictitious date that simulates what wjs would do
            # using freeze_time now() is refereeAssignDate
            date_due = timezone.localtime(timezone.now()).date() + datetime.timedelta(days=21)

            form_data = {
                "acceptance_due_date": date_due,
                "message": "VALUE NOT USED",
            }
            # the automated message is not disabled otherwise the action does not appear in the timeline
            review_assignment = AssignToReviewer(
                reviewer=reviewer,
                workflow=self.article.articleworkflow,
                editor=editor,
                form_data=form_data,
                request=request,
            ).run()

        self.check_and_fix_all_reminder()
        return review_assignment

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for ED_ACT_AS_REF if exists in wjs"""

        # Note wjs logic adds only reminder REWR1 and REWR2 when the reviewer is the editor
        self.check_and_fix_action_reminder("i_will_review", "REWR", 2)


class ReviewAssignmentAction(BaseActionManager):
    """Review assignment management.

    All actions of this class map (roughly) to ReviewAssignment.
    Review assignments are created onto the current review round; see external loop on versions.
    """

    def run(self):

        noop_cases = {
            ("JCOM_007A_0321", 1, 281937),
            ("JCOMAL_005A_0622", 1, 2990),
            ("JCOMAL_003A_0822", 3, 3121),
            ("JCOMAL_001A_1022", 1, 3175),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ReviewAssignmentAction "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated and wrong logic - known case)."
            )
            return

        self.check_editor_set()

        # reviewer data from Current_Referees
        reviewer_data = self.read_reviewer_data()

        # Reviewer not in Current_Referees - for example a removed referee
        #
        # The Action_History contains some data of the referee-related actions:
        # - referee assignment
        # - referee acceptance
        # - referee removal
        # - ..
        #
        # Current_Referees contains all the data of the (current) referees assignments (it is the
        # closest thing to Janeway's ReviewAssignment). But, if a referee has been "removed", the
        # relative assignment data is lost (we have a note about it only in Action_History).
        #
        # In wjapp, only referees that have not done any report can be removed.
        #
        # The import process loops on the action_history and executes all the actions version by
        # version.  When a referee assignment data is found also in Current_Referees, it is checked to
        # extract data (the fact that the action exists, means that the referee has not been removed),
        # otherwise remain only the action data.

        if not reviewer_data:
            reviewer_data = {
                "refereeCod": self.action["targetCod"],
                "refereeLastName": self.action["targetLastname"],
                "refereeFirstName": self.action["targetFirstname"],
                "refereeEmail": self.action["targetEmail"],
                "refereePrivacy": self.action["targetPrivacy"],
                "refereeAssignDate": self.action["actionDate"],
                "report_due_date": None,
                "refereeAcceptDate": None,
                "reminderNumber": None,
                "refereeReminderDate": None,
                "refereeReminderEnabled": None,
                "refereeReminder2Date": None,
                "refereeReminder2Enabled": None,
                "editorReminderDate": None,
                "editorReminderEnabled": None,
                "refereeReminder1ForRefereeReportDate": None,
                "refereeReminder1ForRefereeReportEnabled": None,
                "refereeReminder2ForRefereeReportDate": None,
                "refereeReminder2ForRefereeReportEnabled": None,
                "editorReminderForRefereeReportEnabled": None,
                "editorReminderForRefereeReportDate": None,
            }

        # select reviewer message

        special_cases = {("JCOM_010A_0615", 1, 260355)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in special_cases:
            # fake reviewer message. Not existing documentLayerCod = 0 does not create problems when
            # inserted in imported_document_layer_cod_list at the end of the action.
            reviewer_message = {
                "documentLayerCod": 0,
                "documentLayerText": "",
            }
            logger.warning(
                f"Fix for missing reviewer msg {self.action['actHistCod']}  "
                f"for {self.preprintid}/{self.imported_version_num} (wjapp data missing - known case)."
            )
        else:
            reviewer_message = self.read_reviewer_message()
        logger.debug(f"Reviewer message: {reviewer_message.get('documentLayerCod')}")

        self.set_reviewer(reviewer_data, reviewer_message)

    def read_reviewer_data(self):
        """Read reviewer data."""

        cursor_reviewer = self.connection.cursor(dictionary=True)
        query_reviewer = """
SELECT
refereeCod,
reminderNumber,
refereeReminderDate,
refereeReminderEnabled,
refereeReminder2Date,
refereeReminder2Enabled,
editorReminderDate,
editorReminderEnabled,
refereeReminder1ForRefereeReportDate,
refereeReminder1ForRefereeReportEnabled,
refereeReminder2ForRefereeReportDate,
refereeReminder2ForRefereeReportEnabled,
editorReminderForRefereeReportEnabled,
editorReminderForRefereeReportDate,
u.lastName  AS refereeLastName,
u.firstName AS refereeFirstName,
u.email     AS refereeEmail,
u.privacy   AS refereePrivacy,
assignDate  AS refereeAssignDate,
refereeReportDeadlineDate AS report_due_date,
acceptDate AS refereeAcceptDate
FROM Current_Referees c
LEFT JOIN User u ON (u.userCod=c.refereeCod)
WHERE
        versioncod=%(imported_version_cod)s
    AND refereeCod=%(user_cod)s
ORDER BY assignDate
"""
        cursor_reviewer.execute(
            query_reviewer,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
            },
        )
        reviewer_data = cursor_reviewer.fetchone()
        cursor_reviewer.close()
        return reviewer_data

    def read_reviewer_message(self):
        """Read the message that is sent to the reviewer when he is assigned to a paper."""

        cursor_reviewer_message = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range respect to the action_date
        # time range fixed to 42 (e.g. JCAP_031P_0315)"

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        query_reviewer_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerId,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(user_cod)s
AND dl.documentLayerType='EMAIL'
AND ur.userType='recipient'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 42 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_004A_1016"
            and self.imported_version_cod == 53390
            and self.action["targetCod"] == 8558
        ):
            action_date = "20161017151904"
            logger.debug(f"fixed reviewer assignment msg timing {self.preprintid}")

        cursor_reviewer_message.execute(
            query_reviewer_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
                "action_date": action_date,
            },
        )
        if cursor_reviewer_message.rowcount == 1:
            reviewer_message = cursor_reviewer_message.fetchone()
            logger.debug(
                f"Found 1 reviewer assignment message: {self.preprintid}/{self.imported_version_num}"
                f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
            )
        elif cursor_reviewer_message.rowcount > 1:
            # the query is oredered by submissionDate
            # so is taken the closer in time to the action date
            reviewer_message = cursor_reviewer_message.fetchone()
            logger.warning(
                f"Found closer in time of {cursor_reviewer_message.rowcount} reviewer assignment messages"
                f" {self.preprintid}/{self.imported_version_num} {reviewer_message['documentLayerId']}"
                f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
            )
        elif cursor_reviewer_message.rowcount == 0:
            logger.warning(
                f"Found 0 reviewer assignment messages, fallback empty message for: {self.preprintid}/"
                f"{self.imported_version_num} with agent {self.action['agentCod']} "
                f"and date {self.action['actionDate']}"
            )
            # fallback, signaled with a warning, in some cases e.g. JCAP_127P_0725
            # the message does not exists and is replaced by a direct message to the reviewer
            # which is in any case loaded as correspondence
            reviewer_message = {
                "documentLayerCod": 0,
                "documentLayerText": "",
            }

        cursor_reviewer_message.close()
        return reviewer_message

    def set_reviewer(self, reviewer_data, reviewer_message):
        """Set a reviewer."""

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_data["refereeCod"],
            reviewer_data["refereeLastName"],
            reviewer_data["refereeFirstName"],
            reviewer_data["refereeEmail"],
            reviewer_data["refereePrivacy"],
            self.connection,
        )

        # in wjapp there are cases of review assignment for the same reviewer (actions ED_ASS_REF...ED_ADD_REF)
        # without the reviewer being removed in between (e.g. JCAP_008P_0410/1, JCAP_010P_0819/2)
        if WorkflowReviewAssignment.objects.filter(
            article=self.article,
            reviewer=reviewer,
            is_complete=False,
        ).exists():
            logger.warning(
                f"Skip because already existing review assignment of {self.article.id} to reviewer {reviewer.id}"
                f" {reviewer.full_name()} {reviewer.email}"
            )
            return

        logger.debug(
            f"Creating review assignment of {self.article.id} to reviewer {reviewer.id}"
            f" {reviewer.full_name()} {reviewer.email}"
        )

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.get_current_editor()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_data["refereeAssignDate"]),
        ):
            # default message from settings
            # TODO: verify mail subject exists
            # TODO: verify signature in the final message request.user.signature is not missing

            interval_days = get_setting(
                "wjs_review",
                "default_review_acceptance_days",
                self.journal,
            )
            # wjapp does not record a due-date, so we set a fictitious date that simulates what wjs would do
            # using freeze_time now() is refereeAssignDate
            date_due = timezone.localtime(timezone.now()).date() + datetime.timedelta(
                days=interval_days.process_value()
            )

            if reviewer_message:
                message = newlines_text_to_html(reviewer_message.get("documentLayerText"))
                self.imported_document_layer_cod_list.append(reviewer_message.get("documentLayerCod"))
                logger.debug(f"append reviewer message: {reviewer_message.get('documentLayerCod')}")

            else:
                message = render_template_from_setting(
                    setting_group_name="email",
                    setting_name="review_assignment",
                    journal=self.journal,
                    request=request,
                    context={
                        "article": self.article,
                        "reviewer": reviewer,
                        "request": request,
                    },
                    template_is_setting=True,
                )
                logger.warning(
                    f"used default reviewer message {reviewer=} {self.article=} {self.get_current_editor()=}"
                )

            form_data = {
                "acceptance_due_date": date_due,
                "message": message,
            }

            # check_reviewer_author=False: some old wjapp papers actually have an author
            # assigned as reviewer, and we must replay that history as-is.
            review_assignment = AssignToReviewer(
                reviewer=reviewer,
                workflow=self.article.articleworkflow,
                editor=self.get_current_editor(),
                form_data=form_data,
                request=request,
                check_reviewer_author=False,
            ).run()

            # refereeAcceptDate = 1970-01-02 01:00:00 from wjapp means "refereeAcceptDate not set"
            if reviewer_data["refereeAcceptDate"] and reviewer_data["refereeAcceptDate"].year != 1970:
                request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
                request.user = reviewer

                with freezegun.freeze_time(
                    rome_timezone.localize(reviewer_data["refereeAcceptDate"]),
                ):
                    # the automated message is disabled
                    EvaluateReview._log_accept = noop
                    EvaluateReview(
                        assignment=review_assignment,
                        reviewer=reviewer,
                        editor=self.get_current_editor(),
                        form_data={
                            "reviewer_decision": "1",
                            "additional_comments": "",
                            "accept_gdpr": True,
                            "date_due": timezone.localtime(timezone.now()).date() + datetime.timedelta(days=21),
                        },
                        request=request,
                        token=None,
                    ).run()
                    if reviewer_data["report_due_date"]:
                        datetime_due = rome_timezone.localize(
                            reviewer_data["report_due_date"],
                        )
                        # note: review_assignment date_due is datetime.date not datetime.datetime
                        review_assignment.date_due = datetime_due.date()
                        review_assignment.save()

        # TODO: add disable and sent flags to reviewer and editor reminder taken
        # from reviewer_data (add fields)
        self.check_and_fix_reviewer_editor_reminder(review_assignment, reviewer_data)

        return review_assignment

    def check_and_fix_reviewer_editor_reminder(self, review_assignment, reviewer_data):
        """Check and fix sent and disable reminder for review assignment."""

        # data taken from Current_Referees
        #
        # This kind of reminder related to review assignment in wjapp is managed with Current_Referee table only,
        # not with Reminder table like the other reminders. E.g: JCOM_013A_1124
        ra_reminders = Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.id,
        )
        for r in ra_reminders:
            # from wjapp properties

            next_day_delay = 1

            # REEA
            ref_acc_limit_days = 4
            ref_acc_limit2_days = 2
            ref_acc_limit_editor_reminder_days = 3

            # REWR2
            referee_report_reminder_for_editor_days = 4

            if r.recipient == review_assignment.reviewer and r.code == "REEA1":
                # REEA1 "Reviewer should evaluate assignment" -> refereeReminderEnabled/Date
                if not reviewer_data["refereeReminderEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["refereeReminderDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["refereeReminderDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["refereeReminderDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = (
                    rome_timezone.localize(reviewer_data["refereeAssignDate"])
                    + datetime.timedelta(days=next_day_delay)
                    + datetime.timedelta(days=ref_acc_limit_days)
                ).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()
                review_assignment.date_due = r.date_due
                review_assignment.save()
                logger.debug(
                    f"{r.code} ({r.id}) and RA {review_assignment.id} (article {review_assignment.article.id})"
                    f" due_date change {r.date_due} for {r.recipient}"
                )

            if r.recipient == review_assignment.reviewer and r.code == "REEA2":
                # REEA2 "Reviewer should evaluate assignment" -> refereeReminder2Enabled/Date
                if not reviewer_data["refereeReminder2Enabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["refereeReminder2Date"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["refereeReminder2Date"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["refereeReminder2Date"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = (
                    rome_timezone.localize(reviewer_data["refereeAssignDate"])
                    + datetime.timedelta(days=next_day_delay)
                    + datetime.timedelta(days=ref_acc_limit_days)
                    + datetime.timedelta(days=ref_acc_limit2_days)
                ).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()

            if r.recipient == review_assignment.editor and r.code == "REEA3":
                # REEA3 "Reviewer should evaluate assignment" > editorReminderEnabled/Date
                if not reviewer_data["editorReminderEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["editorReminderDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["editorReminderDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["editorReminderDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                r.date_due = (
                    rome_timezone.localize(reviewer_data["refereeAssignDate"])
                    + datetime.timedelta(days=next_day_delay)
                    + datetime.timedelta(days=ref_acc_limit_days)
                    + datetime.timedelta(days=ref_acc_limit2_days)
                    + datetime.timedelta(days=ref_acc_limit_editor_reminder_days)
                ).date()
                logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                r.save()

            if r.recipient == review_assignment.reviewer and r.code == "REWR1":
                # REWR1 "Reviewer should write review" -> refereeReminder1ForRefereeReportEnabled
                # e.g. JCOM_002N_0125 JCOM_002N_1224 JCOM_009A_0325
                if not reviewer_data["refereeReminder1ForRefereeReportEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["refereeReminder1ForRefereeReportDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["refereeReminder1ForRefereeReportDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["refereeReminder1ForRefereeReportDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                # Note: for JHEP/JCAP import stress test
                if self.journal.code in ["JHEP", "JCAP"]:
                    if reviewer_data["report_due_date"]:
                        r.date_due = rome_timezone.localize(reviewer_data["report_due_date"]).date()
                        logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                        r.save()
                else:
                    r.date_due = rome_timezone.localize(reviewer_data["report_due_date"]).date()
                    logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                    r.save()

            if r.recipient == review_assignment.editor and r.code == "REWR2":
                # REWR2 "Reviewer should write review" -> editorReminderForRefereeReportDate
                if not reviewer_data["editorReminderForRefereeReportEnabled"]:
                    r.disable = True
                    r.save()
                    logger.debug(f"reminder {r} modified disable:{r.disable}")

                if reviewer_data["editorReminderForRefereeReportDate"] and r.date_sent != rome_timezone.localize(
                    reviewer_data["editorReminderForRefereeReportDate"]
                ):
                    r.date_sent = rome_timezone.localize(reviewer_data["editorReminderForRefereeReportDate"])
                    r.save()
                    logger.debug(f"reminder {r} modified date_sent:{r.date_sent}")

                # Note: for JHEP/JCAP import stress test
                if self.journal.code in ["JHEP", "JCAP"]:
                    if reviewer_data["report_due_date"]:
                        r.date_due = (
                            rome_timezone.localize(reviewer_data["report_due_date"])
                            + datetime.timedelta(days=referee_report_reminder_for_editor_days)
                        ).date()
                        logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                        r.save()
                else:
                    r.date_due = (
                        rome_timezone.localize(reviewer_data["report_due_date"])
                        + datetime.timedelta(days=referee_report_reminder_for_editor_days)
                    ).date()
                    logger.debug(f"due_date change {r.code} {r.date_due} to {r.recipient}")
                    r.save()


class ED_ASS_REF(ReviewAssignmentAction):  # noqa N801
    """Manages wjapp action "editor assigns referee"."""


class ED_ADD_REF(ReviewAssignmentAction):  # noqa N801
    """Manages wjapp action "editor adds referee"."""


class DeselectReviewerAction(BaseActionManager):
    """Editor deselect reviewer."""

    def run(self):
        # wjapp actions for editor deselect reviewer (ex:JCOM_005N_0324)

        # EQ1_ED_REM_REF	[#referee=1]/editor removes referee (e.g. JCOM_007A_0724)
        # GT1_ED_REM_REF	[#referee>1]/editor removes referee (e.g. JCOM_013A_0724)
        # ED_REM_REF	editor removes referee (e.g. JCOM_018A_0724)

        noop_cases = {
            ("JCOM_002A_0323", 1, 291112),
            ("JCOM_002C_1216", 1, 263831),
            ("JCOM_010A_0615", 1, 260593),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} deselect reviewer "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        reviewer_cod = self.action["targetCod"]
        reviewer_lastname = self.action["targetLastname"]
        reviewer_firstname = self.action["targetFirstname"]
        reviewer_email = self.action["targetEmail"]
        reviewer_privacy = self.action["targetPrivacy"]
        reviewer_deselection_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
            reviewer_privacy,
            self.connection,
        )

        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.get_current_editor()

        deselect_reviewer_message = self.read_deselect_reviewer_message()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_deselection_date),
        ):
            # the automated message is disabled
            DeselectReviewer._log_operation = noop
            DeselectReviewer(
                assignment=review_assignment,
                actor=review_assignment.editor,
                request=request,
                send_reviewer_notification=False,
                form_data={
                    "notification_subject": deselect_reviewer_message.get("documentLayerSubject"),
                    "notification_body": deselect_reviewer_message.get("documentLayerText"),
                },
            ).run()

        self.check_and_fix_all_reminder()

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for DeselectReviewerAction if exists in wjs"""

        self.check_and_fix_action_reminder("ed_removed_ed_decision", "EDMD", 3)

    def read_deselect_reviewer_message(self):
        """Read the message that is sent to the reviewer when he is deselected."""

        cursor_deselect_reviewer_message = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range (60") respect to the action_date
        # e.g. 58" delay to match JCAP_058P_0416

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported_version_cod ensures to retrive the correct article

        query_deselect_reviewer_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(user_cod)s
AND dl.documentLayerType='EMAIL'
AND ur.userType='recipient'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 58 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced like the
        # documentlayer date. The option to enlarge more the interval in the query risks to bring
        # errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_009A_0718"
            and self.imported_version_cod == 54251
            and self.action["targetCod"] == 9905
        ):
            action_date = "20180831095428"
            logger.debug(f"fixed deselect reviewer msg 6 seconds after action date {self.preprintid}")

        if (
            self.preprintid == "JCOM_005A_0515"
            and self.imported_version_cod == 52998
            and self.action["targetCod"] == 8640
        ):
            action_date = "20151210115632"
            logger.debug(f"fixed deselect reviewer msg 24 seconds after action date {self.preprintid}")

        cursor_deselect_reviewer_message.execute(
            query_deselect_reviewer_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "user_cod": self.action["targetCod"],
                "action_date": action_date,
            },
        )

        if cursor_deselect_reviewer_message.rowcount == 1:
            deselect_reviewer_message = cursor_deselect_reviewer_message.fetchone()
            logger.debug(f"Found 1 deselect reviewer message: {self.preprintid}" f"/{self.imported_version_cod}")
        elif cursor_deselect_reviewer_message.rowcount > 1:
            noop_cases = {
                ("JCOM_002C_1216", 53455, 8528),
            }
            if (self.preprintid, self.imported_version_cod, self.action["targetCod"]) in noop_cases:
                logger.warning(
                    f"Skipped duplicated deselect reviewer message for agent {self.action['targetCod']} "
                    f"and {self.preprintid} version cod: {self.imported_version_cod} (duplicated - known case)."
                )
                deselect_reviewer_message = cursor_deselect_reviewer_message.fetchone()
            else:
                logger.warning(
                    f"Found {cursor_deselect_reviewer_message.rowcount} deselect reviewer messages taken the most"
                    f" recent one: {self.preprintid}/{self.imported_version_cod}"
                )
                deselect_reviewer_message = cursor_deselect_reviewer_message.fetchone()
        else:
            # added for JHEP/JCAP ST
            raise ValueError("Not found deselect reviewer message")

        cursor_deselect_reviewer_message.close()

        return deselect_reviewer_message


class EQ1_ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action "one referee and editor removes referee"."""


class GT1_ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action "more than one referee and editor removes referee"."""


class ED_REM_REF(DeselectReviewerAction):  # noqa N801
    """Manages wjapp action "editor removes referee"."""


class ReviewerDeclineAction(BaseActionManager):
    """Reviewer decline management."""

    def run(self):
        # wjapp actions for referee declined assignment for preprintid in wjapp:

        # Known cases
        noop_cases = {
            ("JCOM_002Y_0216", 1, 261768),  # the previous ed act as ref removed due to auth eq ed
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ReviewerDeclineAction "
                f"for {self.preprintid}/{self.imported_version_num} (due to auth as ed- known case)."
            )
            return

        # - EQ1_REF_REF: this action indicates that a referee declined an assignment on a
        #   paper with exactly one referee (i.e. the paper has no more active review assignments)

        # - GT1_REF_REF: this action indicates that a referee declined an assignment on a
        #   paper with more than one referee (i.e. the paper has still active review assignments)

        # - REF_REF:  this action indicates that a referee declined an assignment.
        #   It is present in the wjapp code and Action table, but seems not used.
        #   Probably has been replaced by the two above. Added for completeness

        self.check_editor_set()
        reviewer_decline_message = self.read_reviewer_decline_message()
        self.reviewer_declines(reviewer_decline_message)

    def read_reviewer_decline_message(self):
        """Read decline message."""

        cursor_reviewer_decline_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp we don't know why a certain message EMAIL was sent to someone. So we make a list of all messages
        # from editor, in a certain time range respect to the action_date
        # time range fixed to 48 (JCAP_044P_0716_EMAIL021040716)"

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - exist documentLayerSubject customized by the editor
        #      - imported _version_cod ensures to retrive the correct article

        query_reviewer_decline_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='EMAIL'
AND dl.documentLayerSubject LIKE 'Referee declines assignment%'
AND ur.userType='author'
AND dl.submissionDate>=%(action_date)s
AND dl.submissionDate<DATE_ADD(%(action_date)s, INTERVAL 48 SECOND)
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOM_005A_0416"
            and self.imported_version_cod == 53205
            and self.action["agentCod"] == 8572
        ):
            action_date = "20160504155638"
            logger.debug(f"fixed reviewer decline msg 11 seconds after action date {self.preprintid}")

        if (
            self.preprintid == "JCOM_002A_0616"
            and self.imported_version_cod == 53277
            and self.action["agentCod"] == 8474
        ):
            action_date = "20160625193509"
            logger.debug(f"fixed reviewer decline msg 15 seconds after action date {self.preprintid}")

        cursor_reviewer_decline_message.execute(
            query_reviewer_decline_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
            },
        )

        if cursor_reviewer_decline_message.rowcount == 1:
            reviewer_decline_message = cursor_reviewer_decline_message.fetchone()
            logger.debug(f"Found 1 reviewer decline message: {self.preprintid}/{self.imported_version_num}")
        elif cursor_reviewer_decline_message.rowcount > 1:
            reviewer_decline_message = cursor_reviewer_decline_message.fetchone()
            logger.debug(
                f"Found {cursor_reviewer_decline_message.rowcount} reviewer decline message "
                f"taken the most recent one: {self.preprintid}/{self.imported_version_num}"
            )
        else:
            logger.warning(
                f"Found 0 reviewer decline message: for {self.preprintid}/{self.imported_version_num}"
                f" with agent {self.action['agentCod']} and date {self.action['actionDate']}"
            )
            # fallback, signaled with a warning, in some cases e.g. JCAP_019P_0506
            # the message does not exists
            reviewer_decline_message = {
                "documentLayerCod": 0,
                "documentLayerText": "",
            }

        cursor_reviewer_decline_message.close()
        return reviewer_decline_message

    def reviewer_declines(self, reviewer_decline_message):
        """Reviewer declines."""

        reviewer_cod = self.action["agentCod"]
        reviewer_lastname = self.action["agentLastname"]
        reviewer_firstname = self.action["agentFirstname"]
        reviewer_email = self.action["agentEmail"]
        reviewer_privacy = self.action["agentPrivacy"]
        reviewer_declines_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
            reviewer_privacy,
            self.connection,
        )

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = reviewer

        # We create versions (and RAs) in a serial fashion, i.e. one after the other,
        # respecting the temporal order in which they have been originally created;
        # so we are always working on the latest/current version/review-round.

        # replaced "get()"" with "filter last()" to fix case JCOM_013A_0524:
        # more review assignment for the same referee in the same wjapp
        # version (same wjs review round)
        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_declines_date),
        ):
            logger.debug(f"Reviewer {reviewer} declines {self.article}")

            additional_comments = newlines_text_to_html(reviewer_decline_message.get("documentLayerText"))

            # TBV: not added to imported document layer list because the decline action does not save the message.
            # therefore the import of the message happens with the general correspondence.

            EvaluateReview(
                assignment=review_assignment,
                reviewer=reviewer,
                editor=self.get_current_editor(),
                form_data={"reviewer_decision": "0", "additional_comments": additional_comments, "accept_gdpr": True},
                request=request,
                token=None,
            ).run()

        self.check_and_fix_all_reminder()
        return

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for ReviewerDeclineAction if exists in wjs"""

        self.check_and_fix_action_reminder("ref_declined_ed_decision", "EDMD", 3)
        self.check_and_fix_action_reminder("Ref_decline", "EDSR", 3)


class EQ1_REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "one referee and referee refuses"."""

    # automated message disabled
    EvaluateReview._log_decline = noop


class GT1_REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "more than one and referee refuses"."""

    # automated message disabled
    EvaluateReview._log_decline = noop


class REF_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "referee refuses"."""

    # automated message disabled
    EvaluateReview._log_decline = noop


class ED_NEED_REF(ReviewerDeclineAction):  # noqa N801
    """Manages wjapp action "editor needs referee"."""

    # in wjapp this action means that the editor stops to review her/him self
    # the article and will assign referees (in wjs declines as reviewer)

    # the automated message is not disabled for ED_NEED_REF
    # otherwise the action does not appear in the timeline
    # e.g. JCOM_004A_0924


class REF_ACC(BaseActionManager):  # noqa N801
    """Reviewer accepts the review: wjapp action "referee accepts"."""

    def run(self):
        logger.debug("REF_ACC managed in ReviewAssignmentAction but without reviewer confirmation message")


class REF_SENDS_REP(BaseActionManager):  # noqa N801
    """Reviewer send report management: wjapp action "referee sends report"."""

    def run(self):
        # Reviewer send report

        self.check_editor_set()
        self.reviewer_send_report(self.read_reviewer_report_message())

    def read_reviewer_report_message(self):
        """Read report message."""

        cursor_reviewer_report_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp a certain message is not directly linked to an action. So we make a list of REREP
        # from the reviewer, taking the closer in time to the action_date if two. If more than two
        # or zero raise an exception

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - imported _version_cod ensures to retrive the correct article

        query_reviewer_report_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerId,
dl.documentLayerText,
dl.documentLayerOnlyTex,
dl.submissionDate
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='REREP'
AND ur.userType='author'
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])
        if (
            self.preprintid == "JCOMAL_002A_0621"
            and self.imported_version_cod == 390
            and self.action["agentCod"] == 10293
        ):
            action_date = "20210804230010"
            logger.debug(f"fixed reviewer date 20 seconds after action date {self.preprintid}")

        cursor_reviewer_report_message.execute(
            query_reviewer_report_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
            },
        )
        if cursor_reviewer_report_message.rowcount == 0:
            logger.error(
                f"Found {cursor_reviewer_report_message.rowcount}"
                f" reviewer report: {self.preprintid}/{self.imported_version_num}"
            )
            reviewer_report_message = None
            # added for JHEP/JCAP ST
            raise ValueError("Not found reviewer report message")
        elif cursor_reviewer_report_message.rowcount > 2 and self.preprintid == "JCAP_022P_1205":
            # This action could not be repeated in wjapp - a bug with missing state control plus
            # stale browser-cached form data let the referee resubmit the same report 6 times
            # within ~5 minutes. Take the most recent one (the query orders by submissionDate).
            rows = cursor_reviewer_report_message.fetchall()
            reviewer_report_message = rows[-1]
            logger.warning(
                f"Found {cursor_reviewer_report_message.rowcount} reviewer report (known repeated"
                f" submission bug), using the most recent {reviewer_report_message['documentLayerId']}"
                f" agent {self.action['agentCod']} for {self.preprintid}/{self.imported_version_num}"
            )
        elif cursor_reviewer_report_message.rowcount > 2:
            logger.error(
                f"Found {cursor_reviewer_report_message.rowcount}"
                f" reviewer report: {self.preprintid}/{self.imported_version_num}"
            )
            reviewer_report_message = None
            # added for JHEP/JCAP ST
            raise ValueError("Not found reviewer report message")
        elif cursor_reviewer_report_message.rowcount == 2:
            fmt = "%Y%m%d%H%M%S"
            action_dt = datetime.strptime(action_date, fmt)
            rows = cursor_reviewer_report_message.fetchall()
            sd1 = datetime.strptime(rows[0]["submissionDate"], fmt)
            sd2 = datetime.strptime(rows[1]["submissionDate"], fmt)
            diff1 = abs((sd1 - action_dt).total_seconds())
            diff2 = abs((sd2 - action_dt).total_seconds())
            reviewer_report_message = rows[0] if diff1 <= diff2 else rows[1]
            logger.debug(
                f"Found closer in time of two reviewer report {reviewer_report_message['documentLayerId']}"
                f" agent {self.action['agentCod']} for {self.preprintid}/{self.imported_version_num}"
            )
        else:
            reviewer_report_message = cursor_reviewer_report_message.fetchone()
            logger.debug(
                f"Found reviewer report {reviewer_report_message['documentLayerId']}"
                f" agent {self.action['agentCod']} {self.preprintid}/{self.imported_version_num}"
            )
        cursor_reviewer_report_message.close()
        return reviewer_report_message

    def reviewer_send_report(
        self,
        wjapp_report,
    ):
        """Reviewer sends report."""

        reviewer_cod = self.action["agentCod"]
        reviewer_lastname = self.action["agentLastname"]
        reviewer_firstname = self.action["agentFirstname"]
        reviewer_email = self.action["agentEmail"]
        reviewer_privacy = self.action["agentPrivacy"]
        reviewer_report_date = self.action["actionDate"]

        reviewer = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            reviewer_cod,
            reviewer_lastname,
            reviewer_firstname,
            reviewer_email,
            reviewer_privacy,
            self.connection,
        )

        # filter and last() not get() to manage JCOM_008A_0324 with 2 review assignments
        review_assignment = WorkflowReviewAssignment.objects.filter(
            reviewer=reviewer,
            article=self.article,
            editor=self.get_current_editor(),
            review_round=self.article.current_review_round_object(),
        ).last()
        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = reviewer
        submit_final = True

        # the (current) default review form element "Cover letter (for the Editor in charge)""
        # is a WjsMiniHTMLFormField. Text from wjapp must not be formatted to html.
        # unlike author_review, editor_cover_letter has no plain-text ("_tex") sibling field, so this
        # value always goes through bleach.clean(). An unescaped "<" followed by a letter, with no
        # later ">" in the string, makes bleach's tokenizer silently drop everything after it (same
        # failure mode fixed for author_review_tex) - escape it here so bleach never sees a raw "<".
        raw_cover_letter_message = wjapp_report.get("documentLayerText")
        formatted_cover_letter_message = (
            escape(raw_cover_letter_message) if raw_cover_letter_message else raw_cover_letter_message
        )

        # Use author_review_tex (plain CharField, no bleach sanitization) instead of author_review
        # (WjsMiniHTMLFormField): the latter runs the text through bleach.clean(), which silently
        # truncates it at the first unterminated "<" (common in LaTeX source).
        # not reliable: formatted_report_message = wjapp_report.get("documentLayerOnlyTex")

        formatted_report_message = import_file_manager.ImportFileManager(self).import_rerep_file_source(
            wjapp_report.get("documentLayerId")
        )

        # we leave empty those fields that don't exist in wjapp and
        # that do not have a "mandatory" value (such as no-conflict-of-interests).

        report_form = get_report_form(self.journal.code)

        # with the data in this example the form would be valid, but the imported report
        # have not this data. The Key error on the template report_form_summary.html
        # has been solved in template tag "keyvalue"
        #
        # conflict_of_interest: "no",
        # structure_and_writing_style: "Good",
        # originality: "Good",
        # scope_and_methods: "Good",
        # argument_and_discussion: "Good",
        # recommendation: "publish",
        # editor_cover_letter: formatted_cover_letter_message,
        # author_review: formatted_report_message,

        # Note: form not valid because has empty values
        jcom_report_form_data = {
            "conflict_of_interest": "no",
            "editor_cover_letter": formatted_cover_letter_message,
            "author_review_tex": formatted_report_message,
        }

        form = report_form(
            data=jcom_report_form_data,
            review_assignment=review_assignment,
            request=request,
            submit_final=True,
            journal=self.journal,
        )

        # form is not valid because missing required fields
        # import data problem: the required fields have fixed select values ex:
        # Structure and writing style: Poor | Acceptable | Good | Excellent
        # and we have not this values from wjapp
        #
        # we do not need to check our data because they already exist in the (wjapp) system
        # and so we can consider them to be valid a priori
        if form.is_valid():
            logger.warning("Report form is valid this was unexpected because of missing required fields")

        with freezegun.freeze_time(
            rome_timezone.localize(reviewer_report_date),
        ):
            # SubmitReview does not validate the form.
            # the form is validated in view ReviewSubmit -> ReportForm.save()
            # and SubmitReview is called only afterwards

            # the automated message is disabled
            SubmitReview._log_operation = noop
            submit = SubmitReview(
                assignment=review_assignment,
                form=form,
                submit_final=submit_final,
                request=request,
            )
            wra = submit.run()

            if review_file := import_file_manager.ImportFileManager(self).import_rerep_file_pdf(
                wjapp_report.get("documentLayerId")
            ):
                new_file = files.save_file_to_article(
                    review_file,
                    wra.article,
                    wra.reviewer,
                )
                wra.tex_report_pdf = new_file
                wra.save()

            editor_message_subject = render_template_from_setting(
                setting_group_name="email_subject",
                setting_name="subject_review_complete_acknowledgement",
                journal=self.journal,
                request=request,
                context={
                    "review_assignment": review_assignment,
                    "article": self.article,
                },
                template_is_setting=True,
            )
            communication_utils.log_operation(
                actor=reviewer,
                article=self.article,
                message_subject=editor_message_subject,
                message_body="",
                recipients=[self.get_current_editor()],
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
                hijacking_actor=get_hijacker(),
                notify_actor=False,
            )

            self.imported_document_layer_cod_list.append(wjapp_report.get("documentLayerCod"))
            logger.debug(f"append referee report message {self.imported_document_layer_cod_list=}")

            # WorflowReviewAssignment.report_form_answers is a json containing:
            # - editor_cover_letter (cover letter REREP)
            # - author_review (text REREP)
            self.imported_doclayer_check_visibility[wjapp_report.get("documentLayerCod")] = {
                "obj": wra,
                "type": "REREP",
            }

        self.check_and_fix_all_reminder()

        return

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for REF_SENDS_REP if exists in wjs"""

        # The referee sends report action could be the first, the second, the last report.
        # The check and fix function changes only the existing reminders EDMD created by the wjs logic,
        # therefore if the wjs logic does not supply reminders for this action, nothing is changed,
        # (also if the reminder is read from wjapp) otherwise the date is fixed if it is different.
        self.check_and_fix_action_reminder("only_ref_sent_rep", "EDMD", 3)
        self.check_and_fix_action_reminder("last_ref_sent_rep", "EDMD", 3)


@dataclass
class EditorDecisionAction(BaseActionManager):
    """Editor decision management."""

    editor_decison: tuple = field(init=False)
    requires_revision: bool = field(init=False)
    revision_interval_days: int = field(init=False)

    def run(self):
        # wjs editor report store:
        #
        # - for ED_REQ_REV, ED_ACC_DOC_WMC
        #     the EDREP is visible for the author on revision request page
        #        the view is ArticleRevisionUpdate based on model EditorRevisionRequest
        #         the templates are
        #            "wjs_review/revision/revision_form.html.
        #            --> wjs_review/revision/elements/info.html
        #
        # - for ED_REJ_DOC the EDREP is NOT visible for the author
        #
        # - all editor reports are stored in EditorDecision.decision_editor_report
        #
        # - editor reports with revision request are stored also in EditorRevisionRequest

        self.check_editor_set()

        # special case broken action data on wjapp, missing agentCod:
        #
        # -   actHistCod: 8
        # -   versionCod: 1
        # -    actionCod: 8
        # -     agentCod: NULL
        # -      userCod: 10004
        # - realAgentCod: NULL
        # -   actionDate: 2018-10-31 13:10:43

        if self.action["actHistCod"] == 8 and self.journal.code == "JCOMAL" and self.preprintid == "JCOMAL_001A_1018":
            self.action["agentCod"] = 10004
            logger.warning(
                f"fix action history jcomal/8: {self.imported_version_cod}"
                f" {self.action['agentCod']} {self.action['actionDate']}"
            )

        revision = self.editor_decides()
        if self.requires_revision:
            logger.debug(f"editor decision with revision request EditorRevisionRequest: {revision=}")

    def download_edrep_file(self, document_layer_id):
        """Download EDREP file.

        special case:
        edrep not into the wjapp database report url i.e.: JCOMAL_001A_1018_EDREP000071018

        https://jcomal.sissa.it/jcomal/common/archiveFile?filePath=JCOMAL_001A_1018/1
        /EDREP/JCOMAL_001A_1018_EDREP000071018/JCOMAL_001A_1018_EDREP000071018.txt&fileType=txt
        """

        file_url = (
            f"{self.url_base}{self.preprintid}/{self.imported_version_num}/EDREP/{document_layer_id}/"
            f"{document_layer_id}.txt&fileType=txt"
        )

        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(f"empty {document_layer_id} file downloaded: {response.headers['Content-Length']}")
            # added for JHEP/JCAP ST
            raise ValueError("Not found edrep file")
        return response.text

    def read_editor_report_message(self):
        """Read editor report message."""

        cursor_editor_report_message = self.connection.cursor(buffered=True, dictionary=True)

        # in wjapp a certain message is not directly linked to an action. So we make a list of EDREP
        # from the editor. If two edrep, we take the closer in time, if more than two or zero raise an exception

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - imported _version_cod ensures to retrive the correct article

        query_editor_report_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerId,
dl.documentLayerText,
dl.documentLayerOnlyTex,
dl.submissionDate
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='EDREP'
AND ur.userType='author'
ORDER BY dl.submissionDate
"""

        action_date = str(self.action["actionDate"])
        cursor_editor_report_message.execute(
            query_editor_report_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_editor_report_message.rowcount == 0:
            logger.error(
                f"Found {cursor_editor_report_message.rowcount}"
                f" editor report: {self.preprintid}/{self.imported_version_num}"
            )
            editor_report_message = None
            # added for JHEP/JCAP ST
            raise ValueError("Not found editor report")
        elif cursor_editor_report_message.rowcount > 2:
            logger.error(
                f"Found {cursor_editor_report_message.rowcount}"
                f" editor report: {self.preprintid}/{self.imported_version_num}"
            )
            editor_report_message = None
            # added for JHEP/JCAP ST
            raise ValueError("Not found editor report")
        elif cursor_editor_report_message.rowcount == 2:
            fmt = "%Y%m%d%H%M%S"
            action_dt = datetime.strptime(action_date, fmt)
            rows = cursor_editor_report_message.fetchall()
            sd1 = datetime.strptime(rows[0]["submissionDate"], fmt)
            sd2 = datetime.strptime(rows[1]["submissionDate"], fmt)
            diff1 = abs((sd1 - action_dt).total_seconds())
            diff2 = abs((sd2 - action_dt).total_seconds())
            editor_report_message = rows[0] if diff1 <= diff2 else rows[1]
            logger.debug(
                f"Found closer in time of two editor report {editor_report_message['documentLayerId']}"
                f" agent {self.action['agentCod']} for {self.preprintid}/{self.imported_version_num}"
            )
        else:
            editor_report_message = cursor_editor_report_message.fetchone()
            logger.debug(
                f"Found editor report {editor_report_message.get('documentLayerId')}"
                f" agent {self.action['agentCod']} {self.preprintid}/{self.imported_version_num}"
            )
        cursor_editor_report_message.close()
        return editor_report_message

    def editor_decides(self):
        """Editor decides on article."""

        wjapp_editor_report = self.read_editor_report_message()
        editor_report_date = self.action["actionDate"]
        # the (current) default review form element for editor cover letter
        # has a rich-text/html widget.  Text from wjapp formatted to html
        wjapp_editor_cover_letter_message = newlines_text_to_html(wjapp_editor_report.get("documentLayerText"))

        # the (current) default review form element for editor report
        # has a rich-text/html widget.  Text from wjapp formatted to html
        # e.g. JCOM_027Y_0215 has the cover letter but not the report file

        # special case missing documentLayerOnlyTeX data on wjapp
        if (
            self.journal.code == "JCOMAL"
            and self.preprintid == "JCOMAL_001A_1018"
            and wjapp_editor_report.get("documentLayerCod") == 7
        ):
            wjapp_editor_report_source = newlines_text_to_html(
                str(self.download_edrep_file("JCOMAL_001A_1018_EDREP000071018"))
            )
            logger.warning(
                f"fix missing edrep in documentLayerOnlyTeX: {wjapp_editor_report.get('documentLayerCod')=}"
            )
        else:

            # TODO: adapt to the new jcap review page with provides fields for edrep.tex and erep.pdf
            # As temporary solution the content of the file edrep.tex is attached to the text cover
            # letter of wjapp edrep to bulid the entire "editor report message"
            logger.debug(f"{wjapp_editor_report.get('documentLayerId')=}")

            wjapp_editor_report_source = import_file_manager.ImportFileManager(self).import_edrep_file_source(
                wjapp_editor_report.get("documentLayerId")
            )

            # retuns a DjangoFile
            wjapp_editor_report_pdf = import_file_manager.ImportFileManager(self).import_edrep_file_pdf(
                wjapp_editor_report.get("documentLayerId")
            )
            if wjapp_editor_report_pdf:
                assignment = WjsEditorAssignment.objects.get_current(self.article)
                generated_pdf_jf = files.save_file_to_article(
                    file_to_handle=wjapp_editor_report_pdf,
                    article=self.article,
                    owner=self.get_current_editor(),
                    label="Editor report PDF",
                )
                assignment.editor_report_pdf_draft = generated_pdf_jf
                assignment.save()

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.get_current_editor()

        with freezegun.freeze_time(
            rome_timezone.localize(editor_report_date),
        ):
            # If the editor has done i will review, we set the wra of the editor "completed"
            # if it is not, otherwise the wra results as deselect.
            # The editor report is not uploaded as reviewer report, because it seems useless.
            # In wjapp in this case the editor does not upload
            # a report as referee. It would be equal to the editor report.
            # TBV: better to do the action SubmitReview using the same report text?
            review_assignment = WorkflowReviewAssignment.objects.filter(
                reviewer=self.get_current_editor(),
                article=self.article,
                editor=self.get_current_editor(),
                review_round=self.article.current_review_round_object(),
            ).last()
            if review_assignment and not review_assignment.is_complete:
                review_assignment.date_complete = timezone.now()
                review_assignment.is_complete = True
                review_assignment.save()

                # delete reminders related to the review assignment
                Reminder.objects.filter(
                    content_type=ContentType.objects.get_for_model(review_assignment),
                    object_id=review_assignment.id,
                ).delete()

            date_due = timezone.localtime(timezone.now()).date()
            if self.requires_revision:
                date_due = date_due + datetime.timedelta(days=self.revision_interval_days)

            # wjapp has only one document revision dead line therefore
            # this assignment could be not correct if this is for example the first revision request
            # and afterwards there is another one revision request
            # but the same problem can happen if this assignment is done
            # during the action PSTPN_REV_DEADLN (which is managed as correspondence only)

            # added guard for None document_revision_dead_line: JCOM_002N_0824
            if self.document_revision_dead_line:
                if self.document_revision_dead_line.date() >= date_due:
                    date_due = self.document_revision_dead_line

            # TBV: date_due has to be set in the form in the case of rejection?
            # TBV: HandleDecision bypasses DecisionForm/bleach, so this raw LaTeX text is saved as-is
            # (no truncation on import). But review_decision.html renders decision_editor_report|safe
            # into the shared modal.html (also |safe), so an unescaped "<" in the LaTeX source will
            # still get silently dropped by the browser when displayed - same failure mode as
            # author_review_tex, pending the shared modal.html fix from wjs-develop.
            form_data = {
                "decision": self.editor_decision,
                "decision_editor_report": wjapp_editor_report_source,
                "withdraw_notice": "notice",
                "date_due": date_due,
            }

            # TODO: fix JCOM_004A_0424 exception: WjsEditorAssignment matching query does not exist

            # the automated message are disabled
            # TODO: ex: JCOM_002N_0824 Subject: Invite to review withdrawn
            # is not disabled on rejection
            HandleDecision._log_accept = noop
            HandleDecision._log_decline = noop
            HandleDecision._log_not_suitable = noop
            HandleDecision._log_revision_request = noop
            HandleDecision._log_requires_resubmission = noop
            HandleDecision._log_technical_revision_request = noop
            handle = HandleDecision(
                workflow=self.article.articleworkflow,
                form_data=form_data,
                user=self.get_current_editor(),
                request=request,
            )
            handle.run()

            message_timeline = render_template_from_setting(
                setting_group_name=self.setting_group_name,
                setting_name=self.setting_name,
                journal=self.journal,
                request=request,
                context={
                    "article": self.article,
                    "decision": self.editor_decision,
                    "request": request,
                },
            )

            communication_utils.log_operation(
                article=self.article,
                message_subject=f"{message_timeline}",
                message_body=wjapp_editor_cover_letter_message,
                actor=self.get_current_editor(),
                recipients=[self.article.correspondence_author],
                verbosity=Message.MessageVerbosity.TIMELINE,
                hijacking_actor=get_hijacker(),
                notify_actor=communication_utils.should_notify_actor(),
            )

        self.article.refresh_from_db()
        revision = None
        if self.requires_revision:
            revision = EditorRevisionRequest.objects.get(
                article=self.article, review_round=self.article.current_review_round_object()
            )

        self.imported_document_layer_cod_list.append(wjapp_editor_report.get("documentLayerCod"))

        # EditorRevisionRequest.editor_decision contains the editor report
        # (wjapp editor cover letter + wjapp editor report)
        # if revision is not required, no need to check extra permissions in wjapp
        if revision:
            self.imported_doclayer_check_visibility[wjapp_editor_report.get("documentLayerCod")] = {
                "obj": revision.editor_decision,
                "type": "EDREP",
            }

        self.check_and_fix_all_reminder()

        return revision


@dataclass
class ED_REQ_REV(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor requires major revision"."""

    def __post_init__(self):
        """Set the specific data for major revision"""
        self.editor_decision = ArticleWorkflow.Decisions.MAJOR_REVISION
        self.requires_revision = True
        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_major_revision_days",
            self.journal,
        ).process_value()
        self.setting_group_name = "email_subject"
        self.setting_name = "subject_request_revisions"

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminder for ED_REQ_REV if exists in wjs"""

        self.check_and_fix_action_reminder("pending_revision", "AUMJR", 3)


@dataclass
class ED_ACC_DOC_WMC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor requires minor revision"."""

    def __post_init__(self):
        """Set the specific data for minor revision"""
        self.editor_decision = ArticleWorkflow.Decisions.MINOR_REVISION
        self.requires_revision = True
        self.revision_interval_days = get_setting(
            "wjs_review",
            "default_author_minor_revision_days",
            self.journal,
        ).process_value()
        self.setting_group_name = "wjs_review"
        self.setting_name = "review_decision_requires_resubmission_subject"

    def check_and_fix_all_reminder(self):
        """Checks and fix wjapp reminders for ED_ACC_DOC_WMC if exists in wjs"""

        self.check_and_fix_action_reminder("pending_minor_revision", "AUMIR", 3)


@dataclass
class ED_REJ_DOC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor rejects document"."""

    def __post_init__(self):
        """Set the specific data for rejection"""
        self.editor_decision = ArticleWorkflow.Decisions.REJECT
        self.requires_revision = False
        self.revision_interval_days = 0
        self.setting_group_name = "email_subject"
        self.setting_name = "subject_review_decision_decline"


@dataclass
class ED_CON_NOT_SUIT(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor considers not suitable"."""

    def __post_init__(self):
        """Set the specific data for not suitable"""
        self.editor_decision = ArticleWorkflow.Decisions.NOT_SUITABLE
        self.requires_revision = False
        self.revision_interval_days = 0
        self.setting_group_name = "wjs_review"
        self.setting_name = "review_decision_not_suitable_subject"


# TBV: DEBUG 2024-06-02 11:00:33,000 M:logic: No XML galleys found for crossref citation extraction
# TO FIX: exeception
@dataclass
class ED_ACC_DOC(EditorDecisionAction):  # noqa N801
    """Manages wjapp action "editor accepts document"."""

    def __post_init__(self):
        """Set the specific data for acceptance"""
        self.editor_decision = ArticleWorkflow.Decisions.ACCEPT
        self.requires_revision = False
        self.revision_interval_days = 0
        self.setting_group_name = "email_subject"
        self.setting_name = "subject_review_decision_accept"


@dataclass
class AuthorSubmitRevisionAction(BaseActionManager):
    """Author submit revision management."""

    # action_triggers_import_files not used here because this action always implies new files from the author

    def run(self):
        # TBV: author of the action is the same of main_author?
        #      the author could be switched with coauthor

        # This fix solves a problem in broken wjapp data for JCOM_031A_1024/2 which is
        # a version with only the submission of the contribution and no other actions.
        # The workaround is to not execute AuthorHandleRevision for this version
        # and let only import the correspondence. The files and the author cover letter
        # are not imported because they are not been reviewed. The review has been done
        # on version  JCOM_031A_1024/3.
        if self.preprintid == "JCOM_031A_1024" and self.imported_version_num == 2:
            logger.warning(
                f"fix of broken wjapp data version eliminated: {self.preprintid} {self.imported_version_num=}"
            )

            return

        author_report_date = self.action["actionDate"]

        request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        request.user = self.article.correspondence_author

        author_note = self.read_author_cover_letter_message()

        form_data = {"author_note": newlines_text_to_html(author_note["documentLayerText"])}

        # TODO: missing for JCOM_031A_1024 data broken on wjapp
        revision_request = EditorRevisionRequest.objects.get(
            article=self.article, review_round=self.article.current_review_round_object()
        )

        with freezegun.freeze_time(
            rome_timezone.localize(author_report_date),
        ):
            # the automated message is not disabled on purpose to have
            # the timeline
            # AuthorHandleRevision._log_operation = noop #noqa
            service = AuthorHandleRevisionObsolete(
                revision=revision_request,
                form_data=form_data,
                user=self.article.correspondence_author,
                request=request,
            )
            revision = service.run()

        self.article.refresh_from_db()

        if author_note.get("documentLayerCod") != 0:
            self.imported_document_layer_cod_list.append(author_note.get("documentLayerCod"))

            # EditorRevisionRequest.author_note contains the author cover letter
            self.imported_doclayer_check_visibility[author_note.get("documentLayerCod")] = {
                "obj": revision,
                "type": "CVLETT",
            }

        # JCAP_080P_1026/1, actHistCod 501607: this AU_CONF_VER is a relabel of the real
        # ADMIN_RESETS_ED (import_specific_corrections.py), used only to supply the missing
        # UNDER_APPEAL -> EDITOR_SELECTED transition for the reject+appeal correction - it is
        # not a real new-version file submission. Per wjapp's own data it still belongs to
        # version 1 (confirmed via SQL), whose files were already imported earlier in this same
        # version's processing, so re-running import_version_files() here would try to rebuild
        # the same build_archive_dir/zip a second time and crash with FileExistsError.
        skip_file_reimport_cases = {
            ("JCAP_080P_1026", 1, 501607),
        }
        if self.importfiles and (self.preprintid, self.imported_version_num, self.action["actHistCod"]) not in (
            skip_file_reimport_cases
        ):
            import_file_manager.ImportFileManager(self).import_version_files()

        self.check_and_fix_all_reminder()

        return

    def read_author_cover_letter_message(self):
        """Read author cover letter message."""

        cursor_cover_letter_message = self.connection.cursor(buffered=True, dictionary=True)

        # NOTE: condition on documentLayerSubject not used because:
        #      - wjapp maintenace "change documentType" let old preprintid in
        #        Document_Layer (and Attachments)
        #      - imported _version_cod ensures to retrive the correct article

        query_cover_letter_message = """
SELECT
dl.documentLayerCod,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='CVLETT'
AND ur.userType='author'
ORDER BY dl.submissionDate
"""

        # Because the action date is out of the interval set in the query, it is forced
        # like the documentlayer date. The option to enlarge more the interval in the query risks
        # to bring errors considering all the articles imported.
        action_date = str(self.action["actionDate"])

        if self.preprintid == "JCOM_001A_0520" and self.imported_version_cod == 55227:
            action_date = "20200616233039"
            logger.debug(f"fixed cvlett date 21 seconds after action date {self.preprintid}")

        if self.preprintid == "JCOM_005A_1117" and self.imported_version_cod == 54139:
            action_date = "20180428112442"
            logger.debug(f"fixed cvlett date 8 seconds after action date {self.preprintid}")

        if self.preprintid == "JCOM_006A_1217" and self.imported_version_cod == 54012:
            action_date = "20180215042732"
            logger.debug(f"fixed cvlett date 6 seconds after action date {self.preprintid}")

        if self.preprintid == "JCOM_005A_1216" and self.imported_version_cod == 53625:
            action_date = "20170410161125"
            logger.debug(f"fixed cvlett date 6 seconds after action date {self.preprintid}")

        cursor_cover_letter_message.execute(
            query_cover_letter_message,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": action_date,
            },
        )
        if cursor_cover_letter_message.rowcount == 1:
            cover_letter_message = cursor_cover_letter_message.fetchone()
            logger.debug(
                f"Found 1 cover letter {self.preprintid}/{self.imported_version_cod}"
                f" {cover_letter_message.get('documentLayerCod')}"
            )
        elif cursor_cover_letter_message.rowcount > 1:
            if self.preprintid == "JCAP_061P_0524" and self.imported_version_num == 2:
                cover_letter_message = cursor_cover_letter_message.fetchall()[-1]
                logger.debug(
                    f"Found 2 cover letter taken the last one {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_009P_1023" and self.imported_version_cod == 66001:
                # 6 cover letters share this versionCod (this reader has no date-window filter, so
                # it also picks up later resubmission attempts wjapp filed under the same version)
                # - the action's own actionDate (2024-01-19 18:57:59) matches the first one
                # (documentLayerCod 403108) exactly.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_078P_1022" and self.imported_version_cod == 61580:
                # Same misfiled-cover-letter pattern as JCAP_009P_1023: version 2's real AU_SUB_REV
                # (actionDate 2023-03-31 16:04:32) matches the first cover letter (documentLayerCod
                # 373912, submitted 2023-03-31 16:04:32) exactly. The second one (377551, submitted
                # 2023-05-11 16:15:43) actually belongs to version 3's own resubmission - it sits 5
                # minutes before version 3's AU_SUB_REV_WMC (2023-05-11 16:20:35), which already has
                # its own correct, separate cover letter (377552) - but was filed under version 2's
                # versionCod in wjapp by mistake.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_057P_0621" and self.imported_version_cod == 54520:
                # Same misfiled-cover-letter pattern as JCAP_078P_1022/JCAP_009P_1023: version 2's
                # real AU_SUB_REV (actionDate 2021-09-15 17:12:38) matches the first cover letter
                # (documentLayerCod 328199, submitted 2021-09-15 17:12:38) exactly. The second one
                # (331019, submitted 2021-10-18 17:39:19) actually belongs to version 3's own
                # resubmission - it sits 10 minutes before version 3's AU_SUB_REV_WMC (2021-10-18
                # 17:49:28) - but was filed under version 2's versionCod in wjapp by mistake.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_099P_0619" and self.imported_version_cod == 44999:
                # Same misfiled-cover-letter pattern: version 2's real AU_SUB_REV (actionDate
                # 2019-08-14 15:26:14) matches the first cover letter (documentLayerCod 264911,
                # submitted 2019-08-14 15:26:14) exactly. The second one (267456, submitted
                # 2019-09-17 09:30:18) belongs to version 3's own resubmission, misfiled under
                # version 2's versionCod in wjapp.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_099P_0619" and self.imported_version_cod == 45344:
                # Version 3's real AU_SUB_REV_WMC (actionDate 2019-09-17 09:35:41) matches the
                # first of 4 cover letters sharing this versionCod (documentLayerCod 267457,
                # submitted 2019-09-17 09:35:41) exactly. The other 3 (267920/267921/267923, all
                # submitted 2019-09-21) belong to a later resubmission attempt, misfiled under
                # version 3's versionCod in wjapp.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_060P_1018" and self.imported_version_cod == 44652:
                # Same misfiled-cover-letter pattern: version 3's real AU_SUB_REV (actionDate
                # 2019-07-11 22:27:22) matches the first cover letter (documentLayerCod 262302,
                # submitted 2019-07-11 22:27:23 - 1 second later, not exact like the other cases,
                # but still the obvious match). The second one (270227, submitted 2019-10-17
                # 06:47:50, 3+ months later) belongs to a later resubmission attempt, misfiled
                # under version 3's versionCod in wjapp.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_005P_0719" and self.imported_version_cod == 45640:
                # Same misfiled-cover-letter pattern: version 2's real AU_SUB_REV (actionDate
                # 2019-10-07 16:13:51) matches the first cover letter (documentLayerCod 269422,
                # submitted 2019-10-07 16:13:51) exactly. The second one (273897, submitted
                # 2019-11-29 02:38:25) belongs to version 3's own resubmission, misfiled under
                # version 2's versionCod in wjapp.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_006P_0312" and self.imported_version_cod == 16513:
                # Version 2's real AU_CONF_VER (actionDate 2012-05-16 10:55:44) matches the
                # first of 4 cover letters sharing this versionCod (documentLayerCod 90616,
                # submitted 2012-05-16 10:55:44) exactly. The other 3 (92804/92808/92817, all
                # submitted 2012-06-26 morning/early afternoon) are NOT version 3's cover letter
                # either - version 3 has its own, separately-filed one (documentLayerCod 92829,
                # versionCod 16873, submitted 2012-06-26 14:30:02, matching the wjapp UI exactly)
                # - these 3 are genuinely orphaned duplicate/retry submissions, misfiled under
                # version 2's versionCod and not referenced by either version's real resolution.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_017P_0712" and self.imported_version_cod == 17701:
                # Same misfiled-cover-letter pattern: version 2's real AU_SUB_REV (actionDate
                # 2012-09-27 17:58:49) matches the first of 5 cover letters sharing this
                # versionCod (documentLayerCod 97760, submitted 2012-09-27 17:58:49) exactly.
                # The other 4 (99016/99017/99018/99019, all submitted 2012-10-22) belong to a
                # later resubmission attempt, misfiled under version 2's versionCod in wjapp.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            elif self.preprintid == "JCAP_034P_0712" and self.imported_version_cod == 17560:
                # Same misfiled-cover-letter pattern: version 2's real AU_SUB_REV_WMC (actionDate
                # 2012-09-10 17:55:13) matches the first cover letter (documentLayerCod 96862,
                # submitted 2012-09-10 17:55:13) exactly. The second one (98808, submitted
                # 2012-10-17 17:14:30) belongs to a later resubmission attempt, misfiled under
                # version 2's versionCod in wjapp.
                cover_letter_message = cursor_cover_letter_message.fetchall()[0]
                logger.debug(
                    f"Found {cursor_cover_letter_message.rowcount} cover letters, taken the one matching "
                    f"the action date {self.preprintid}/{self.imported_version_num}"
                    f" {cover_letter_message.get('documentLayerCod')}"
                )
            else:
                logger.error(
                    f"Found {cursor_cover_letter_message.rowcount} cover letter: "
                    f"{self.preprintid}/{self.imported_version_cod}"
                )
                cover_letter_message = None
                # added for JHEP/JCAP ST
                raise ValueError("Not found cover letter")
        else:
            # fallback, signaled with a warning, in some cases
            # the cover letter documentLayer of type CVLETT for the author does not exists.
            # the documentLayerCod = 0  does not match with real documentLayercods
            logger.warning(
                f"Found 0 cover letter sent by author for {self.preprintid}/"
                f"{self.imported_version_cod} - saved an empty message"
            )
            cover_letter_message = {"documentLayerCod": 0, "documentLayerSubject": "", "documentLayerText": ""}

        cursor_cover_letter_message.close()

        return cover_letter_message


@dataclass
class AU_SUB_REV(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author submits revised version"."""

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for AU_SUB_REV if exists in wjs"""

        self.check_and_fix_action_reminder("major_revision", "EDSR", 3)


@dataclass
class AU_SUB_REV_WMC(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author submits minor revision"."""

    def check_and_fix_all_reminder(self):
        """Check and fix wjapp reminder for AU_SUB_REV_WMC if exists in wjs"""

        self.check_and_fix_action_reminder("minor_revision", "EDSR", 3)


@dataclass
class AU_SUB_REV_ED_CH(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "Author submits after ed check"."""

    def __post_init__(self):
        """logs error if not know case"""

        known_cases = {("JCOM_003A_0417", 4, 266570), ("JCOM_012A_0615", 4, 261114)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) not in known_cases:
            logger.error(
                f"case not managed {self.action['actHistCod']} AU_SUB_REV_ED_CH "
                f"for {self.preprintid}/{self.imported_version_num}."
            )
            # added for JHEP/JCAP ST
            raise ValueError("case not managed AU_SUB_REV_ED_CH")
        else:
            logger.warning(
                f"AU_SUB_REV_ED_CH {self.action['actHistCod']} "
                f"for {self.preprintid}/{self.imported_version_num} (known case)."
            )


@dataclass
class AU_SUB_NEW_VER(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author submits new version" (appeal)."""


@dataclass
class AU_CONF_VER(AuthorSubmitRevisionAction):  # noqa N801
    """Manages wjapp action "author confirms version" (appeal)."""


class SelectCoauthorAction(BaseActionManager):
    """Coauthor selection management."""

    def run(self):

        # JCOM_001E_0322 is a published paper. Coauthor remains corrected as published.
        noop_cases = {
            ("JCOM_001E_0322", 1, 285908),
            ("JCOM_007A_0623", 1, 291630),
            ("JCOM_004Y_0623", 1, 292042),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} Select coauthor "
                f"for {self.preprintid}/{self.imported_version_num} (coauthor as reviewer/editor - known case)."
            )
            return

        # coauthor is the target of the wjapp action
        coauthor_cod = self.action["targetCod"]
        coauthor_lastname = self.action["targetLastname"]
        coauthor_firstname = self.action["targetFirstname"]
        coauthor_email = self.action["targetEmail"]
        coauthor_privacy = self.action["targetPrivacy"]
        coauthor_assign_date = self.action["actionDate"]

        # coauthor data
        coauthor = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            coauthor_cod,
            coauthor_lastname,
            coauthor_firstname,
            coauthor_email,
            coauthor_privacy,
            self.connection,
        )
        logger.debug(f"Creating coauthor of {self.article.id} user: {coauthor}")

        # NOTE: the wjapp message related to select coauthor acion is imported to wjs
        # as general correspondence
        with freezegun.freeze_time(
            rome_timezone.localize(coauthor_assign_date),
        ):
            if not coauthor.check_role(self.journal, "author", staff_override=False):
                coauthor.add_account_role("author", self.journal)
            order = self.article.next_frozen_author_order()
            submission_models.FrozenAuthor.objects.get_or_create(
                article=self.article,
                author=coauthor,
                defaults={
                    "order": order,
                },
            )

        return


class AU_SELECTS_COAUT(SelectCoauthorAction):  # noqa N801
    """Manages wjapp action "author selects co-author"."""


class ADMIN_SELECTS_COAUT(SelectCoauthorAction):  # noqa N801
    """Manages wjapp action "editorial office selects co-author"."""


class SwapCorrespondenceAuthor(BaseActionManager):  # noqa N801
    """Manages wjapp action "author changes corresponding author"."""

    def run(self):
        # E.g. JCOM_001A_1115
        # note: in wjapp the new corresponding author must be one of the coauthors

        # This method changes the corresponding author. The related messages are loaded as correspondence
        # for all the subclasses maintaining sender and recipient

        new_author_row = self.read_new_author_data()
        new_author = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            new_author_row["new_author_cod"],
            new_author_row["new_author_lastname"],
            new_author_row["new_author_firstname"],
            new_author_row["new_author_email"],
            new_author_row["new_author_privacy"],
            self.connection,
        )
        logger.debug(f"swap corresponding author, new: {new_author}")
        assert new_author in self.article.author_accounts.all()

        # when the action swaps between the past author of version 1
        # added at first action SYS_ASS_ED and the current correspondence
        # author set at create article, there is nothing to do, also the
        # order is already correct.
        # If the two users are different, also the orders are swapped.
        if new_author != self.article.correspondence_author:
            self.article.owner = new_author
            aao_new_author = submission_models.FrozenAuthor.objects.get(
                article=self.article,
                author=new_author,
            )
            submission_models.FrozenAuthor.objects.filter(
                article=self.article,
                author=self.article.correspondence_author,
            ).update(order=aao_new_author.order)

            aao_new_author.order = 0
            aao_new_author.save()

            self.article.correspondence_author = new_author
            self.article.save()

    def read_new_author_data(self):
        """Read new author data."""

        cursor_new_author = self.connection.cursor(dictionary=True)
        query = """
SELECT
userCod AS new_author_cod,
lastname AS new_author_lastname,
firstname AS new_author_firstname,
email AS new_author_email,
privacy AS new_author_privacy
FROM User
WHERE
userCod = %(new_author_cod)s
"""
        cursor_new_author.execute(
            query,
            {
                "new_author_cod": self.action["targetCod"],
            },
        )
        new_author_row = cursor_new_author.fetchone()
        cursor_new_author.close()
        return new_author_row


class AU_SWAPS_CORR_AU(SwapCorrespondenceAuthor):  # noqa N801
    """Manages wjapp action "author changes corresponding author"."""


class ADMIN_SWAPS_CORR_AU(SwapCorrespondenceAuthor):  # noqa N801
    """Manages wjapp action "editorial office changes corresponding author"."""


# PRODUCTION


class TYP_TAKES_CHARGE(BaseActionManager):  # noqa N801
    """Manages wjapp action "typesetter takes in charge"."""

    def run(self):
        """Assign the typesetter.

        Also create the typesetter's Account if necessary.
        """

        noop_cases = {("JCOM_003A_0417", 2, 265687), ("JCOM_012A_0615", 2, 260995)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} TYP_TAKES_CHARGE "
                f"for {self.preprintid}/{self.imported_version_num} (management ed restart - known case)."
            )
            return

        # specs#3131: the replayed acceptance may have held the paper in ACCEPTED; the wjapp history
        # goes on, so let it through before the typesetter takes it in charge.
        release_production_readiness_hold(self.article, when=rome_timezone.localize(self.action["actionDate"]))

        typesetter_cod = self.action["agentCod"]
        typesetter_lastname = self.action["agentLastname"]
        typesetter_firstname = self.action["agentFirstname"]
        typesetter_email = self.action["agentEmail"]
        typesetter_privacy = self.action["agentPrivacy"]
        typesetter_assign_date = self.action["actionDate"]

        if typesetter_cod:
            typesetter = account_get_or_create_check_correspondence(
                self.journal.code.lower(),
                typesetter_cod,
                typesetter_lastname,
                typesetter_firstname,
                typesetter_email,
                typesetter_privacy,
                self.connection,
            )
            if not typesetter.check_role(self.journal, "typesetter", staff_override=False):
                typesetter.add_account_role("typesetter", self.journal)

            logger.debug(f"Assign typ: {typesetter.last_name} {typesetter.first_name} onto {self.article.pk}")
            request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
            request.user = typesetter

            with freezegun.freeze_time(
                rome_timezone.localize(typesetter_assign_date),
            ):
                typesetting_assignment = AssignTypesetter(
                    article=self.article,
                    typesetter=typesetter,
                    request=request,
                ).run()
                self.article.save()
                logger.debug(f"typesetting assignment {typesetting_assignment=}")

            # JCOM and JCOMAL
            # for both published or pending paper
            # the A.data_figure_files are copied as SF in AW.supplementary_files_at_acceptance
            # > sf = []
            # > for f in self.article.data_figure_files.all():
            # >   sf.append(SupplementaryFile.objects.create(file=f))

            self.article.refresh_from_db()
            return typesetter


class TYP_UPLOADS_FOR_PM(BaseActionManager):  # noqa N801
    """Manages wjapp action "typesetter uploads for production manager"."""

    def run(self):

        noop_cases = {
            ("JCOM_003A_0417", 3, 266295),
            ("JCOM_012A_0615", 3, 260996),
            ("JCOM_027Y_0615", 3, 260386),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} TYP_UPLOADS_FOR_PM "
                f"for {self.preprintid}/{self.imported_version_num} (- known case)."
            )
            return

        typesetter_uploads_date = self.action["actionDate"]

        # get typesetter file tar.gz
        if self.importfiles:
            source_prod_dj = import_file_manager.ImportFileManager(self).get_production_source()
            # TODO: (delete) source_prod_dj = self.file_fake_source_prod()
        else:
            # empty tar.gz when importfiles is disabled
            # the logic action requires a file
            source_prod_dj = DjangoFile(BytesIO(b""), f"{self.preprintid}.tar.gz")

        # TODO: better "application/gzip"?
        source_prod_dj.content_type = "application/zip"

        fake_request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        ta_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        fake_request.user = ta_assignment.typesetter

        with freezegun.freeze_time(
            rome_timezone.localize(typesetter_uploads_date),
        ):
            TypesettedFilesUpload._check_file_condition = noop_true
            TypesettedFilesUpload._look_for_queries_in_archive = noop_true

            article_with_file = TypesettedFilesUpload(
                typesetter=ta_assignment.typesetter,
                request=fake_request,
                assignment=ta_assignment,
                file_to_upload=source_prod_dj,
                do_create_galleys=False,
            ).run()
            article_with_file.articleworkflow.save()

        # e.g. JCOM_017A_0624
        if self.importfiles:
            # the production source has already been manage in the action
            import_file_manager.ImportFileManager(self).import_version_files(production_version=True)


class Requestproofs(BaseActionManager):
    """Manages wjapp actions "production manager sends to author"."""

    def run(self):
        # Known cases of "dirty data" on wjapp, when this action had no effect.
        # We identify them by a triplet made of preprint id, version number and action-history code
        # (the first two are just human-friendly pointers 🙂).
        noop_cases = {
            ("JCOM_007A_0421", 5, 284286),
            ("JCOM_012A_1020", 4, 281589),
            ("JCOM_013A_1020", 3, 282697),
            ("JCOM_003A_1120", 5, 282339),
            ("JCOM_027A_1120", 7, 281985),
            ("JCOM_001BR_0324", 3, 296043),
            ("JCOM_004N_0724", 5, 299101),
            ("JCOM_001Y_0724", 5, 299357),
            ("JCOM_011A_0123", 8, 291803),
            ("JCOM_001N_0923", 6, 297135),
            ("JCOM_005A_0322", 4, 288477),
            ("JCOM_001N_0321", 6, 285416),
            ("JCOM_003A_0821", 5, 286339),
            ("JCOM_003N_1021", 5, 285322),
            ("JCOM_010N_1021", 4, 285108),
            ("JCOM_012A_1021", 4, 285642),
            ("JCOM_004A_1121", 5, 288627),
            ("JCOM_018A_1021", 3, 285170),
            ("JCOM_023A_1020", 3, 282928),
            ("JCOM_007A_1120", 6, 281952),
            ("JCOM_006A_0118", 3, 268513),
            ("JCOM_006A_0418", 6, 270334),
            ("JCOM_010A_0718", 4, 272266),
            ("JCOM_003C_0818", 2, 269513),
            ("JCOM_004C_1118", 5, 270687),
            ("JCOM_002C_0317", 5, 265426),
            ("JCOM_001CR_0517", 3, 265350),
            ("JCOM_001E_0617", 2, 265459),
            ("JCOM_001C_0516", 5, 262715),
            ("JCOM_001BR_0616", 2, 262890),
            ("JCOM_004A_0616", 4, 263179),
            ("JCOM_019A_1216", 6, 265656),
            ("JCOMAL_001N_1220", 4, 2678),
            ("JCOMAL_001A_0122", 7, 3301),
            ("JCOMAL_003A_0923", 4, 4686),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PM_SENDS_FOR_PROOF_R "
                f"for {self.preprintid}/{self.imported_version_num} (not uploaded new version - known case)."
            )
            return

        fake_request = create_rich_fake_request(user=None, journal=self.journal, settings=settings)
        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        fake_request.user = typesetting_assignment.typesetter

        skip_duplicate_galley_proofing = (
            request_proofs_dedup_galley_proofing_applies(self.preprintid)
            and typesetting_assignment.round.galleyproofing_set.exists()
        )

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            if skip_duplicate_galley_proofing:
                # See import_exception_list.py: this round already has a GalleyProofing, and
                # wjapp's history repeats this action with no new typesetter upload in between
                # (e.g. after PUM_SENDS_TO_EO resets state without creating a new round - see
                # JCAP_035P_0210/4). Running the full RequestProofs()/GalleyProofing creation
                # here would be fabricated data (no real new typesetting work happened) - just
                # advance the FSM state so later actions (e.g. AU_SENDS_CORRECT) see what they
                # need, reusing the existing round/GalleyProofing instead of duplicating it.
                self.article.articleworkflow.typesetter_submits()
                self.article.articleworkflow.save()
                logger.warning(
                    f"skipped duplicate GalleyProofing for {self.preprintid}/{self.imported_version_num}: "
                    f"reused existing round {typesetting_assignment.round.round_number}, only advanced state"
                )
            else:
                RequestProofs._log_operation = noop
                RequestProofs(
                    assignment=typesetting_assignment,
                    typesetter=typesetting_assignment.typesetter,
                    request=fake_request,
                    workflow=self.article.articleworkflow,
                ).run()

            message_subject = get_setting(
                setting_group_name="wjs_review",
                setting_name="proofreading_request_subject",
                journal=self.article.journal,
            ).processed_value
            communication_utils.log_operation(
                article=self.article,
                message_subject=message_subject,
                message_body="",
                recipients=[
                    self.article.correspondence_author,
                ],
                actor=typesetting_assignment.typesetter,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )

            self.article.refresh_from_db()
            assert self.article.articleworkflow.state == ArticleWorkflow.ReviewStates.PROOFREADING


class PM_SENDS_FOR_PROOF_R(Requestproofs):  # noqa N801
    """Manages wjapp action "production manager sends for proof reading"."""


class PM_REQ_INFO(Requestproofs):  # noqa N801
    """Manages wjapp action "production manager requires information"."""


class AU_SENDS_CORRECT(BaseActionManager):  # noqa N801
    """Manages wjapp action "author sends corrections"."""

    def run(self):
        # E.g. JCOM_017A_0624

        # asked eo, authorized to loose this auann:
        # - JCOM_001A_0323_AUANN000690623.comments ASJ for team final.pdf
        # - JCOM_009A_0119_AUANN000710519 (two auann in the same version, kept the second one)
        # - JCOMAL_003A_0923 resent 3 times, loose first and second keep third final version
        #      - loose JCOMAL_003A_0923_AUANN000330124.docx
        #      - loose JCOMAL_003A_0923_AUANN000340124.jpg
        #
        noop_cases = {
            ("JCOM_023A_0623", 3, 294207),
            ("JCOM_018A_1124", 3, 302240),
            ("JCOM_001A_0323", 3, 291581),
            ("JCOM_002Y_0322", 8, 289194),
            ("JCOM_014N_1021", 4, 286002),
            ("JCOM_004A_0920", 5, 282895),
            ("JCOM_009A_0119", 3, 272016),
            ("JCOM_002N_0219", 5, 272910),
            ("JCOMAL_003A_0923", 4, 4701),
            ("JCOMAL_003A_0923", 4, 4702),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} AU_SENDS_CORRECT "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated - known case)."
            )
            return

        author_send_corrections_date = self.action["actionDate"]
        author_annotation_data = self.read_author_annotation()

        # remember that TR are ordered by -round_number, so the "first" TR is the most recent one (!)
        current_round: TypesettingRound = self.article.typesettinground_set.first()
        old_typesetting_assignment: TypesettingAssignment = current_round.typesettingassignment
        author_proofs: GalleyProofing = current_round.galleyproofing_set.get()  # we always set at most 1 GP per round
        author_proofs.notes = author_annotation_data["documentLayerText"] or " --- "
        author_proofs.save()

        # get author anotation file if it exists
        if self.importfiles and self.journal.code.upper() in ("JCAP", "JHEP", "JCOM", "JCOMAL"):
            (extension, author_annotation_file_dj) = import_file_manager.ImportFileManager(
                self
            ).get_author_annotation_file(author_annotation=author_annotation_data.get("documentLayerID"))
            if author_annotation_file_dj and extension:
                logger.debug(f"Found annotation file {author_annotation_file_dj=}")
                author_annotation_file_dj.content_type = f"application/{extension}"

            if author_annotation_file_dj:
                author_ann_file = files.save_file_to_article(
                    author_annotation_file_dj,
                    self.article,
                    self.article.correspondence_author,
                )
                author_proofs.annotated_files.add(author_ann_file)

        fake_request = create_rich_fake_request(
            user=self.article.correspondence_author, journal=self.journal, settings=settings
        )
        with freezegun.freeze_time(
            rome_timezone.localize(author_send_corrections_date),
        ):
            AuthorSendsCorrections._log_operation = noop
            AuthorSendsCorrections(
                user=self.article.correspondence_author,
                old_assignment=old_typesetting_assignment,
                request=fake_request,
            ).run()

            message_subject = get_setting(
                setting_group_name="email_subject",
                setting_name="subject_notify_typesetter_proofing_changes",
                journal=self.article.journal,
            ).processed_value
            communication_utils.log_operation(
                article=self.article,
                message_subject=message_subject,
                message_body="",
                actor=self.article.correspondence_author,
                recipients=[
                    old_typesetting_assignment.typesetter,
                ],
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )

            self.article.refresh_from_db()

    def read_author_annotation(self):
        """Read author annotation of the imported version."""

        cursor_author_annotation = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # AUANN is only one in the wjapp version
        query_author_annotation = """
SELECT
dl.documentLayerCod,
dl.documentLayerID,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
LEFT JOIN User_Rights ur USING (documentLayerCod)
LEFT JOIN User u USING (userCod)
WHERE
    versioncod=%(imported_version_cod)s
AND ur.userCod=%(agent_cod)s
AND dl.documentLayerType='AUANN'
AND ur.userType='author'
ORDER BY dl.submissionDate DESC
"""

        cursor_author_annotation.execute(
            query_author_annotation,
            {
                "imported_version_cod": self.imported_version_cod,
                "agent_cod": self.action["agentCod"],
                "action_date": str(self.action["actionDate"]),
            },
        )
        if cursor_author_annotation.rowcount == 0:
            logger.error(f"Found {cursor_author_annotation.rowcount} author annotation: {self.preprintid}")
            author_annotation = None
            # added for JHEP/JCAP ST
            raise ValueError("Not found author annotation")
        else:
            # JCOM_023A_0623 has two AUANN in the same version (data error) in any case we take the
            # most recent one
            author_annotation = cursor_author_annotation.fetchone()
            logger.debug(f"{self.preprintid} AUANN: {author_annotation.get('documentLayerCod')}")
        cursor_author_annotation.close()

        return author_annotation

    def get_author_annotation_file(self, document_layer_id):
        """Returns annotation file if exists."""

        annotation_extension = self.extract_annotation_file_extension(document_layer_id)
        if not annotation_extension:
            logger.debug(f"AUANN file extension missing for {document_layer_id}")
            return (None, None)
        response_author_ann_file = self.download_author_annotation_file(document_layer_id, annotation_extension)
        if response_author_ann_file.headers["Content-Length"] != "0":
            return (
                annotation_extension,
                file_from_response(response_author_ann_file, f"{document_layer_id}.{annotation_extension}"),
            )
        else:
            logger.error(f"Empty AUANN file downloaded for {document_layer_id=}!")
            # added for JHEP/JCAP ST
            raise ValueError("Empty AUANN file downloaded")

    def extract_annotation_file_extension(self, document_layer_id):
        """Extract annotation file extension from compressed contribution zip., if exists.

        We either return the extension, meaning that an AUANN file exists and must be download,
        or we return None, meaning that no AUANN file exists and there it no need to download anything.
        """

        # returns the extension of the annotation file, if exists, taken it from the
        # production zip content list. The production zip contains, if it exists, the annotation directory and file.
        # e.g. JCOM_017A_0624:
        # if an AUANN file exists,
        # - it will be called JCOM_017A_0624_AUANN004381124.XXX
        #   where "JCOM_017A_0624_AUANN004381124" is the documentLayerID
        #   (do not confuse with documentLayerCod!)
        # - the path of the file (from the root of the production zip) will be:
        #   AUANN/JCOM_017A_0624_AUANN004381124/JCOM_017A_0624_AUANN004381124.XXX
        # We are interested in the "XXX" extension.
        # The file will be downloaded directly from the main directory, because we prefer not to use, if possible,
        # the production zip for the files, because in case of manual maintenance, the production zip
        # will probably be out of sync (even if we can assume that the extension we are interested in is ok)

        # production zip to download
        file_url = (
            f"{self.url_base}{self.preprintid}/{self.imported_version_num}/"
            f"production/{self.preprintid}.zip&fileType=zip"
        )

        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(
                f"check wjapp login credentials empty production zip: {response.headers['Content-Length']} {file_url=}"
            )
            # added for JHEP/JCAP ST
            raise ValueError("Empty production zip downloaded")
        try:
            files_zip = zipfile.ZipFile(BytesIO(response.content))
        except zipfile.BadZipFile:
            logger.error(f"file not open: {file_url}")
            # added for JHEP/JCAP ST
            raise ValueError("zip file not open")

        for filepath in files_zip.namelist():
            # We assume that the chance of find any other file in the production zip
            # named something similar to "...document_layer_id. ..." is minimal.
            # Note the ending "." in the f-string below:
            if f"{document_layer_id}." in filepath:
                _, extension = os.path.splitext(filepath)
                return extension.lstrip(".")

    def download_author_annotation_file(self, document_layer_id, annotation_extension):
        """Download author annotation file from AUANN directory using the extension found previously.

        Example url:
        JCOM_017A_0624/4/AUANN/JCOM_017A_0624_AUANN004381124/JCOM_017A_0624_AUANN004381124.zip&fileType=zip
        """

        filename_suffix = ""

        # specific cases correction for AUANN filename
        if document_layer_id == "JCOM_001Y_0624_AUANN006670724":
            filename_suffix = ".BVL proofed"

        if document_layer_id == "JCOM_001Y_0624_AUANN001100824":
            filename_suffix = ".proof 20240805.BVL correction"

        if document_layer_id == "JCOM_003N_0823_AUANN003910224":
            filename_suffix = ".-JCOM - Proofreading - 26-02-2024"

        if document_layer_id == "JCOM_013A_1021_AUANN004140722":
            filename_suffix = ".29.22"

        if document_layer_id == "JCOM_015A_1021_AUANN004030422":
            filename_suffix = ".22.22"

        if document_layer_id == "JCOM_002N_0619_AUANN002740819":
            filename_suffix = ".docx"

        if document_layer_id == "JCOM_003A_1217_AUANN002650518":
            filename_suffix = ".odt"

        if document_layer_id == "JCOM_005A_0916_AUANN001140317":
            filename_suffix = ". Number of views according to the established classification."

        if document_layer_id == "JCOM_002A_1215_AUANN000160816":
            filename_suffix = ".02.16"

        if document_layer_id == "JCOMAL_001A_0524_AUANN000691024":
            filename_suffix = ". Obra_ DAMIANA, créditos Fermín Bongiorno"

        file_url = (
            f"{self.url_base}{self.preprintid}/{self.imported_version_num}/AUANN/{document_layer_id}/"
            f"{document_layer_id}{filename_suffix}.{annotation_extension}&fileType={annotation_extension}"
        )

        response = self.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            logger.error(
                f"check wjapp login credentials empty AUANN file downloaded: {response.headers['Content-Length']}"
            )
            # added for JHEP/JCAP ST
            raise ValueError("Empty AUANN file downloaded")
        return response


class PUM_SENDS_TO_TYP(BaseActionManager):  # noqa N801
    """Manages wjapp action "publication manager sends back to typesetter"."""

    # NOTE this action happens after ready for publication
    # e.g. JCOM_008A_0324

    def run(self):

        # for (JCOM_001A_0917, 4): the workaround is necessary for a "missing"
        # ready for publication in version 5, which creates a transition error
        # if there are two PUM_SENDS_TO_TYP in sequence

        noop_cases = {
            ("JCOM_001A_0917", 4, 266532),
            ("JCOM_001A_0917", 5, 266534),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PUM_SENDS_TO_TYP "
                f"for {self.preprintid}/{self.imported_version_num} (- known case)."
            )
            return

        # we need to get the admin user who did the action
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        back_to_typ_date = self.action["actionDate"]
        back_to_typesetter = self.read_pub_manager_annotation()

        old_typesetting_assignment = self.article.typesettinground_set.first().typesettingassignment
        assert back_to_typesetter
        body_pub_manager_annotation = back_to_typesetter["documentLayerText"]
        subject_pub_manager_annotation = back_to_typesetter["documentLayerSubject"]

        with freezegun.freeze_time(
            rome_timezone.localize(back_to_typ_date),
        ):
            HandleEOSendBackToTypesetter(
                workflow=self.article.articleworkflow,
                user=admin,
                old_assignment=old_typesetting_assignment,
                body=body_pub_manager_annotation,
                subject=subject_pub_manager_annotation,
            ).run()

    def read_pub_manager_annotation(self):
        """Read publication manager annotation of the imported version."""

        cursor_pub_manager_annotation = self.connection.cursor(
            buffered=True,
            dictionary=True,
        )

        # After wjapp action PUM_SENDS_TO_TYP the paper goes to the typesetter and when the typesetter uploads,
        # a new version is created. Therefore there is usually only one PUMANN in a version and the condition
        # does not need the agentCod or the actionDate. wjapp allows a duplicate submission of the same
        # annotation though (data error, no check on repeated action - e.g. JCAP_031P_1221/6, JCAP_052P_1219/6,
        # JCAP_065P_1112/5); same as read_author_annotation()'s AUANN handling below, in that case we take the
        # most recent one (which is also what wjapp's own UI shows).
        query_pub_manager_annotation = """
SELECT
dl.documentLayerCod,
dl.documentLayerID,
dl.documentLayerSubject,
dl.documentLayerText
FROM Document_Layer dl
WHERE
    versioncod=%(imported_version_cod)s
AND dl.documentLayerType='PUMANN'
ORDER BY dl.submissionDate DESC
"""

        cursor_pub_manager_annotation.execute(
            query_pub_manager_annotation,
            {
                "imported_version_cod": self.imported_version_cod,
            },
        )
        if cursor_pub_manager_annotation.rowcount == 0:
            logger.error(
                f"Found {cursor_pub_manager_annotation.rowcount} publication manager annotation: "
                f"{self.preprintid}/{self.imported_version_num}"
            )
            pub_manager_annotation = None
            # added for JHEP/JCAP ST
            raise ValueError("Not found publication manager annotation")
        else:
            pub_manager_annotation = cursor_pub_manager_annotation.fetchone()
            logger.debug(f"{self.preprintid} PUMANN: {pub_manager_annotation.get('documentLayerCod')}")
            self.imported_document_layer_cod_list.append(pub_manager_annotation.get("documentLayerCod"))

        cursor_pub_manager_annotation.close()

        return pub_manager_annotation


@dataclass
class DeclareReadyForPublication(BaseActionManager):
    """Manages wjapp action "article declared ready for publication"."""

    ready_for_publication_agent: Account = None

    def run(self):

        noop_cases = {
            ("JCOM_005A_0621", 4, 283684),
            ("JCOM_005A_0621", 4, 283685),
            ("JCOM_001N_0321", 6, 285546),
            ("JCOM_005A_1221", 6, 286343),
            ("JCOM_018A_1021", 3, 285172),
            ("JCOM_023A_1020", 3, 282974),
            ("JCOM_001BR_1018", 3, 270181),
            ("JCOM_001CR_0517", 3, 265365),
            ("JCOM_001BR_0616", 2, 262893),
            ("JCOM_001A_0917", 4, 266531),
            ("JCOM_011A_0123", 6, 291147),
            ("JCOM_005C_1118", 4, 270710),
            ("JCOMAL_001A_0622", 5, 3199),
            ("JCOMAL_001A_1224", 5, 5999),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping repeated action {self.action['actHistCod']} DeclareReadyForPublication "
                f"for {self.preprintid}/{self.imported_version_num} (duplicated/wrong logic - known case)."
            )
            return

        self.article.articleworkflow.production_flag_no_queries = True
        self.article.articleworkflow.production_flag_no_checks_needed = True
        self.article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            # use default timeline message. Wjapp message imported as correspondence
            # e.g. JCOM_017A_0524
            ReadyForPublication(workflow=self.article.articleworkflow, user=self.ready_for_publication_agent).run()
        self.article.refresh_from_db()
        self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION


@dataclass
class PM_PUBLISHES(DeclareReadyForPublication):  # noqa N801
    """Manages wjapp action "eo declares ready for publication"."""

    def __post_init__(self):
        """Set the specific data for eo agent, used typesetter"""
        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        assert is_article_typesetter(self.article.articleworkflow, typesetting_assignment.typesetter)
        self.ready_for_publication_agent = typesetting_assignment.typesetter


@dataclass
class TYP_PUBLISHES(DeclareReadyForPublication):  # noqa N801
    """Manages wjapp action "typesetter declares ready for publication"."""

    def __post_init__(self):
        """Set the specific data typesetter agent"""
        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        assert is_article_typesetter(self.article.articleworkflow, typesetting_assignment.typesetter)
        self.ready_for_publication_agent = typesetting_assignment.typesetter


@dataclass
class AU_PUBLISHES(DeclareReadyForPublication):  # noqa N801
    """Manages wjapp action "author declares ready for publication"."""

    def __post_init__(self):
        """Set the specific data for author agent"""
        self.ready_for_publication_agent = self.article.correspondence_author


@dataclass
class PM_SENDS_TO_TYP(BaseActionManager):  # noqa N801
    """Manages wjapp action eo sends back to typesetter."""

    # this action is not in the logic of wjs. It is added a message in the timeline for the imported papers.
    # other related messages are imported with the general correspondence
    def run(self):
        # we need to get the admin user who did the action
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        typesetting_assignment = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            communication_utils.log_operation(
                article=self.article,
                message_subject="back to typesetter",
                message_body="",
                recipients=[typesetting_assignment.typesetter],
                actor=admin,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )


@dataclass
class PM_PRE_PUBLISHES(BaseActionManager):  # noqa N801
    """Manages wjapp action Publication Manager prepares for publication.

    this action is not in the logic of wjs. It is added a message in the timeline for the imported papers.
    other related messages are imported with the general correspondence
    """

    def run(self):
        # we need to get the pm user who did the action
        pm_cod = self.action["agentCod"]
        pm_lastname = self.action["agentLastname"]
        pm_firstname = self.action["agentFirstname"]
        pm_email = self.action["agentEmail"]
        pm_privacy = self.action["agentPrivacy"]

        pm = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            pm_cod,
            pm_lastname,
            pm_firstname,
            pm_email,
            pm_privacy,
            self.connection,
        )

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            communication_utils.log_operation(
                article=self.article,
                message_subject="Publication Manager prepares for publication",
                message_body="",
                recipients=[pm],
                actor=pm,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )


@dataclass
class PUB_PUBLISHES(BaseActionManager):  # noqa N801
    """Manages wjapp action publisher publishes."""

    # this action is not in the logic of wjs. It is added a message in the timeline for the imported papers.
    # other related messages are imported with the general correspondence
    def run(self):
        # we need to get the pub user who did the action
        pub_cod = self.action["agentCod"]
        pub_lastname = self.action["agentLastname"]
        pub_firstname = self.action["agentFirstname"]
        pub_email = self.action["agentEmail"]
        pub_privacy = self.action["agentPrivacy"]

        pub = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            pub_cod,
            pub_lastname,
            pub_firstname,
            pub_email,
            pub_privacy,
            self.connection,
        )

        with freezegun.freeze_time(
            rome_timezone.localize(self.action["actionDate"]),
        ):
            self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
            self.article.articleworkflow.save()
            self.article.stage = submission_models.STAGE_PUBLISHED
            self.article.save()
            communication_utils.log_operation(
                article=self.article,
                message_subject="publisher publishes",
                message_body="",
                recipients=[pub],
                actor=pub,
                verbosity=Message.MessageVerbosity.TIMELINE,
                flag_as_read=True,
                flag_as_read_by_eo=True,
            )

            if self.importfiles:
                import_file_manager.ImportFileManager(self).get_publication_pdf()
                import_file_manager.ImportFileManager(self).get_publication_attachments()
                import_file_manager.ImportFileManager(self).move_production_source_to_pub_galleys_source_file()


@dataclass
class ACC_CPRGHT_TRNSFR(BaseActionManager):  # noqa N801
    """Wjapp action to accept copyright after acceptance."""

    # this action which in wjapp is done after acceptance is not in the logic of wjs
    # where the copyright is accepted at submission
    def run(self):
        pass


@dataclass
class PUM_SENDS_TO_EO(BaseActionManager):  # noqa N801
    """Wjapp action to send article from publicaton manager to eo after ready for publication."""

    # PUM_SENDS_TO_EO in wjapp is done after ready for publication but does not
    # require typesetter upload or author sends proof. So in wjs must only reset
    # ready for publication to permit another ready for publication action
    # e.g. JCOM_015A_0224, JCOM_001E_0924
    def run(self):

        noop_cases = {
            ("JCOM_018A_1021", 4, 285245),
            ("JCOM_023A_1020", 4, 283085),
            ("JCOM_001CR_0517", 4, 265366),
            ("JCOM_001BR_0616", 3, 262894),
            ("JCOM_011A_0123", 7, 291170),
            ("JCOM_005C_1118", 5, 270711),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PUM_SENDS_TO_EO "
                f"for {self.preprintid}/{self.imported_version_num} ( - known case)."
            )
            return

        self.article.articleworkflow.state = ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
        self.article.articleworkflow.save()
        ta = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        ta.completed = None
        ta.save()


@dataclass
class PUB_REG_CREF_OK(BaseActionManager):  # noqa N801
    """Wjapp action Crossref notification (registration ok/error)."""

    # this action of wjapp is managed in other way in the logic of wjs
    # so is skipped. i.e. JCOM_006A_0418, JCOM_003C_0818
    def run(self):
        pass


@dataclass
class PUB_REG_CREF_REQ(BaseActionManager):  # noqa N801
    """Wjapp action Crossref notification (request)."""

    # this action of wjapp is managed in other way in the logic of wjs
    # so is skipped. i.e. JCOM_006A_1217
    def run(self):
        pass


@dataclass
class PUB_REG_CREF_ERR(BaseActionManager):  # noqa N801
    """Wjapp action Crossref notification (error)."""

    # this action of wjapp is managed in other way in the logic of wjs
    # so is skipped. i.e. JCOM_006A_1217
    def run(self):
        pass


@dataclass
class PM_REQ_ED_CHECK(BaseActionManager):  # noqa N801
    """Wjapp action EO requires editor check."""

    # This action of wjapp is not supported by wjs
    # but we need to deal with each single case,
    # because they may need special treatment in other places
    # (e.g. in the main loop, in ED_RESTARTS_REV, in TYP_UPLOADS_FOR_PM, etc.)
    def run(self):

        noop_cases = {
            ("JCOM_003A_0417", 3, 266298),
            ("JCOM_012A_0615", 3, 260997),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PM_REQ_ED_CHECK "
                f"for {self.preprintid}/{self.imported_version_num} (managed known case)."
            )
            pass
        else:
            logger.error(f"PM_REQ_ED_CHECK not managed for {self.preprintid} {self.article.id}")
            # added for JHEP/JCAP ST
            raise ValueError("PM_REQ_ED_CHECK not managed")


@dataclass
class ED_APPROVES(BaseActionManager):  # noqa N801
    """Wjapp action to accept author`s proof changes."""

    # this action which in wjapp is done after acceptance is not in the logic of wjs
    # the information about the action remains in the correspondence
    def run(self):
        pass


@dataclass
class ED_RESTARTS_REV(BaseActionManager):  # noqa N801
    """Wjapp action editor does not approve author`s proof changes. Revision requested to author."""

    # This action of wjapp is not supported by wjs
    # but we need to deal with each single case,
    # because they may need special treatment in other places
    # (e.g. in the main loop, in ED_RESTARTS_REV, in TYP_UPLOADS_FOR_PM, etc.)
    def run(self):

        noop_cases = {("JCOM_003A_0417", 3, 266304), ("JCOM_012A_0615", 3, 261008)}
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} ED_RESTARTS_REV "
                f"for {self.preprintid}/{self.imported_version_num} (managed known case)."
            )
            pass
        else:
            logger.error(f"ED_RESTARTS_REV not managed for {self.preprintid} {self.article.id}")
            # added for JHEP/JCAP ST
            raise ValueError("ED_RESTARTS_REV not managed")


@dataclass
class PM_UPLOADS_FORMAT_V(BaseActionManager):  # noqa N801
    """Wjapp action PM uploads version."""

    # This action of wjapp is not supported by wjs
    # but we need to deal with each single case,
    # because they may need special treatment in other places
    # (e.g. in the main loop, in ED_RESTARTS_REV, in TYP_UPLOADS_FOR_PM, etc.)
    def run(self):

        noop_cases = {
            ("JCOM_027Y_0615", 4, 260632),
        }
        if (self.preprintid, self.imported_version_num, self.action["actHistCod"]) in noop_cases:
            logger.warning(
                f"Skipping action {self.action['actHistCod']} PM_UPLOADS_FORMAT_V "
                f"for {self.preprintid}/{self.imported_version_num} (managed known case)."
            )
            pass
        else:
            logger.error(f"PM_UPLOADS_FORMAT_V not managed for {self.preprintid} {self.article.id}")
            # added for JHEP/JCAP ST
            raise ValueError("PM_UPLOADS_FORMAT_V not managed")


@dataclass
class ADM_SETS_PUB_SETINGS(BaseActionManager):  # noqa N801
    """Wjapp action to set publication settings."""

    # this action is not in the logic of wjs
    # the information about the action remains in the correspondence
    def run(self):
        pass


@dataclass
class ADMIN_CONFIRMS_TA(BaseActionManager):  # noqa N801
    """Wjapp action admin confirms TA."""

    def run(self):
        """Admin confirms TA."""

        # we need to get the admin user to log it
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]
        admin_opens_appeal_date = self.action["actionDate"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        # Note: this action is not visible in the wjs timeline
        with freezegun.freeze_time(
            rome_timezone.localize(admin_opens_appeal_date),
        ):
            logger.debug(f"Admin {admin} Confirm production readiness {self.article.pk}")

            if self.article.submission_data.access_mode.code == "oa-transformative-agreement":
                self.article.articleworkflow.system_verifies_production_requirements()
                self.article.articleworkflow.save()
            else:
                logger.debug(f"Admin {admin} confirms TA skipped for {self.article.pk}")


@dataclass
class ADMIN_REJECTS_TA(BaseActionManager):  # noqa N801
    """Wjapp action admin rejects TA."""

    def run(self):
        """Admin rejects TA."""

        # we need to get the admin user to log it
        admin_cod = self.action["agentCod"]
        admin_lastname = self.action["agentLastname"]
        admin_firstname = self.action["agentFirstname"]
        admin_email = self.action["agentEmail"]
        admin_privacy = self.action["agentPrivacy"]
        admin_opens_appeal_date = self.action["actionDate"]

        admin = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            admin_cod,
            admin_lastname,
            admin_firstname,
            admin_email,
            admin_privacy,
            self.connection,
        )

        if not has_eo_role(admin):
            eo_group, _ = Group.objects.get_or_create(name=constants.EO_GROUP)
            logger.debug(f"Admin {admin} added group {constants.EO_GROUP}")
            admin.groups.add(eo_group)

        with freezegun.freeze_time(
            rome_timezone.localize(admin_opens_appeal_date),
        ):
            logger.debug(f"Admin {admin} Confirm production readiness {self.article.pk}")

            # Note: this action is not visible in the wjs timeline
            # for the moment 3 cases:
            # 1. if the imported paper is TA and is in a final state after the acceptance
            #    this rejection action can act as to ADMIN_CONFIRMS_TA otherwise the workflow of the paper
            #    is blocked in this point
            # 2. if the imported paper is not TA in wjapp, when is loaded in wjs the paper is not blocked.
            #    Therefore this action can be skipped.
            # 3. TODO: (waiting for the final implementation of this workflow in wjs)
            #    if the imported paper is in state accepted, is TA, in any case this action can be skipped
            #    because in wjs
            #    the paper is already blocked
            if self.article.submission_data.access_mode.code == "oa-transformative-agreement":
                self.article.articleworkflow.system_verifies_production_requirements()
                self.article.articleworkflow.save()
            else:
                logger.debug(f"Admin {admin} rejects TA skipped for {self.article.pk}")
