#!/usr/bin/env python3
"""
AgribalyseAutoderive - automated derivation of bounded Agribalyse impacts
========================================================================

Derives, without manual arbitration, the environmental impacts of a food
inventory whose products may each match several Agribalyse references: it
matches, keeps every plausible candidate, bounds the result between a best
and a worst scenario, and propagates uncertainty by Monte Carlo. Developed
for the IESF Knowledge Convention life cycle assessment, but not specific
to it.

Reads a normalized inventory (CSV or Excel) and computes environmental
impacts using the Agribalyse v3.x database.

USAGE
-----
    python agribalyse_autoderive.py --inventory <file> --agribalyse <file> [options]

INVENTORY FORMAT
----------------
The input inventory must be a CSV or Excel file.

  Required columns:
    Product               Name of the elementary product (string)
    Mass_kg               Total mass in kg (float, positive)
    Match_Confidence      Matching confidence: HIGH / MEDIUM / LOW

  The former French names (Produit_Elementaire, Masse_Totale_kg,
  Match_Confiance and the traceability columns) are still accepted and
  renamed on load.

  Optional, to refine the mass uncertainty:
    Mass_CV               Coefficient of variation of the mass, as a number.
                          Overrides the value derived from Match_Confidence for
                          that row. Use it when the plausible range of a mass is
                          known from a reference: with a lognormal, a CV of c
                          places the 95th percentile at roughly
                          exp(1.645*s - s*s/2) times the mean, s = sqrt(ln(1+c*c)).
                          Rows that leave it empty keep the confidence-level default.

  Agribalyse assignment – ONE of:
    Agribalyse_Code       Single Agribalyse code per row (int or str).
                          Use this for *expanded* format: one row per candidate.
    Agribalyse_Codes      Semicolon-separated codes on one row, e.g. "5001;5011".
                          Use this for *compact* format: script expands automatically.

  Optional traceability columns (passed through to output):
    N_People, Mass_per_Person_g, Pieces_per_Person,
    Mass_per_Piece_g, Volume_per_Person_mL, Density_kg_L, Estimation_Source

  Any other column is treated as a "context" column (kept as-is in output).
  The first context column drives Monte Carlo grouping (override: --group-col).

OUTPUT
------
  Excel with 4 sheets:
    All_candidates    All candidates (1 row per product × Agribalyse code)
    Best_case         Lowest EF score per product
    Worst_case        Highest EF score per product
    MC_distributions  Monte Carlo raw distributions (N × groups × scenarios)

EXAMPLES
--------
  # Basic run
  python agribalyse_autoderive.py -i inventory.csv -a agribalyse.xlsx

  # Specify output and grouping column
  python agribalyse_autoderive.py -i inventory.csv -a agribalyse.xlsx \\
      -o results.xlsx --group-col WE --n-mc 2000

  # Exact keyword search
  python agribalyse_autoderive.py -a agribalyse.xlsx --search pain complet

  # Fuzzy search – handles typos, word order, partial phrases
  python agribalyse_autoderive.py -a agribalyse.xlsx --search "legumes cuisines" --fuzzy
  python agribalyse_autoderive.py -a agribalyse.xlsx --search vienoiserie --fuzzy --threshold 70

FUZZY MATCHING
--------------
  Use ``--fuzzy`` (CLI) or the ``Agribalyse_Fuzzy: True`` column (inventory) to
  activate fuzzy search via ``rapidfuzz`` (combined token_set_ratio + WRatio).  This handles:
    - Typos and OCR errors:   "vienoiserie" → Viennoiserie
    - Word-order variants:    "expresso cafe" → Café expresso
    - Partial phrases:        "legumes" → Ratatouille cuisinée  (token subset)
    - Accent differences:     handled automatically in both modes
  The default threshold is 60 (0–100 scale). Raise it for more precision,
  lower it for broader recall.  Per-row overrides: ``Agribalyse_Threshold``.
  When exact search yields nothing, the script auto-retries with fuzzy as a
  fallback and emits a warning with the top match score.
"""

import argparse
import sys
import unicodedata
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

try:
    from rapidfuzz import fuzz as _rfuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    import difflib as _difflib
    _HAS_RAPIDFUZZ = False

# ──────────────────────────────────────────────────────────────────────────────
# 1.  CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

VERSION = "1.1.0"

# Standard Agribalyse XLSX column names (sheet 'Synthese', header row index 2)
AGRIBALYSE_COLS = [
    'Code_AGB', 'Code_CIQUAL', 'Groupe', 'SousGroupe', 'Nom_FR', 'LCI_Name',
    'code_saison', 'code_avion', 'Livraison', 'Approche_emballage', 'Preparation', 'DQR',
    'Score_EF_mPt', 'CC_kgCO2eq', 'Ozone_kgCVC11eq', 'Rayonnements_kBqU235eq',
    'Ozone_photo_kgNMVOCeq', 'Particules_diseaseInc', 'Toxicol_nonCancer_CTUh',
    'Toxicol_Cancer_CTUh', 'Acidification_molHeq', 'Eutro_eauDouce_kgPeq',
    'Eutro_marine_kgNeq', 'Eutro_terrestre_molNeq', 'Ecotox_CTUe', 'UtilSol_Pt',
    'Eau_m3depriv', 'Energie_MJ', 'Mineraux_kgSbeq',
    'CC_biogenique_kgCO2eq', 'CC_fossile_kgCO2eq', 'CC_affectSol_kgCO2eq',
]

IMPACT_COLS = [
    'Score_EF_mPt', 'CC_kgCO2eq', 'Ozone_kgCVC11eq', 'Rayonnements_kBqU235eq',
    'Ozone_photo_kgNMVOCeq', 'Particules_diseaseInc', 'Toxicol_nonCancer_CTUh',
    'Toxicol_Cancer_CTUh', 'Acidification_molHeq', 'Eutro_eauDouce_kgPeq',
    'Eutro_marine_kgNeq', 'Eutro_terrestre_molNeq', 'Ecotox_CTUe', 'UtilSol_Pt',
    'Eau_m3depriv', 'Energie_MJ', 'Mineraux_kgSbeq',
    'CC_biogenique_kgCO2eq', 'CC_fossile_kgCO2eq', 'CC_affectSol_kgCO2eq',
]

# Inventory columns with known fixed semantics.
# The canonical names are English; the original French names are accepted as
# aliases and renamed on load, so inventories written for earlier versions of
# this script keep working unchanged.
REQUIRED_COLS = ['Product', 'Mass_kg', 'Match_Confidence']

