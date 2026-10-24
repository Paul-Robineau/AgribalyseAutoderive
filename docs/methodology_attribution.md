# Methodology – Food Inventory Attribution with Agribalyse

This document describes the attribution methodology implemented in `agribalyse_autoderive.py`: how a real-world food inventory is translated into environmental impacts using the Agribalyse database.

---

## 1. Scope and purpose

The goal is to compute the environmental footprint of a set of food purchases or meals using the **Agribalyse** database as the impact source. The functional unit is typically **1 kg of food product as consumed** (at consumer stage), consistent with the Agribalyse modelling boundary.

The methodology is designed for situations where:
- Food data comes from purchase records, invoices, or meal plans – not from measured masses.
- Products must be matched to Agribalyse entries by name, not by barcode or CIQUAL code.
- Uncertainty is substantial and should be quantified rather than ignored.

---

## 2. Agribalyse database

**Agribalyse** (ADEME, France) is a national LCA database for food products. The current version (v3.2, published August 2025) covers **2,458 products** with **20 environmental impact indicators** following the EF 3.1 methodology.

Key characteristics:
- Functional unit: **1 kg of product as consumed** (consumer stage, includes retail, packaging, and preparation losses)
- Impact method: Environmental Footprint 3.1 (EF 3.1), midpoint characterisation
- Each product carries a **DQR** (Data Quality Rating, 1–5) reflecting the quality of underlying LCI data

