# Script Reference – agribalyse_autoderive.py

## Overview

`agribalyse_autoderive.py` is a self-contained Python script that computes the environmental impact of a food inventory using the **Agribalyse** database. It reads a normalised inventory file, matches products to Agribalyse entries, computes 20 EF 3.1 impact indicators, builds best/worst scenarios, and propagates uncertainty via Monte Carlo simulation.

---

## Installation

```bash
conda env create -f environment.yml
conda activate AgribalyseAutoderive
```

Or manually:

```bash
pip install pandas>=2.2 numpy>=2.0 openpyxl>=3.1 rapidfuzz>=3.0
```

`rapidfuzz` is optional: if not installed, fuzzy search falls back to Python's `difflib.SequenceMatcher` (less accurate).

---

## CLI Reference

```
python agribalyse_autoderive.py [options]
```

### Pipeline mode

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--inventory FILE` | `-i` | required | Input inventory (CSV or Excel) |
| `--agribalyse FILE` | `-a` | required | Agribalyse XLSX (sheet `Synthese`) |
| `--output FILE` | `-o` | `impact_results.xlsx` | Output Excel workbook |
| `--group-col COL` | `-g` | auto (first context col) | Column to use as grouping variable for Monte Carlo |
| `--n-mc N` | `-n` | `1000` | Number of Monte Carlo iterations |
| `--seed N` | | `42` | Random seed for reproducibility |

### Search mode

| Option | Description |
|--------|-------------|
| `--search WORD [WORD ...]` | Search Agribalyse by keywords (prints results, no pipeline) |
| `--exclude WORD [WORD ...]` | Exclude products containing these words |
| `--fuzzy` | Enable fuzzy matching (tolerates typos, word order, partial phrases) |
| `--threshold N` | Fuzzy similarity threshold, 0–100 (default: 60) |
| `--max-results N` | Maximum number of results to display (default: 10) |

### Examples

```bash
# Keyword search
python agribalyse_autoderive.py -a AGRIBALYSE3.2.xlsx --search vin rouge --max-results 5

# Exclude terms
python agribalyse_autoderive.py -a AGRIBALYSE3.2.xlsx --search pain --exclude complet surgele

# Fuzzy search
python agribalyse_autoderive.py -a AGRIBALYSE3.2.xlsx --search vienoiserie --fuzzy
python agribalyse_autoderive.py -a AGRIBALYSE3.2.xlsx --search "cafe filtre" --fuzzy --threshold 70

# Full pipeline
python agribalyse_autoderive.py \
  -i inventory.csv -a AGRIBALYSE3.2.xlsx \
  -o results.xlsx --group-col Group --n-mc 1000
