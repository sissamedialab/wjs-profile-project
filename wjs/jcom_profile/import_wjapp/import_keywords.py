"""Normalize and merge the editor keywords read from wjapp.

Kept apart from ``import_logic`` on purpose: importing ``import_logic`` monkeypatches ``wjs_review`` classes
process-wide (e.g. ``SendProductionXMLToPublisher.run``, ``EvaluateReview._log_decline``) to silence notifications
during the wjapp import replay. ``normalize_keyword``/``merge_editor_keywords`` are pure and can be used and tested
without that side effect. ``get_or_create_import_keyword`` does touch the DB, but has no dependency on
``import_logic`` either, so it is kept here too rather than duplicated by its three callers (the article importer's
``set_keywords``, ``EditorAssignmentAction.set_editor_parameters`` and the standalone editor importer's
``sync_editor`` - see wjs/specs#3477).
"""

from typing import Iterable, Optional

from django.utils import timezone
from journal.models import Journal
from submission import models as submission_models
from utils.logger import get_logger

logger = get_logger(__name__)

# Journals whose wjapp database is known to have the Keyword_KSubgroup/KSubgroup tables (wjapp's
# own keyword group/subgroup hierarchy - see wjs/specs#3477). Only JCAP has been verified so far;
# JHEP also uses WJS's hierarchical_keywords feature and may have the same wjapp structure,
# but that has not been checked against their own wjapp databases - extend this set once verified,
# rather than assuming. A journal not in this set simply never gets a group assigned on import
# (same as before this fix), it does not risk a SQL error against a missing wjapp table.
WJAPP_HIERARCHICAL_KEYWORD_JOURNALS = {"JCAP"}


def keyword_subgroup_sql(journal_code: str, keyword_cod_column: str) -> str:
    """Build the SQL fragment selecting wjapp's KSubgroup.subgroupName for a keyword, or NULL.

    `keyword_cod_column` is the fully-qualified column (e.g. ``"kw.keywordCod"``) to correlate
    against, since callers alias the ``Keyword`` table differently. Returns a scalar subquery
    (at most one row even if a keyword were ever linked to more than one subgroup) rather than a
    JOIN, so it can never duplicate the caller's base rows.
    """
    if journal_code.upper() not in WJAPP_HIERARCHICAL_KEYWORD_JOURNALS:
        return "NULL"
    return f"""(
        SELECT sg.subgroupName
        FROM Keyword_KSubgroup ks
        JOIN KSubgroup sg ON sg.subgroupCod = ks.subgroupCod
        WHERE ks.keywordCod = {keyword_cod_column}
        LIMIT 1
    )"""


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


def merge_editor_keywords(journal_code: str, editor_keywords: Iterable[dict]) -> dict[str, dict]:
    """Collapse the wjapp keyword rows of an editor into one weight/availability/group per normalized keyword.

    The same keyword can appear more than once for an editor - also because different raw strings normalize to
    the same word, or because wjapp has more than one keywordCod sharing the same keywordName (e.g. one
    available, one not, see wjs/specs#3449) - but an editor can have a keyword only once (StaffKeyword is unique
    per parameters and keyword). The highest weight wins and a warning is logged for each merged keyword.
    Availability is "any wins": the merged keyword is available if any of its wjapp rows is. The group name is
    the first non-null one seen (in practice a keyword belongs to a single wjapp subgroup, see wjs/specs#3477).
    The first-seen order is kept.

    Args:
        journal_code: the journal code, used to normalize the keyword strings.
        editor_keywords: wjapp rows with the ``keywordName``, ``keywordWeight`` and (optional) ``keywordAvailable``,
            ``subgroupName`` keys.

    Returns:
        A mapping of normalized keyword word to ``{"weight": int, "available": bool, "group_name": str | None}``.
    """
    merged: dict[str, dict] = {}
    for row in editor_keywords:
        kwd_word = normalize_keyword(journal_code, row["keywordName"].strip())
        kwd_weight = row["keywordWeight"]
        kwd_available = bool(row.get("keywordAvailable"))
        kwd_group_name = row.get("subgroupName")
        if kwd_word in merged:
            logger.warning(
                f'Keyword "{kwd_word}" found more than once for the same editor on {journal_code} '
                f"(weights {merged[kwd_word]['weight']} and {kwd_weight}): keeping the highest"
            )
            kwd_weight = max(merged[kwd_word]["weight"], kwd_weight)
            kwd_available = merged[kwd_word]["available"] or kwd_available
            kwd_group_name = merged[kwd_word]["group_name"] or kwd_group_name
        merged[kwd_word] = {"weight": kwd_weight, "available": kwd_available, "group_name": kwd_group_name}
    return merged


def get_or_create_import_keyword(
    journal: Journal,
    word: str,
    available: bool,
    group_name: Optional[str] = None,
) -> tuple[submission_models.Keyword, bool]:
    """Get or create a WJS Keyword for import, linking it to the journal and setting its initial state.

    Shared by all three keyword-creation call sites that touch wjapp data (the article importer's
    ``set_keywords``, ``EditorAssignmentAction.set_editor_parameters`` and the standalone editor
    importer's ``sync_editor``), so a keyword is always created/linked the same way regardless of which
    import path first encounters it (wjs/specs#3477).

    The journal link is set unconditionally, even for an already-existing keyword - mirroring the
    article importer's own long-standing behaviour (a pre-existing keyword might so far only be linked
    to another journal). ``add()`` is idempotent, so this is safe on every call.

    ``available`` only matters on actual creation (passed as ``defaults``): mirrors Janeway core's own
    convention of creating new ad-hoc keywords deactivated until reviewed and activated by the journal
    admin (submission/views.py). An already existing keyword's visibility is never touched here, since
    it may have since been reviewed/toggled by an EO.

    ``group_name`` is wjapp's ``KSubgroup.subgroupName``, matched 1:1 against a top-level WJS
    ``KeywordGroup.name`` (confirmed exact match, see wjs/specs#3477). An unknown group name is logged
    and skipped rather than raised, so one odd/unmapped wjapp subgroup can never abort an import batch.
    An already existing keyword's group is backfilled if it doesn't have one yet, but never overwritten
    once set - this is what lets a word that some other journal's import happens to create first without
    a group (JCOM/JCOMAL have no group concept at all; JQuant has keyword groups too, but never had a
    wjapp database - it started directly on WJS - so it has no subgroup schema to query, see
    WJAPP_HIERARCHICAL_KEYWORD_JOURNALS above, and currently supplies no group_name either) still pick
    up JCAP's own, authoritative group once JCAP's import encounters that same word, instead of
    permanently losing it (see wjs/specs#3477 discussion).
    """
    group = None
    if group_name:
        try:
            group = submission_models.KeywordGroup.objects.get(name=group_name, parent_group__isnull=True)
        except submission_models.KeywordGroup.DoesNotExist:
            logger.error(f'Unknown wjapp keyword group "{group_name}" for keyword "{word}" - leaving ungrouped')

    defaults = {"deactivated": None if available else timezone.now()}
    if group is not None:
        defaults["group"] = group

    keyword, created = submission_models.Keyword.objects.get_or_create(word=word, defaults=defaults)

    if not created and group is not None and keyword.group is None:
        keyword.group = group
        keyword.save(update_fields=["group"])

    journal.keywords.add(keyword)
    return keyword, created