COLUMN_ALIASES = {
    'Produit_Elementaire': 'Product',
    'Masse_Totale_kg':     'Mass_kg',
    'Match_Confiance':     'Match_Confidence',
    'Nb_Personnes':        'N_People',
    'Masse_par_Pers_g':    'Mass_per_Person_g',
    'Nb_Pieces_par_Pers':  'Pieces_per_Person',
    'Masse_par_Piece_g':   'Mass_per_Piece_g',
    'Volume_mL_par_Pers':  'Volume_per_Person_mL',
    'Densite_kg_L':        'Density_kg_L',
    'Source_Estimation':   'Estimation_Source',
}

CODE_COLS = ['Agribalyse_Code', 'Agribalyse_Codes']   # singular=expanded, plural=compact

SEARCH_COLS = [
    'Agribalyse_Keywords', 'Agribalyse_Exclude',
    'Agribalyse_Fuzzy',     # bool – per-row: use fuzzy search instead of exact
    'Agribalyse_Threshold', # float – per-row: fuzzy threshold override (0–100)
]

# Default fuzzy matching threshold (0–100; 60 gives good recall with reasonable precision)
DEFAULT_FUZZY_THRESHOLD = 60.0

TRACEABILITY_COLS = [
    'N_People', 'Mass_per_Person_g', 'Pieces_per_Person',
    'Mass_per_Piece_g', 'Volume_per_Person_mL', 'Density_kg_L', 'Estimation_Source',
]

# Output columns added by the script from Agribalyse lookup
AGB_META_COLS = [
    'Agribalyse_Code', 'Agribalyse_Nom',
    'Agribalyse_Groupe', 'Agribalyse_SousGroupe', 'Agribalyse_DQR',
]

# DQR → CV mapping (PEF / EF3.1 methodology)
_DQR_POINTS = [1.0, 2.0, 3.0, 4.0, 5.0]
_CV_POINTS  = [0.05, 0.10, 0.20, 0.35, 0.50]

# Confidence level → mass CV (uncertainty on estimated masses).
# These three levels are a coarse default. When the plausible range of a mass is
# known from an external reference, give the coefficient of variation directly in
# an optional `Mass_CV` column of the inventory: it overrides the level below for
# that row. See MASS_CV_COL.
CONFIDENCE_CV = {'HIGH': 0.10, 'MEDIUM': 0.20, 'LOW': 0.35}

MASS_CV_COL = 'Mass_CV'   # optional per-row override of the mass CV


# ──────────────────────────────────────────────────────────────────────────────
# 2.  AGRIBALYSE LOADING
# ──────────────────────────────────────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    """Remove accents from a string for accent-insensitive search.

    'Café expresso' → 'cafe expresso'
    Uses Unicode NFD decomposition + Mn category removal (stdlib only).
    """
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


def _fuzzy_score(query: str, target: str) -> float:
    """Fuzzy similarity score (0–100) between query and target product name.

    Both strings are accent-normalised and lowercased before comparison.

    Uses ``max(rapidfuzz.fuzz.token_set_ratio, rapidfuzz.fuzz.WRatio)`` when
    available (recommended), taking the best of both strategies.
    ``token_set_ratio`` excels for subset/word-order queries (e.g. single-word
    queries against verbose product names), while ``WRatio`` handles partial
    multi-word phrases and typos better.  Falls back to
    ``difflib.SequenceMatcher`` otherwise.

    The combined scorer handles:
      - Subset queries:       "cafe" matches "Café expresso, non sucré…"  → 100
      - Multi-word phrases:   "cafe filtre" matches "Café expresso…"      →  86
      - Word order:           "rouge vin" matches "Vin rouge"             → 100
      - Typos:                "vienoiserie" matches "Viennoiserie…"       →  82
      - Unrelated terms:      "chocolat" vs "Vin rouge"                   →  12
    """
    q = _strip_accents(query).lower().strip()
    t = _strip_accents(target).lower().strip()
    if not q or not t:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return float(max(_rfuzz.token_set_ratio(q, t), _rfuzz.WRatio(q, t)))
    else:
        # stdlib fallback – less powerful (no token reordering)
        return _difflib.SequenceMatcher(None, q, t).ratio() * 100.0


def _normalize_code(code) -> str:
    """Return a canonical string representation of an Agribalyse code.

    Handles:
      - integer  5214     → "5214"
      - float    5214.0   → "5214"
      - str      "13024_1"→ "13024_1"  (variant codes with underscore preserved)
      - str      "5214.0" → "5214"
    """
    s = str(code).strip()
    # Remove trailing .0 only when the stem is a plain integer
    if s.endswith('.0'):
        stem = s[:-2]
        if stem.lstrip('-').isdigit():
            s = stem
    return s


def load_agribalyse(path: str) -> pd.DataFrame:
    """Load and normalise the Agribalyse Excel file.

    Expects the 'Synthese' sheet with the header on the 3rd row (header=2).
    Returns a DataFrame with standardised column names and a '_code_str'
    search index.

    Raises:
        FileNotFoundError: path does not exist
        ValueError: file cannot be parsed or has unexpected structure
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Agribalyse file not found: {path}")

    print(f"  Loading Agribalyse: {path.name} ...", end=" ", flush=True)
    try:
        agri = pd.read_excel(path, sheet_name='Synthese', header=2)
    except Exception as exc:
        raise ValueError(f"Cannot read Agribalyse Excel ({path.name}): {exc}") from exc

    if len(agri.columns) < len(AGRIBALYSE_COLS):
        raise ValueError(
            f"Agribalyse sheet has {len(agri.columns)} columns, "
            f"expected >= {len(AGRIBALYSE_COLS)}.\n"
            "Check that the file is Agribalyse v3.x with the standard format."
        )

    # Rename the first N columns; any extra columns beyond AGRIBALYSE_COLS are kept as-is
    rename_map = {agri.columns[i]: AGRIBALYSE_COLS[i] for i in range(len(AGRIBALYSE_COLS))}
    agri = agri.rename(columns=rename_map)

    # Accent-normalised lowercase search index
    agri['_nom_lower'] = (agri['Nom_FR'].astype(str)
                          .apply(lambda x: _strip_accents(x).lower().strip()))

    # Canonical string index for code lookup
    agri['_code_str'] = agri['Code_AGB'].apply(lambda x: _normalize_code(str(x)))

    print(f"{len(agri)} products loaded.")
    return agri


# ──────────────────────────────────────────────────────────────────────────────
# 3.  PRODUCT LOOKUP
# ──────────────────────────────────────────────────────────────────────────────