```

---

## Inventory format

The inventory must be a CSV or Excel (`.xlsx`) file. Columns are detected automatically by name.

### Required columns

| Column | Type | Description |
|--------|------|-------------|
| `Product` | str | Elementary product name (unique identifier within a context group) |
| `Mass_kg` | float | Total mass in kg (must be > 0) |
| `Match_Confidence` | str | Matching confidence: `HIGH`, `MEDIUM`, or `LOW` |

### Agribalyse assignment – choose one mode

**Expanded mode** – one row per candidate code (already expanded):

| Column | Type | Description |
|--------|------|-------------|
| `Agribalyse_Code` | int / str | Single Agribalyse code |

**Compact mode** – multiple codes on one row:

| Column | Type | Description |
|--------|------|-------------|
| `Agribalyse_Codes` | str | Semicolon-separated codes: `"5001;5011;5209"` |

**Search mode** – codes resolved at run time by keyword search:

| Column | Type | Description |
|--------|------|-------------|
| `Agribalyse_Keywords` | str | Space-separated keywords (AND logic) |
| `Agribalyse_Exclude` | str | (optional) Words to exclude from results |
| `Agribalyse_Fuzzy` | bool | (optional) `True` to use fuzzy search for this row |
| `Agribalyse_Threshold` | float | (optional) Per-row fuzzy threshold override (0–100) |

### Optional traceability columns (preserved in output, ignored by computation)

`N_People`, `Mass_per_Person_g`, `Pieces_per_Person`, `Mass_per_Piece_g`,
`Volume_per_Person_mL`, `Density_kg_L`, `Estimation_Source`

### Context columns

Any column not in the above lists is treated as a **context column** and passed through to the output unchanged. The first context column is used as the Monte Carlo grouping variable (override with `--group-col`). Typical context columns, as written by `inventory_builder.py`: `Group`, `Meal_Type`, `Invoice_Item`, `Unit_Price_excl_VAT_EUR`, etc.

### Mixed modes

A single inventory may mix modes row-by-row: expanded rows (with `Agribalyse_Code`) and search rows (with `Agribalyse_Keywords`) can coexist. The detection logic is:

1. If `Agribalyse_Code` column exists → expanded mode rows
2. If `Agribalyse_Codes` column exists → compact mode rows
3. If `Agribalyse_Keywords` column exists → search mode rows

---

## Output format

The output is an Excel workbook with four sheets:

### Sheet 1: `All_candidates`

All candidates – one row per (product × Agribalyse code). Contains all input columns plus:

| Column prefix | Description |
|---------------|-------------|
| `Agribalyse_Code` | Matched Agribalyse code |
| `Agribalyse_Nom` | Agribalyse product name |
| `Agribalyse_Groupe` | Agribalyse category |
| `Agribalyse_SousGroupe` | Agribalyse subcategory |
| `Agribalyse_DQR` | Data Quality Rating (1–5) |
| `AGB_{indicator}_per_kg` | Agribalyse impact factor per kg (20 columns) |
| `CALC_{indicator}` | Computed impact = factor × mass (20 columns) |
| `Score_Matching` | Fuzzy match score (0–100), only in search/fuzzy mode |

### Sheet 2: `Best_case`

Best-case scenario: for each product, the candidate with the **lowest** EF score is selected. When `confidence_first=True` (default), HIGH-confidence matches are always preferred over MEDIUM and LOW, regardless of score.

### Sheet 3: `Worst_case`

Worst-case scenario: for each product, the candidate with the **highest** EF score is selected, with the same confidence-first priority rule.

### Sheet 4: `MC_distributions`

Raw Monte Carlo distributions. One row per (scenario × group × simulation), with columns:

`Scenario`, `Groupe`, `Simulation`, then one column per impact indicator.

---

## Impact indicators (20 EF 3.1 columns)

| Column | Unit | Description |
|--------|------|-------------|
| `Score_EF_mPt` | mPt | Single EF score |
| `CC_kgCO2eq` | kg CO₂ eq | Climate change (total) |
| `CC_biogenique_kgCO2eq` | kg CO₂ eq | Climate change – biogenic |
| `CC_fossile_kgCO2eq` | kg CO₂ eq | Climate change – fossil |
| `CC_affectSol_kgCO2eq` | kg CO₂ eq | Climate change – land use |
| `Ozone_kgCVC11eq` | kg CVC11 eq | Ozone depletion |
| `Rayonnements_kBqU235eq` | kBq U235 eq | Ionising radiation |
| `Ozone_photo_kgNMVOCeq` | kg NMVOC eq | Photochemical ozone |
| `Particules_diseaseInc` | disease incidences | Particulate matter |
| `Toxicol_nonCancer_CTUh` | CTUh | Human toxicity (non-cancer) |
| `Toxicol_Cancer_CTUh` | CTUh | Human toxicity (cancer) |
| `Acidification_molHeq` | mol H⁺ eq | Acidification |
| `Eutro_eauDouce_kgPeq` | kg P eq | Freshwater eutrophication |
| `Eutro_marine_kgNeq` | kg N eq | Marine eutrophication |
| `Eutro_terrestre_molNeq` | mol N eq | Terrestrial eutrophication |
| `Ecotox_CTUe` | CTUe | Aquatic ecotoxicity |
| `UtilSol_Pt` | Pt | Land use |
| `Eau_m3depriv` | m³ world eq deprived | Water use |
| `Energie_MJ` | MJ | Energy |
| `Mineraux_kgSbeq` | kg Sb eq | Mineral resources |

---

## Fuzzy matching

When exact keyword search yields no results, the script automatically retries with fuzzy matching and prints a warning. The `--fuzzy` flag forces fuzzy mode from the start.

### Scoring algorithm

Fuzzy scores are computed as `max(token_set_ratio, WRatio)` from the `rapidfuzz` library (both accent-normalised):

- **`token_set_ratio`**: tokenises both strings and compares the intersection + remainders; ideal for subset queries where the user types a subset of the product name (e.g. `"cafe"` → `"Café expresso, non instantané…"`).
- **`WRatio`**: tries multiple strategies (ratio, partial ratio, token sort) and returns the best; handles partial multi-word phrases and typos better (e.g. `"cafe filtre"` → `"Café expresso…"`).

Taking the maximum of the two gives the best coverage across query types.

### Threshold

The default threshold is **60** (0–100 scale). Typical values:

| Threshold | Effect |
|-----------|--------|
| 40–50 | Broad recall, many false positives |
| 60 | Default – good balance |
| 75–85 | High precision, fewer matches |
| ≥ 90 | Near-exact matches only |

Per-row overrides are possible via the `Agribalyse_Threshold` inventory column.

---

## Confidence levels and uncertainty

### Matching confidence (affects mass uncertainty in Monte Carlo)

| Level | Meaning | Mass CV |
|-------|---------|---------|
| `HIGH` | Direct, precise match | 10% |
| `MEDIUM` | Reasonable match with approximation | 20% |
| `LOW` | Proxy used, approximate match | 35% |

### DQR → CV (affects factor uncertainty in Monte Carlo)

The DQR (Data Quality Rating) from Agribalyse is converted to a coefficient of variation (CV) following the PEF/EF 3.1 methodology, with linear interpolation between anchor points:

| DQR | Data quality | CV |
|-----|--------------|----|
| 1 | Excellent | 5% |
| 2 | Good | 10% |
| 3 | Average | 20% |
| 4 | Poor | 35% |
| 5 | Very poor | 50% |

---

## Python API

The script can be imported and used programmatically:

```python
from agribalyse_autoderive import (
    load_agribalyse,
    search_agribalyse,
    get_product_by_code,
    detect_column_roles,
    expand_candidates,
    compute_impacts,
    select_best_worst,
    run_monte_carlo,
    save_xlsx,
    run_pipeline,
)