Download: [agribalyse.ademe.fr](https://agribalyse.ademe.fr/)

---

## 3. Inventory construction

### 3.1 Decomposition into elementary products

Raw purchase data (invoices, meal plans, receipts) rarely lists products in a form directly matchable to Agribalyse. The first step is **decomposition**: each line item is broken into individual elementary products that can each be matched to a single Agribalyse entry.

For example, a catering line "Breakfast – coffee/tea/pastries/fruit/juice" becomes:
- Coffee (espresso)
- Tea
- Croissant (viennoiserie)
- Fresh fruit
- Orange juice

### 3.2 Mass estimation

Purchase records typically provide quantities in units (pieces, servings, persons) rather than kg. Mass must be estimated from:

**Reference portion sizes:**

| Component | Reference source | Typical range |
|-----------|-----------------|---------------|
| Hot beverages | Standard cup | 200 mL |
| Fruit juice | GEMRCN / standard | 100–200 mL |
| Pastry (viennoiserie) | Product standard | 40–60 g |
| Main dish (vegetables) | GEMRCN | 200–300 g |
| Starchy side | GEMRCN | 150–250 g |
| Dessert | GEMRCN | 100–130 g |
| Bread | GEMRCN | 40 g |
| Cocktail snacks | Hypothèse de l'étude | 25–30 g/piece, 10–15 pieces |
| Wine | Standard bottle | 750 mL / 6 glasses |
| Beer | Standard can | 330 mL |

> GEMRCN: Groupe d'Étude des Marchés de Restauration Collective et de Nutrition (French institutional catering standards).  
> Les lignes marquées « Hypothèse de l'étude » ne renvoient à aucune source publiée : ce sont des
> ordres de grandeur d'usage en restauration, documentés ligne à ligne dans la colonne de notes.

**Mass calculation chain:**

```
Masse_totale (kg) = Masse_par_personne (kg) × Nombre_de_personnes
```

Where, depending on product type:
- **Liquids**: `Masse_par_personne = Volume_mL × Density_kg/L / 1000`
- **Piece-counted products**: `Masse_par_personne = Nb_pieces × Masse_par_piece_g / 1000`
- **Portions**: `Masse_par_personne` estimated directly from reference tables

### 3.3 Documentation

Every mass estimate should be traceable. The inventory format supports the following traceability columns:

| Column | Description |
|--------|-------------|
| `N_People` | Number of people |
| `Mass_per_Person_g` | Mass per person (g) |
| `Pieces_per_Person` | Number of pieces per person |
| `Mass_per_Piece_g` | Mass per piece (g) |
| `Volume_per_Person_mL` | Volume per person (mL) |
| `Density_kg_L` | Product density (kg/L) |
| `Estimation_Source` | Free text: source or justification |

---

## 4. Matching with Agribalyse

### 4.1 Search strategy

Each elementary product is matched to one or more Agribalyse products by keyword search in product names. The search is **accent-insensitive** (é = e, etc.) and supports:
- **Exact mode**: AND logic – all keywords must appear in the product name
- **Fuzzy mode**: tolerates typos, word order variants, partial phrases (see Section 4.3)

Multiple candidates are retained when several Agribalyse products are plausible matches, enabling best/worst scenario analysis.

### 4.2 Confidence levels

Each match is assigned a confidence level reflecting the quality of the correspondence:

| Level | Criteria | Mass CV |
|-------|----------|---------|
| **HIGH** | Direct, unambiguous match – the Agribalyse product is essentially the same food | 10% |
| **MEDIUM** | Reasonable approximation – same food category, minor differences in preparation or composition | 20% |
| **LOW** | Proxy – no direct equivalent exists; closest available product used | 35% |

The CV (coefficient of variation) associated with each confidence level is used as the mass uncertainty in Monte Carlo simulation (Section 6).

### 4.3 Common proxy situations

Some food categories lack direct Agribalyse equivalents. Typical proxy strategies:

| Real product | Recommended proxy | Rationale |
|--------------|------------------|-----------|
| Vegetable curry | Ratatouille or cooked vegetable dish | Similar cooked vegetable composition |
| Veggie dumplings / gyoza | Nem / spring roll | Similar filled pastry structure |
| Grain bowl (cereals + vegetables) | Tabbouleh or couscous salad | Similar cereal + vegetable composition |
| Filled sandwich (artisan) | Standard baguette sandwich | Bread + filling structure |
| Homemade granola | Muesli croustillant | Similar toasted cereal base |
| Risotto (specific variety) | Rice dish (riz cantonais) | Similar grain-based dish |

When a proxy is used, assign `LOW` confidence and document the justification in `Estimation_Source`.

---

## 5. Impact calculation

For each row (elementary product × Agribalyse candidate), all 20 impact indicators are computed:

```
CALC_{indicator} = AGB_{indicator}_per_kg × Mass_kg
```

where `AGB_{indicator}_per_kg` is the Agribalyse impact factor per kg of consumed product.

The 20 EF 3.1 indicators computed are listed in [docs/script_reference.md](script_reference.md#impact-indicators-20-ef-31-columns).

---

## 6. Best-case / worst-case scenarios

Once all candidates are expanded and impacts computed, two deterministic scenarios are built:

**Best-case** (`Best_case`): for each elementary product, the Agribalyse candidate with the **lowest** EF single score (mPt) is selected.

**Worst-case** (`Worst_case`): for each elementary product, the candidate with the **highest** EF single score is selected.

**Confidence-first rule** (default): within each product, HIGH-confidence matches are always preferred over MEDIUM and LOW, regardless of score. This prevents artificially optimistic results driven by poorly-matched proxy products. The rule can be disabled via the `confidence_first` parameter in the Python API.

---

## 7. Uncertainty propagation – Monte Carlo

Two independent sources of uncertainty are modelled:

### 7.1 Factor uncertainty: DQR → CV

The Agribalyse DQR (Data Quality Rating) quantifies the reliability of the LCI data behind each product. It is converted to a coefficient of variation (CV) following the PEF/EF 3.1 methodology:

| DQR | Data quality | CV |
|-----|--------------|----|
| 1 | Excellent | 5% |
| 2 | Good | 10% |
| 3 | Average | 20% |
| 4 | Poor | 35% |
| 5 | Very poor | 50% |

Linear interpolation is used for non-integer DQR values.

### 7.2 Mass uncertainty: confidence level → CV

Mass uncertainty reflects both the precision of the portion size reference and the quality of the product match. It is encoded in the `Match_Confidence` column:

| Confidence | CV |
|------------|----|
| HIGH | 10% |
| MEDIUM | 20% |
| LOW | 35% |

### 7.3 Monte Carlo procedure

For each product and each simulation draw (N = 1 000 by default):

1. Sample the **mass** from a lognormal distribution: mean = `Mass_kg`, CV = confidence CV.
2. Sample each **impact factor** from a lognormal distribution: mean = Agribalyse factor, CV = DQR CV.
3. Compute the product impact: `impact = sampled_mass × sampled_factor`.
4. Sum all product impacts per group (e.g. per event, per meal category).

The lognormal distribution is appropriate because masses and impact factors are strictly positive and typically right-skewed.

Results are stored in the `MC_distributions` sheet: one row per (scenario × group × simulation), enabling computation of percentiles, confidence intervals, and other summary statistics.

**Reproducibility**: the random seed is fixed (`seed=42` by default). Pass `--seed N` to use a different seed.

### 7.4 Interpreting results

From the Monte Carlo distributions, typical outputs are:

- **Median**: robust central estimate (less sensitive to outliers than the mean)
- **P5–P95**: 90% confidence interval
- **P2.5–P97.5**: 95% confidence interval

Compare the deterministic result (best/worst scenario) with the Monte Carlo median to validate consistency. A large gap suggests that a small number of high-mass/high-impact products dominate and deserve closer scrutiny.

---

## 8. Limitations

### 8.1 Mass estimation uncertainty

When purchase records do not include masses, all quantities must be estimated from reference portion sizes. This is the dominant uncertainty source in most food inventory studies. Common situations to document carefully:

- **Lump-sum catering contracts**: "dinner for 60 persons, €23/person" gives no product detail. Decomposition is hypothetical and should be assigned `LOW` confidence.
- **Social events / bar items**: forfait prices cover a wide range of possible consumption; the decomposition is highly speculative.
- **Shared dishes**: portion size depends on the number and appetite of participants; a ±30% range is realistic.

### 8.2 Proxy products

When no direct Agribalyse equivalent exists, the proxy introduces systematic bias. The direction of the bias depends on the specific product – verify that the proxy's carbon intensity (g CO₂eq/kg) is in the same order of magnitude as the real product before accepting it.

### 8.3 Agribalyse modelling assumptions

Agribalyse models **standard industrial products**. Artisanal, seasonal, or locally-produced variants may have different impact profiles. The database does not distinguish preparation methods beyond broad categories (e.g. "ready to eat" vs. "raw").

### 8.4 Food waste

Agribalyse factors represent the product **at consumer stage** and already incorporate upstream transformation and distribution losses. Plate waste at the event is **not** included in the methodology. According to ADEME, food waste at collective catering events is typically 10–15%.

### 8.5 Functional unit boundary

The methodology covers **food products and beverages** consumed at the event. Non-food items (tableware, delivery, room rental) should be excluded at the inventory construction stage.

---

## 9. Recommendations for future studies

- Collect masses at source: ask caterers for total prepared masses when possible.
- Prefer detailed menus over lump-sum contracts for better decomposition.
- Run the script with multiple threshold values and compare results to test robustness.
- Consider integrating with a full LCA framework (e.g. Brightway2) for a multi-stage analysis covering transport, accommodation, and other impact categories.