def get_product_by_code(agri: pd.DataFrame, code) -> dict | None:
    """Fetch a single Agribalyse product by code.

    Returns a dict with keys Agribalyse_Code/Nom/Groupe/SousGroupe/DQR and
    all 20 AGB_*_per_kg impact factor keys, or None if not found.
    """
    code_str = _normalize_code(str(code))

    mask = agri['_code_str'] == code_str
    if not mask.any():
        # Fallback: compare numeric part (e.g., user gave "13024" for "13024_1")
        try:
            code_int = int(float(code_str.split('_')[0]))
            mask = agri['_code_str'].str.split('_').str[0].astype(str) == str(code_int)
        except (ValueError, TypeError):
            pass

    if not mask.any():
        return None

    r = agri[mask].iloc[0]
    result: dict = {
        'Agribalyse_Code':        r['Code_AGB'],
        'Agribalyse_Nom':         r['Nom_FR'],
        'Agribalyse_Groupe':      r['Groupe'],
        'Agribalyse_SousGroupe':  r['SousGroupe'],
        'Agribalyse_DQR':         r['DQR'],
    }
    for col in IMPACT_COLS:
        result[f'AGB_{col}_per_kg'] = r[col]
    return result


def search_agribalyse(agri: pd.DataFrame,
                      keywords: list[str] | None = None,
                      exclude:  list[str] | None = None,
                      max_results: int = 10,
                      fuzzy: bool = False,
                      fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD) -> list[dict]:
    """Search Agribalyse by keywords, with optional fuzzy matching.

    Two modes
    ---------
    Exact mode (default, ``fuzzy=False``):
        All ``keywords`` must appear as substrings in the product name (AND
        logic, accent-insensitive).  Fast – O(n × k) string scans.
        Results are returned in database order; no relevance score.

    Fuzzy mode (``fuzzy=True``):
        ``keywords`` are joined into a single query phrase and compared
        against every product name using ``token_set_ratio`` (rapidfuzz) or
        ``SequenceMatcher`` (stdlib fallback).  Results above
        ``fuzzy_threshold`` are returned sorted by score descending.
        A ``'Score_Matching'`` key (0–100) is added to every result dict.
        Use this when:
          - the exact spelling is uncertain ("vienoiserie" → viennoiserie)
          - the query word order differs from the product name
          - no exact match exists but a close proxy should be found

    Args:
        keywords:        search terms; joined into one phrase for fuzzy mode
        exclude:         terms that must *not* appear in results (both modes)
        max_results:     maximum number of results returned
        fuzzy:           enable fuzzy scoring
        fuzzy_threshold: minimum score (0–100) to include a result (fuzzy only)

    Returns:
        List of dicts with keys: Code_AGB, Nom_FR, Groupe, SousGroupe,
        Score_EF_mPt, CC_kgCO2eq [, Score_Matching (fuzzy only)]
    """
    cols = ['Code_AGB', 'Nom_FR', 'Groupe', 'SousGroupe', 'Score_EF_mPt', 'CC_kgCO2eq']

    # ── Build exclusion mask (common to both modes) ────────────────────────────
    excl_mask = pd.Series(True, index=agri.index)
    if exclude:
        for ekw in exclude:
            ekw_norm = _strip_accents(ekw).lower().strip()
            excl_mask &= ~agri['_nom_lower'].str.contains(ekw_norm, na=False, regex=False)

    # ── Exact mode ─────────────────────────────────────────────────────────────
    if not fuzzy:
        mask = excl_mask.copy()
        for kw in (keywords or []):
            kw_norm = _strip_accents(kw).lower().strip()
            mask &= agri['_nom_lower'].str.contains(kw_norm, na=False, regex=False)
        return agri[mask].head(max_results)[cols].to_dict('records')

    # ── Fuzzy mode ─────────────────────────────────────────────────────────────
    if not keywords:
        return []
    query = ' '.join(str(k) for k in keywords)
    query_norm = _strip_accents(query).lower().strip()

    # Restrict scoring to non-excluded rows for speed
    candidates = agri[excl_mask]
    scores = candidates['_nom_lower'].apply(
        lambda name: _fuzzy_score(query_norm, name)
    )

    # Filter by threshold and sort
    above = scores[scores >= fuzzy_threshold].sort_values(ascending=False)
    top_idx = above.head(max_results).index

    results = []
    for idx in top_idx:
        row = agri.loc[idx]
        r = {c: row[c] for c in cols}
        r['Score_Matching'] = round(float(above[idx]), 1)
        results.append(r)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 4.  INVENTORY LOADING AND VALIDATION
# ──────────────────────────────────────────────────────────────────────────────