# Load database
agri = load_agribalyse("AGRIBALYSE3.2.xlsx")

# Search
results = search_agribalyse(agri, keywords=["pain", "complet"], max_results=5)
results = search_agribalyse(agri, keywords=["vienoiserie"], fuzzy=True, fuzzy_threshold=65)

# Full pipeline (returns dict with DataFrames)
out = run_pipeline(
    inventory_path="my_inventory.csv",
    agribalyse_path="AGRIBALYSE3.2.xlsx",
    output_path="results.xlsx",
    group_col="Category",
    n_mc=1000,
    seed=42,
)
# out keys: 'detail', 'best', 'worst', 'mc_best', 'mc_worst', 'distributions'
```

---

## Agribalyse XLSX format notes

- Sheet: `Synthese`
- Header is on **row 3** (0-indexed: `header=2`)
- Code column: `Code AGB` → mapped to `Code_AGB`
- Products are identified at the "consumed product" level (at consumer stage, including preparation losses)
- Codes with underscore suffix (e.g. `13024_1`) are variant products; they are compared as strings
- Float codes in CSV (e.g. `5214.0`) are automatically normalised to their integer string representation (`"5214"`)

### `Mass_CV` (optional)

Per-row coefficient of variation of the mass, as a number. Overrides the value derived from
`Match_Confidence` for that row only; rows that leave it empty keep the confidence-level
default. Non-numeric or negative values are ignored.

Use it when an external reference gives a plausible range for a mass and you want the Monte
Carlo to carry that range without moving the central value. With a lognormal of mean `m` and
CV `c`, `s = sqrt(ln(1 + c^2))`, the 95th percentile is `m * exp(1.645*s - s^2/2)`.

The column is excluded from the context columns, so adding it never changes product identity,
grouping or deduplication. It is carried through to the scenario sheets of the output.
