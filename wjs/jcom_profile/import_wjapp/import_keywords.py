"""Normalize and merge the editor keywords read from wjapp.

Kept apart from ``import_logic`` on purpose: importing ``import_logic`` monkeypatches ``wjs_review`` classes
process-wide (e.g. ``SendProductionXMLToPublisher.run``, ``EvaluateReview._log_decline``) to silence notifications
during the wjapp import replay. These helpers are pure, so they can be used and tested without that side effect.
"""

from typing import Iterable

from utils.logger import get_logger

logger = get_logger(__name__)

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


def merge_editor_keywords(journal_code: str, editor_keywords: Iterable[dict]) -> dict[str, int]:
    """Collapse the wjapp keyword rows of an editor into one weight per normalized keyword.

    The same keyword can appear more than once for an editor (also because different raw strings normalize to the
    same word), but an editor can have a keyword only once (StaffKeyword is unique per parameters and keyword).
    The highest weight wins and a warning is logged for each merged keyword. The first-seen order is kept.

    Args:
        journal_code: the journal code, used to normalize the keyword strings.
        editor_keywords: wjapp rows with the ``keywordName`` and ``keywordWeight`` keys.

    Returns:
        A mapping of normalized keyword word to weight.
    """
    merged: dict[str, int] = {}
    for row in editor_keywords:
        kwd_word = normalize_keyword(journal_code, row["keywordName"].strip())
        kwd_weight = row["keywordWeight"]
        if kwd_word in merged:
            logger.warning(
                f'Keyword "{kwd_word}" found more than once for the same editor on {journal_code} '
                f"(weights {merged[kwd_word]} and {kwd_weight}): keeping the highest"
            )
            kwd_weight = max(merged[kwd_word], kwd_weight)
        merged[kwd_word] = kwd_weight
    return merged