def load_inventory(path: str) -> pd.DataFrame:
    """Load an inventory file (CSV or Excel).

    For Excel, tries sheets named 'Inventory' or 'Normalised_inventory' first,
    then falls back to the first sheet.

    Raises:
        FileNotFoundError: path does not exist
        ValueError: unsupported format or parse failure
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Inventory file not found: {path}")

    suffix = path.suffix.lower()
    try:
        if suffix == '.csv':
            df = pd.read_csv(path)
        elif suffix in ('.xlsx', '.xls'):
            xl = pd.ExcelFile(path)
            sheet = xl.sheet_names[0]
            for candidate in ('Inventory', 'Normalised_inventory',
                              'Inventaire', 'Inventaire_normalise', 'inventaire'):
                if candidate in xl.sheet_names:
                    sheet = candidate
                    break
            print(f"  Using sheet: '{sheet}'")
            df = pd.read_excel(path, sheet_name=sheet)
        else:
            raise ValueError(f"Unsupported file format: '{suffix}'. Use .csv, .xlsx, or .xls")
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise ValueError(f"Cannot load inventory ({path.name}): {exc}") from exc

    renamed = {old: new for old, new in COLUMN_ALIASES.items()
               if old in df.columns and new not in df.columns}
    if renamed:
        df = df.rename(columns=renamed)
        print(f"  Legacy French column names renamed: {', '.join(sorted(renamed))}")

    return df


def detect_column_roles(df: pd.DataFrame,
                        override_group_col: str | None = None) -> dict:
    """Analyse the inventory DataFrame and return a column-role map.

    Returns a dict with:
        mode:                 'expanded' | 'compact' | 'search'
        context_cols:         columns treated as free-form context/metadata
        traceability_cols:    traceability columns present in df
        group_col:            column used for MC grouping (first context col or override)
        product_identity_cols: columns that uniquely identify a product element
                               (context_cols + ['Product'])

    Raises:
        ValueError: required columns missing or no assignment column found
    """
    cols = set(df.columns)

    # ── Required columns ──────────────────────────────────────────────────────
    missing = [c for c in REQUIRED_COLS if c not in cols]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}\n"
            f"Required: {REQUIRED_COLS}\n"
            f"Found columns: {sorted(cols)}"
        )

    # ── Assignment mode ───────────────────────────────────────────────────────
    if 'Agribalyse_Code' in cols:
        mode = 'expanded'
    elif 'Agribalyse_Codes' in cols:
        mode = 'compact'
    elif 'Agribalyse_Keywords' in cols:
        mode = 'search'
    else:
        raise ValueError(
            "No Agribalyse assignment column found. Add one of:\n"
            "  'Agribalyse_Code'     – one row per candidate (expanded)\n"
            "  'Agribalyse_Codes'    – semicolon-separated codes (compact)\n"
            "  'Agribalyse_Keywords' – keyword search (search mode)"
        )

    # ── Context columns (everything not in any reserved set) ──────────────────
    all_reserved = (
        set(REQUIRED_COLS)
        | set(CODE_COLS)
        | set(SEARCH_COLS)
        | set(TRACEABILITY_COLS)
        | {MASS_CV_COL}
        | set(AGB_META_COLS)
    )
    context_cols = [
        c for c in df.columns
        if c not in all_reserved
        and not c.startswith('AGB_')
        and not c.startswith('CALC_')
        and not c.startswith('_')
    ]

    trace_cols = [c for c in TRACEABILITY_COLS if c in cols]

    # ── Group column ──────────────────────────────────────────────────────────
    if override_group_col:
        if override_group_col not in cols:
            warnings.warn(
                f"Specified --group-col '{override_group_col}' not in inventory; "
                f"falling back to '{context_cols[0] if context_cols else None}'"
            )
            group_col = context_cols[0] if context_cols else None
        else:
            group_col = override_group_col
    else:
        group_col = context_cols[0] if context_cols else None

    # ── Validation warnings ───────────────────────────────────────────────────
    masses = pd.to_numeric(df['Mass_kg'], errors='coerce')
    n_bad_mass = masses.isna().sum()
    if n_bad_mass:
        warnings.warn(f"{n_bad_mass} rows have non-numeric Mass_kg (will be skipped)")
    n_neg = (masses < 0).sum()
    if n_neg:
        warnings.warn(f"{n_neg} rows have negative Mass_kg")

    valid_conf = {'HIGH', 'MEDIUM', 'LOW'}
    invalid_conf = set(df['Match_Confidence'].dropna().unique()) - valid_conf
    if invalid_conf:
        warnings.warn(
            f"Unknown Match_Confidence values: {invalid_conf}. "
            "Expected HIGH / MEDIUM / LOW. Unrecognised values default to MEDIUM."
        )

    return {
        'mode':                  mode,
        'context_cols':          context_cols,
        'traceability_cols':     trace_cols,
        'group_col':             group_col,
        'product_identity_cols': context_cols + ['Product'],
    }


# ──────────────────────────────────────────────────────────────────────────────
# 5.  CANDIDATE EXPANSION
# ──────────────────────────────────────────────────────────────────────────────

def _parse_codes_field(raw) -> list[str]:
    """Parse a code field into a list of canonical code strings.

    Handles: int, float, str with ; , | separators, trailing .0 floats.
    Returns empty list for NaN / empty / 'nan'.
    """
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return []
    val = str(raw).strip()
    if val in ('', 'nan', 'None', 'NaN'):
        return []
    parts = [p.strip() for p in val.replace(',', ';').replace('|', ';').split(';')]
    return [_normalize_code(p) for p in parts if p]


def expand_candidates(inv_df: pd.DataFrame, agri: pd.DataFrame,
                      col_roles: dict) -> pd.DataFrame:
    """Expand inventory rows to one row per (product, Agribalyse candidate).

    Supports three modes (see detect_column_roles):
      - 'expanded': Agribalyse_Code column – one row per candidate already
      - 'compact':  Agribalyse_Codes column – split and expand
      - 'search':   Agribalyse_Keywords column – search Agribalyse and expand

    Adds Agribalyse meta columns (Agribalyse_Code, Nom, Groupe, SousGroupe, DQR)
    and AGB_*_per_kg columns for all 20 impact indicators.

    Returns:
        DataFrame with AGB_ columns; Mass_kg is numeric.

    Raises:
        ValueError: no valid rows produced (all codes invalid or search yielded nothing)
    """
    mode = col_roles['mode']
    context_cols = col_roles['context_cols']
    trace_cols   = col_roles['traceability_cols']

    # Columns to carry through from the input row
    carry_cols = (
        [c for c in context_cols if c in inv_df.columns]
        + [c for c in REQUIRED_COLS if c in inv_df.columns]
        + [c for c in trace_cols   if c in inv_df.columns]
        + ([MASS_CV_COL] if MASS_CV_COL in inv_df.columns else [])
    )
    # Deduplicate while preserving order
    carry_cols = list(dict.fromkeys(carry_cols))

    result_rows: list[dict] = []
    not_found:  list[str] = []

    # ── Expanded mode ─────────────────────────────────────────────────────────
    if mode == 'expanded':
        for _, row in inv_df.iterrows():
            raw_code = row.get('Agribalyse_Code')
            if raw_code is None or (isinstance(raw_code, float) and np.isnan(raw_code)):
                p_name = row.get('Product', '?')
                warnings.warn(f"Empty Agribalyse_Code for '{p_name}' – row skipped")
                continue

            product = get_product_by_code(agri, raw_code)
            if product is None:
                not_found.append(_normalize_code(str(raw_code)))
                continue

            out = {c: row[c] for c in carry_cols}
            out.update(product)
            result_rows.append(out)

    # ── Compact mode ──────────────────────────────────────────────────────────
    elif mode == 'compact':
        for _, row in inv_df.iterrows():
            codes = _parse_codes_field(row.get('Agribalyse_Codes'))
            if not codes:
                p_name = row.get('Product', '?')
                warnings.warn(f"Empty Agribalyse_Codes for '{p_name}' – row skipped")
                continue

            for code in codes:
                product = get_product_by_code(agri, code)
                if product is None:
                    not_found.append(code)
                    continue
                out = {c: row[c] for c in carry_cols}
                out.update(product)
                result_rows.append(out)

    # ── Search mode ───────────────────────────────────────────────────────────
    elif mode == 'search':
        MAX_CANDIDATES = 4
        for _, row in inv_df.iterrows():
            kw_raw    = str(row.get('Agribalyse_Keywords',  '')).strip()
            excl_raw  = str(row.get('Agribalyse_Exclude',   '')).strip()
            fuzzy_raw = row.get('Agribalyse_Fuzzy', False)
            thr_raw   = row.get('Agribalyse_Threshold', DEFAULT_FUZZY_THRESHOLD)
            p_name    = row.get('Product', '?')

            if not kw_raw or kw_raw == 'nan':
                warnings.warn(f"Empty Agribalyse_Keywords for '{p_name}' – row skipped")
                continue

            keywords_list = [k.strip() for k in kw_raw.replace(',', ' ').split() if k.strip()]
            exclude_list  = ([k.strip() for k in excl_raw.replace(',', ' ').split() if k.strip()]
                             if excl_raw and excl_raw != 'nan' else None)

            # Resolve fuzzy flag: True/False/'TRUE'/'1'/1/etc.
            use_fuzzy = (str(fuzzy_raw).strip().upper() in ('TRUE', '1', 'YES', 'OUI')
                         if not isinstance(fuzzy_raw, bool) else fuzzy_raw)
            try:
                threshold = float(thr_raw)
            except (ValueError, TypeError):
                threshold = DEFAULT_FUZZY_THRESHOLD

            matches = search_agribalyse(
                agri, keywords_list, exclude_list,
                max_results=MAX_CANDIDATES,
                fuzzy=use_fuzzy, fuzzy_threshold=threshold,
            )
            if not matches:
                # Auto-retry with fuzzy if exact search found nothing
                if not use_fuzzy:
                    matches = search_agribalyse(
                        agri, keywords_list, exclude_list,
                        max_results=MAX_CANDIDATES,
                        fuzzy=True, fuzzy_threshold=threshold,
                    )
                    if matches:
                        warnings.warn(
                            f"Exact search found nothing for '{p_name}' "
                            f"(keywords: {keywords_list}) – "
                            f"using fuzzy fallback (top score: {matches[0].get('Score_Matching', '?')})"
                        )
            if not matches:
                warnings.warn(f"No Agribalyse match for keywords {keywords_list} ('{p_name}') – skipped")
                continue

            for match in matches:
                product = get_product_by_code(agri, match['Code_AGB'])
                if product is None:
                    continue
                out = {c: row[c] for c in carry_cols}
                out.update(product)
                # Attach matching score if available (fuzzy mode)
                if 'Score_Matching' in match:
                    out['Score_Matching'] = match['Score_Matching']
                result_rows.append(out)

    if not_found:
        print(f"  WARNING: {len(not_found)} code(s) not found in Agribalyse: "
              f"{not_found[:8]}{'...' if len(not_found) > 8 else ''}")

    if not result_rows:
        raise ValueError(
            "No valid rows produced after candidate expansion.\n"
            "Check that Agribalyse codes are correct and the right Agribalyse file is used."
        )

    df = pd.DataFrame(result_rows)
    df['Mass_kg'] = pd.to_numeric(df['Mass_kg'], errors='coerce')
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 6.  IMPACT COMPUTATION
# ──────────────────────────────────────────────────────────────────────────────

def compute_impacts(df: pd.DataFrame) -> pd.DataFrame:
    """Compute CALC_* columns:  CALC_{ind} = AGB_{ind}_per_kg × Mass_kg.

    Missing or non-numeric factor values produce NaN (not 0).
    """
    df = df.copy()
    for col in IMPACT_COLS:
        agb_col  = f'AGB_{col}_per_kg'
        calc_col = f'CALC_{col}'
        if agb_col in df.columns:
            factor = pd.to_numeric(df[agb_col], errors='coerce')
            df[calc_col] = factor * df['Mass_kg']
        else:
            df[calc_col] = np.nan
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 7.  BEST / WORST SCENARIO SELECTION
# ──────────────────────────────────────────────────────────────────────────────

def select_best_worst(df: pd.DataFrame,
                      product_identity_cols: list[str],
                      confidence_first: bool = True) -> tuple:
    """Select best (lowest EF score) and worst (highest EF score) per product.

    Args:
        df: expanded DataFrame with all Agribalyse candidates per product
        product_identity_cols: columns that uniquely identify one product element
                               (typically context_cols + ['Product'])
        confidence_first: if True (default), HIGH-confidence candidates are
            preferred over MEDIUM/LOW regardless of their EF score.
            This mirrors the original IESF script behaviour: the confidence
            level reflects data quality, so a HIGH-quality match is always
            preferred as the primary estimate.

    Returns:
        (df_best, df_worst) – one row per product in each
    """
    score_col = 'AGB_Score_EF_mPt_per_kg'
    if score_col not in df.columns:
        raise ValueError(f"Column '{score_col}' not found. Run compute_impacts first.")

    _CONF_ORDER = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}

    work = df.copy()
    work['_conf_rank'] = work['Match_Confidence'].map(_CONF_ORDER).fillna(1).astype(float)
    work['_score_num'] = pd.to_numeric(work[score_col], errors='coerce').fillna(np.inf)

    # Only use identity cols that actually exist in the DataFrame
    id_cols = [c for c in product_identity_cols if c in work.columns]
    if not id_cols:
        # Fallback: treat each row as its own product (should not happen)
        work['_row_id'] = range(len(work))
        id_cols = ['_row_id']

    if confidence_first:
        # Best:  prefer HIGH confidence, then lowest score
        # Worst: prefer HIGH confidence, then highest score
        sort_best  = id_cols + ['_conf_rank', '_score_num']
        asc_best   = [True]  * len(id_cols) + [True,  True]
        sort_worst = id_cols + ['_conf_rank', '_score_num']
        asc_worst  = [True]  * len(id_cols) + [True, False]
    else:
        # Best/worst by EF score only
        sort_best  = id_cols + ['_score_num']
        asc_best   = [True] * len(id_cols) + [True]
        sort_worst = id_cols + ['_score_num']
        asc_worst  = [True] * len(id_cols) + [False]

    # Replace NaN in id_cols with a sentinel for groupby stability
    SENTINEL = '__NA__'
    for col in id_cols:
        work[col] = work[col].fillna(SENTINEL)

    df_sorted_best  = work.sort_values(sort_best,  ascending=asc_best)
    df_sorted_worst = work.sort_values(sort_worst, ascending=asc_worst)

    df_best  = df_sorted_best .groupby(id_cols, sort=False).first().reset_index()
    df_worst = df_sorted_worst.groupby(id_cols, sort=False).first().reset_index()

    # Restore sentinels → NaN
    for col in id_cols:
        df_best .loc[df_best [col] == SENTINEL, col] = np.nan
        df_worst.loc[df_worst[col] == SENTINEL, col] = np.nan

    drop_cols = ['_conf_rank', '_score_num', '_row_id']
    df_best  = df_best .drop(columns=drop_cols, errors='ignore')
    df_worst = df_worst.drop(columns=drop_cols, errors='ignore')

    return df_best, df_worst


# ──────────────────────────────────────────────────────────────────────────────
# 8.  MONTE CARLO UNCERTAINTY PROPAGATION
# ──────────────────────────────────────────────────────────────────────────────

def _dqr_to_cv(dqr) -> float:
    """Convert a DQR score to a coefficient of variation (PEF methodology).

    DQR 1 → CV 5 %   (excellent quality)
    DQR 3 → CV 20 %  (medium quality)
    DQR 5 → CV 50 %  (very poor quality)
    Linear interpolation; values outside [1, 5] are clamped.
    """
    try:
        dqr_val = max(1.0, min(5.0, float(dqr)))
    except (ValueError, TypeError):
        dqr_val = 3.0  # sensible default when DQR is missing
    return float(np.interp(dqr_val, _DQR_POINTS, _CV_POINTS))


def _lognormal_samples(mean: float, cv: float, n: int) -> np.ndarray:
    """Draw n samples from a lognormal distribution with given mean and CV.

    Returns array of shape (n,). Works for negative means (result is negated).
    Degenerates to a constant array when mean == 0 or cv == 0.
    """
    if mean == 0.0 or cv == 0.0 or np.isnan(mean):
        fill = mean if not np.isnan(mean) else 0.0
        return np.full(n, fill)
    sigma2   = np.log(1.0 + cv ** 2)
    mu       = np.log(abs(mean)) - sigma2 / 2.0
    samples  = np.random.lognormal(mu, np.sqrt(sigma2), n)
    return samples if mean >= 0 else -samples


def run_monte_carlo(df_scenario: pd.DataFrame,
                    group_col: str | None,
                    product_identity_cols: list[str],
                    n_mc: int = 1000,
                    seed: int = 42) -> dict:
    """Propagate uncertainty by Monte Carlo simulation.

    Two uncertainty sources are combined:
      1. Impact factors (Agribalyse DQR → CV, lognormal)
      2. Estimated masses    (Match_Confidence → CV, or explicit Mass_CV, lognormal)

    Args:
        df_scenario:           Best or worst scenario DataFrame (one row per product)
        group_col:             Column used to split results (e.g. 'WE').
                               If None or not in df, a single 'Total' group is used.
        product_identity_cols: Columns identifying unique products (for dedup).
        n_mc:                  Number of Monte Carlo draws.
        seed:                  Numpy random seed for reproducibility.

    Returns:
        dict: {group_value: {indicator: np.ndarray(n_mc)}}
    """
    np.random.seed(seed)

    # Deduplicate: one row per unique product
    id_cols = [c for c in product_identity_cols if c in df_scenario.columns]
    df = df_scenario.drop_duplicates(subset=id_cols if id_cols else None).copy()

    # Resolve group column
    _synthetic_group = False
    if not group_col or group_col not in df.columns:
        df['_mc_group'] = 'Total'
        group_col = '_mc_group'
        _synthetic_group = True

    groups = sorted(df[group_col].dropna().unique())
    results: dict = {g: {ind: np.zeros(n_mc) for ind in IMPACT_COLS} for g in groups}

    for _, row in df.iterrows():
        masse_kg  = float(row['Mass_kg']) if pd.notna(row['Mass_kg']) else 0.0
        dqr       = row.get('Agribalyse_DQR', 3.0)
        confiance = str(row.get('Match_Confidence', 'MEDIUM')).upper()
        grp       = row.get(group_col, 'Total')

        if grp not in results:
            results[grp] = {ind: np.zeros(n_mc) for ind in IMPACT_COLS}

        cv_impact    = _dqr_to_cv(dqr)
        cv_masse     = CONFIDENCE_CV.get(confiance, 0.20)
        # Une valeur explicite dans la colonne Mass_CV prime sur le niveau de confiance.
        cv_explicite = row.get(MASS_CV_COL, None)
        if cv_explicite is not None and pd.notna(cv_explicite):
            try:
                cv_explicite = float(cv_explicite)
                if cv_explicite >= 0:
                    cv_masse = cv_explicite
            except (TypeError, ValueError):
                pass
        masse_samp   = _lognormal_samples(masse_kg, cv_masse, n_mc)

        for ind in IMPACT_COLS:
            factor = row.get(f'AGB_{ind}_per_kg')
            if factor is None or pd.isna(factor) or factor == 0.0:
                continue
            factor = float(factor)
            factor_samp = _lognormal_samples(factor, cv_impact, n_mc)
            results[grp][ind] += masse_samp * factor_samp

    return results


def build_distributions_df(mc_best: dict, mc_worst: dict, n_mc: int) -> pd.DataFrame:
    """Convert raw MC result dicts into a tidy DataFrame for Excel output.

    Columns: Scenario, Group, Simulation, <indicator_1>, ..., <indicator_20>
    """
    rows: list[dict] = []
    for scenario_label, mc_data in (('Best_case', mc_best),
                                    ('Worst_case',      mc_worst)):
        for grp in sorted(mc_data.keys()):
            for i in range(n_mc):
                row: dict = {
                    'Scenario':   scenario_label,
                    'Group':      grp,
                    'Simulation': i + 1,
                }
                for ind in IMPACT_COLS:
                    arr = mc_data[grp].get(ind)
                    # Pas d'arrondi : plusieurs indicateurs, dont les toxicités
                    # humaines, valent moins de 1e-6 et seraient annulés.
                    row[ind] = float(arr[i]) if arr is not None else None
                rows.append(row)
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────────────────
# 9.  EXCEL OUTPUT
# ──────────────────────────────────────────────────────────────────────────────

def _ordered_columns(df: pd.DataFrame,
                     context_cols: list[str],
                     trace_cols: list[str]) -> list[str]:
    """Return a logical column ordering for the output sheets.

    Order: context → required (Product, …) → traceability →
           Agribalyse meta → AGB_*_per_kg → CALC_*
    """
    required_present = [c for c in REQUIRED_COLS if c in df.columns]
    trace_present    = [c for c in TRACEABILITY_COLS if c in df.columns]
    agb_meta         = [c for c in AGB_META_COLS if c in df.columns]
    agb_factors      = [f'AGB_{c}_per_kg' for c in IMPACT_COLS if f'AGB_{c}_per_kg' in df.columns]
    calc_cols        = [f'CALC_{c}' for c in IMPACT_COLS if f'CALC_{c}' in df.columns]

    ordered = (
        [c for c in context_cols if c in df.columns]
        + required_present
        + trace_present
        + agb_meta
        + agb_factors
        + calc_cols
    )
    # Deduplicate
    seen: set = set()
    result: list[str] = []
    for c in ordered:
        if c not in seen:
            seen.add(c)
            result.append(c)
    # Append any remaining columns not captured above (safety net)
    for c in df.columns:
        if c not in seen and not c.startswith('_'):
            result.append(c)
    return result


def save_xlsx(output_path: str,
              df_detail:  pd.DataFrame,
              df_best:    pd.DataFrame,
              df_worst:   pd.DataFrame,
              df_mc:      pd.DataFrame,
              context_cols: list[str],
              trace_cols:   list[str]) -> None:
    """Write the 4-sheet result Excel file."""
    col_detail = _ordered_columns(df_detail, context_cols, trace_cols)
    col_best   = _ordered_columns(df_best,   context_cols, trace_cols)
    col_worst  = _ordered_columns(df_worst,  context_cols, trace_cols)

    def _filter(df: pd.DataFrame, order: list[str]) -> list[str]:
        return [c for c in order if c in df.columns]

    print(f"  Writing Excel: {output_path} ...", end=" ", flush=True)
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df_detail[_filter(df_detail, col_detail)].to_excel(
            writer, sheet_name='All_candidates', index=False)
        df_best  [_filter(df_best,   col_best  )].to_excel(
            writer, sheet_name='Best_case',       index=False)
        df_worst [_filter(df_worst,  col_worst )].to_excel(
            writer, sheet_name='Worst_case',            index=False)
        df_mc.to_excel(writer, sheet_name='MC_distributions', index=False)
    print("done.")


# ──────────────────────────────────────────────────────────────────────────────
# 10. SUMMARY REPORTING
# ──────────────────────────────────────────────────────────────────────────────

def print_summary(df_detail: pd.DataFrame,
                  df_best:   pd.DataFrame,
                  df_worst:  pd.DataFrame,
                  mc_best:   dict,
                  mc_worst:  dict,
                  group_col: str | None,
                  product_identity_cols: list[str]) -> None:
    """Print a concise analysis summary to stdout."""
    id_cols = [c for c in product_identity_cols if c in df_best.columns]
    df_b_u  = df_best .drop_duplicates(subset=id_cols) if id_cols else df_best
    df_w_u  = df_worst.drop_duplicates(subset=id_cols) if id_cols else df_worst

    bar = "=" * 64
    print(f"\n{bar}")
    print("  RESULTS SUMMARY")
    print(bar)
    print(f"  Total rows in Detail sheet:      {len(df_detail):>6}")
    print(f"  Unique elementary products:      {df_detail['Product'].nunique():>6}")
    print(f"  Unique Agribalyse products used: {df_detail['Agribalyse_Code'].nunique():>6}")
    for conf in ('HIGH', 'MEDIUM', 'LOW'):
        n = (df_detail['Match_Confidence'] == conf).sum()
        print(f"  Confidence {conf:<8}:              {n:>6} rows")

    total_mass = df_b_u['Mass_kg'].sum()
    print(f"\n  Total mass (best scenario):      {total_mass:>9.1f} kg")

    cc_b = df_b_u['CALC_CC_kgCO2eq'].sum()
    cc_w = df_w_u['CALC_CC_kgCO2eq'].sum()
    ef_b = df_b_u['CALC_Score_EF_mPt'].sum()
    ef_w = df_w_u['CALC_Score_EF_mPt'].sum()
    print(f"  CC  – best:  {cc_b:>8.1f}  /  worst: {cc_w:.1f}  kgCO2eq")
    print(f"  EF  – best:  {ef_b:>8.1f}  /  worst: {ef_w:.1f}  mPt")

    if group_col and group_col in df_best.columns:
        print(f"\n  By '{group_col}':")
        for g in sorted(df_best[group_col].dropna().unique()):
            sub_b = df_b_u[df_b_u[group_col] == g]
            sub_w = df_w_u[df_w_u[group_col] == g]
            print(f"    {str(g):<12}  {sub_b['Mass_kg'].sum():>7.1f} kg  |  "
                  f"CC {sub_b['CALC_CC_kgCO2eq'].sum():>7.1f} / "
                  f"{sub_w['CALC_CC_kgCO2eq'].sum():>7.1f}  kgCO2eq")

    # MC confidence intervals
    n_mc = len(next(iter(next(iter(mc_best.values())).values())))
    print(f"\n  Monte Carlo (N={n_mc}, CI 90 %):")
    all_best_cc  = sum(mc_best [g]['CC_kgCO2eq'] for g in mc_best)
    all_worst_cc = sum(mc_worst[g]['CC_kgCO2eq'] for g in mc_worst)
    for label, arr in (("best ", all_best_cc), ("worst", all_worst_cc)):
        p5, p50, p95 = np.percentile(arr, [5, 50, 95])
        print(f"    CC {label}:  median {p50:>7.0f}  [{p5:.0f} – {p95:.0f}]  kgCO2eq")
    print(bar)


# ──────────────────────────────────────────────────────────────────────────────
# 11. FULL PIPELINE
# ──────────────────────────────────────────────────────────────────────────────

def run_pipeline(inventory_path: str,
                 agribalyse_path: str,
                 output_path: str,
                 group_col: str | None = None,
                 n_mc: int = 1000,
                 seed: int = 42) -> dict:
    """Execute the full impact calculation pipeline.

    Steps:
      1. Load Agribalyse database
      2. Load and validate inventory
      3. Expand Agribalyse candidates
      4. Compute CALC_* impact columns
      5. Select best / worst scenarios
      6. Monte Carlo uncertainty propagation
      7. Save Excel output

    Args:
        inventory_path:  path to normalized inventory CSV or Excel
        agribalyse_path: path to Agribalyse Excel file (v3.x)
        output_path:     path for the output Excel file
        group_col:       column name for MC grouping (overrides auto-detect)
        n_mc:            number of Monte Carlo draws
        seed:            random seed for reproducibility

    Returns:
        dict with keys: agri, inventory, col_roles, detail, best, worst,
                        mc_best, mc_worst, distributions
    """
    print(f"\n[agribalyse_autoderive v{VERSION}]  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  Inventory:   {inventory_path}")
    print(f"  Agribalyse:  {agribalyse_path}")
    print(f"  Output:      {output_path}")

    # ── 1. Load ───────────────────────────────────────────────────────────────
    print("\n[1/6] Loading data...")
    agri = load_agribalyse(agribalyse_path)
    inv  = load_inventory(inventory_path)
    print(f"  Inventory: {len(inv)} rows, {len(inv.columns)} columns")

    # ── 2. Validate ───────────────────────────────────────────────────────────
    print("\n[2/6] Validating inventory...")
    col_roles = detect_column_roles(inv, override_group_col=group_col)
    print(f"  Mode:             {col_roles['mode']}")
    print(f"  Context columns:  {col_roles['context_cols']}")
    print(f"  Group column:     {col_roles['group_col']}")

    # ── 3. Expand candidates ──────────────────────────────────────────────────
    print(f"\n[3/6] Expanding Agribalyse candidates (mode: {col_roles['mode']})...")
    df_expanded = expand_candidates(inv, agri, col_roles)
    print(f"  → {len(df_expanded)} rows")

    # ── 4. Compute impacts ────────────────────────────────────────────────────
    print("\n[4/6] Computing impacts...")
    df_expanded = compute_impacts(df_expanded)

    # ── 5. Best / worst ───────────────────────────────────────────────────────
    print("\n[5/6] Selecting best / worst scenarios...")
    df_best, df_worst = select_best_worst(
        df_expanded, col_roles['product_identity_cols'])
    print(f"  → {len(df_best)} rows each (best / worst)")

    # ── 6. Monte Carlo ────────────────────────────────────────────────────────
    print(f"\n[6/6] Monte Carlo (N={n_mc}, seed={seed})...")
    mc_best  = run_monte_carlo(df_best,  col_roles['group_col'],
                               col_roles['product_identity_cols'], n_mc, seed)
    mc_worst = run_monte_carlo(df_worst, col_roles['group_col'],
                               col_roles['product_identity_cols'], n_mc, seed)
    df_mc = build_distributions_df(mc_best, mc_worst, n_mc)
    print(f"  → {len(df_mc)} rows")

    # ── 7. Save ───────────────────────────────────────────────────────────────
    save_xlsx(output_path, df_expanded, df_best, df_worst, df_mc,
              col_roles['context_cols'], col_roles['traceability_cols'])

    # ── Summary ───────────────────────────────────────────────────────────────
    print_summary(df_expanded, df_best, df_worst, mc_best, mc_worst,
                  col_roles['group_col'], col_roles['product_identity_cols'])

    return {
        'agri':          agri,
        'inventory':     inv,
        'col_roles':     col_roles,
        'detail':        df_expanded,
        'best':          df_best,
        'worst':         df_worst,
        'mc_best':       mc_best,
        'mc_worst':      mc_worst,
        'distributions': df_mc,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 12. CLI
# ──────────────────────────────────────────────────────────────────────────────

def _cmd_search(args, agri: pd.DataFrame) -> None:
    """Handle --search mode: keyword/fuzzy search in Agribalyse and print results."""
    keywords  = args.search
    exclude   = args.exclude or []
    use_fuzzy = getattr(args, 'fuzzy', False)
    threshold = getattr(args, 'threshold', DEFAULT_FUZZY_THRESHOLD)

    mode_label = f"fuzzy (threshold={threshold:.0f})" if use_fuzzy else "exact"
    results = search_agribalyse(
        agri, keywords, exclude or None,
        max_results=args.max_results,
        fuzzy=use_fuzzy, fuzzy_threshold=threshold,
    )

    if not results:
        print(f"No {mode_label} results for: {' '.join(keywords)}"
              + (f"  (excluding: {exclude})" if exclude else ""))
        # Offer fuzzy fallback hint if exact search failed
        if not use_fuzzy:
            fuzzy_try = search_agribalyse(
                agri, keywords, exclude or None,
                max_results=5, fuzzy=True, fuzzy_threshold=threshold,
            )
            if fuzzy_try:
                print(f"\nTip: try --fuzzy to find close matches, e.g.:")
                for r in fuzzy_try[:3]:
                    print(f"  [{r.get('Score_Matching', '?'):5.1f}]  {r['Nom_FR']}")
        return

    header = f"\nAgribalyse – {mode_label} search:  {' '.join(keywords)}"
    if exclude:
        header += f"  (excluding: {exclude})"
    print(header)

    has_score = 'Score_Matching' in results[0]
    score_hdr = f"{'Match%':>7}  " if has_score else ""
    print(f"{'Code':<12} {score_hdr}{'EF (mPt/kg)':>14}  {'CC (kgCO2eq/kg)':>16}  Nom")
    print("-" * (93 + (9 if has_score else 0)))
    for r in results:
        ef   = r.get('Score_EF_mPt', float('nan'))
        cc   = r.get('CC_kgCO2eq',   float('nan'))
        ef_s = f"{ef:.4f}" if isinstance(ef, (int, float)) and not np.isnan(ef) else "N/A"
        cc_s = f"{cc:.4f}" if isinstance(cc, (int, float)) and not np.isnan(cc) else "N/A"
        score_col = f"{r['Score_Matching']:>6.1f}%  " if has_score else ""
        print(f"{r['Code_AGB']!s:<12} {score_col}{ef_s:>14}  {cc_s:>16}  {r['Nom_FR']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog='agribalyse_autoderive',
        description='Generic Agribalyse Impact Calculator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Main pipeline arguments
    parser.add_argument('--inventory',   '-i', metavar='FILE',
                        help='Normalized inventory CSV or Excel file')
    parser.add_argument('--agribalyse', '-a', metavar='FILE', required=True,
                        help='Agribalyse Excel file (v3.x, sheet "Synthese")')
    parser.add_argument('--output',      '-o', metavar='FILE',
                        help='Output Excel path (default: <inventory>_impacts.xlsx)')
    parser.add_argument('--group-col',   '-g', metavar='COL',
                        help='Column for Monte Carlo grouping (e.g. WE)')
    parser.add_argument('--n-mc',        '-n', type=int, default=1000,
                        help='Number of Monte Carlo simulations (default: 1000)')
    parser.add_argument('--seed',        type=int, default=42,
                        help='Random seed for reproducibility (default: 42)')

    # Search mode (no inventory needed)
    parser.add_argument('--search',      nargs='+', metavar='KW',
                        help='Search Agribalyse by keywords (no inventory required)')
    parser.add_argument('--exclude',     nargs='+', metavar='KW',
                        help='Keywords to exclude from --search results')
    parser.add_argument('--fuzzy',       action='store_true',
                        help='Use fuzzy matching for --search (handles typos and word order)')
    parser.add_argument('--threshold',   type=float, default=DEFAULT_FUZZY_THRESHOLD,
                        metavar='N',
                        help=f'Fuzzy similarity threshold 0–100 (default: {DEFAULT_FUZZY_THRESHOLD:.0f})')
    parser.add_argument('--max-results', type=int, default=15,
                        help='Max results for --search (default: 15)')

    parser.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')

    args = parser.parse_args(argv)

    try:
        agri = load_agribalyse(args.agribalyse)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── Search mode ───────────────────────────────────────────────────────────
    if args.search:
        _cmd_search(args, agri)
        return

    # ── Pipeline mode ─────────────────────────────────────────────────────────
    if not args.inventory:
        parser.error("--inventory is required unless --search is used")

    output = args.output
    if output is None:
        inv_p  = Path(args.inventory)
        output = str(inv_p.parent / (inv_p.stem + '_impacts.xlsx'))

    try:
        run_pipeline(
            inventory_path=args.inventory,
            agribalyse_path=args.agribalyse,
            output_path=output,
            group_col=args.group_col,
            n_mc=args.n_mc,
            seed=args.seed,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
