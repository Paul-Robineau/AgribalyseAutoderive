# AgribalyseAutoderive

Automated derivation of bounded environmental impacts for food inventories, using the French [Agribalyse](https://agribalyse.ademe.fr/) database.

This work has been done as part of the life cycle assessment (LCA) of the IESF (*Ingénieurs et Scientifiques de France*) [Convention on Knowledge](https://conventions.iesf.fr/convention-connaissances/home), a deliberative process modelled on citizen conventions. The associated LCA report will be linked as soon as it is published. The tools are not specific to that study: they apply to any food inventory laid out in the expected format, as the random example of this repository shows.

## Context

Food is often the hardest part of an event inventory. Invoices list dishes, menus or lump-sum packages, rarely masses, and never the database reference that best represents each product. Two steps are therefore needed before any impact can be computed: decomposing each invoice line into elementary products with an estimated mass, then matching each product to an Agribalyse reference.

The second step is rarely clear-cut. An invoiced "fruit juice" may be an orange juice or a multi-fruit juice; a "starchy side dish" may be rice or potatoes. Rather than arbitrating by hand, which hides the choice, AgribalyseAutoderive keeps every plausible candidate and bounds the result between a best and a worst scenario. The gap between the two measures the weight of the matching choices, and the uncertainty of masses and Agribalyse data is propagated by Monte Carlo on top of it.

## Objective & Methodology

The pipeline has two steps, each handled by one script.

1. **`inventory_builder.py`** reads an invoice table and a decomposition file (YAML) mapping each invoice line to elementary products. For each product it computes the total mass from one of five methods (mass per person, pieces per person, volume per person with density, total mass, mass per unit with a number of units), and writes a normalised inventory with one row per product × Agribalyse candidate.
2. **`agribalyse_autoderive.py`** reads that inventory, or any inventory in the same format however it was built, and:
   - looks up each candidate in Agribalyse, by code, or by exact or fuzzy keyword search;
   - computes the 20 indicators published by Agribalyse: the 16 impact categories of the Environmental Footprint method (EF 3.1), the EF single score, and the three sub-indicators of climate change;
   - builds two scenarios. For each product, the candidates with the best confidence level are kept first, then the one with the lowest EF single score gives the **best case** and the one with the highest score the **worst case**;
   - propagates uncertainty by Monte Carlo, with lognormal draws on both the Agribalyse factor and the mass.

Each Agribalyse impact factor is drawn with a coefficient of variation derived from its Data Quality Rating (DQR 1 to 5 → CV 0.05 to 0.50, linear in between). Each mass is drawn with a coefficient of variation set by the matching confidence (`HIGH` 0.10, `MEDIUM` 0.20, `LOW` 0.35), unless a `mass_cv` is given explicitly. These confidence levels are coarse defaults of the tool, not measured values: when the plausible range of a mass is known from a reference, give its coefficient of variation directly.

The full description of the matching strategy and of the uncertainty treatment is in [docs/methodology_attribution.md](docs/methodology_attribution.md), and every option of the scripts in [docs/script_reference.md](docs/script_reference.md).

## Repository contents

| Path | Description |
|------|-------------|
| `inventory_builder.py` | Step 1: invoice + decomposition file → normalised inventory |
| `agribalyse_autoderive.py` | Step 2: normalised inventory → impacts, scenarios and Monte Carlo draws. Also searches Agribalyse from the command line |
| `data/` | Location of the Agribalyse spreadsheet, not shipped: see [data/README.md](data/README.md) |
| `examples/iesf_knowledge_convention/` | Real use case: the food of the four weekends of the IESF Convention on Knowledge |
| `examples/random_products/` | Products drawn at random from Agribalyse, to demonstrate that the tools are generic |
| `docs/` | Methodology and full script reference |
| `tests/` | Test suite (191 tests) |
| `environment.yml`, `requirements.txt` | Python environment |

Each example folder contains the two input files and the two output files of the pipeline:

| File | Role |
|------|------|
| `invoice.xlsx` | input: invoice lines |
| `decomposition.yml` | input: decomposition of each line into elementary products |
| `inventory.xlsx` | output 1: normalised inventory written by `inventory_builder.py` |
| `results.xlsx` | output 2: impacts written by `agribalyse_autoderive.py` |

## Which entry point?

The tools automate the second half of the work: once each product has a mass and one or several plausible Agribalyse references, they compute every indicator, bound the result between a best and a worst scenario instead of arbitrating between candidates, and propagate the uncertainty. They do not automate the first half, which needs judgement: deciding what an invoice line actually contains, how much of it was served per person, and which references are plausible. That work is done once, by hand, in the input files. Three entry points are available, from the most traceable to the quickest.

| Entry point | Use it when | What you write by hand | What is automated |
|-------------|-------------|------------------------|-------------------|
| **A. Invoice + decomposition file** | You only have invoices listing dishes, menus or lump-sum packages, without masses | The decomposition of each invoice line into products, with portion, source, candidate codes and confidence | Masses, inventory, impacts, scenarios, uncertainty |
| **B. Inventory with codes** | Masses are known (orders by weight, stock records) and you choose the references yourself | One row per product: mass, confidence, one or several Agribalyse codes | Impacts, scenarios, uncertainty |
| **C. Inventory with keywords** | Masses are known and you want a first estimate quickly | One row per product: mass, confidence, a few keywords | Search of the references, impacts, scenarios, uncertainty |

**A** is the most work, but every assumption is written down next to its source, which is what an LCA report needs. It is the path followed for the convention, whose decomposition file runs to some 1,500 lines for 56 invoice lines. The command-line search (`--search`, see [How to use](#how-to-use)) helps find candidate codes while writing it.

**B** skips the decomposition: write the normalised inventory directly, with the `Agribalyse_Code` or `Agribalyse_Codes` column.

**C** is the quickest: with an `Agribalyse_Keywords` column, the script searches Agribalyse itself and keeps up to four matching references per product as candidates, switching to fuzzy search when the exact search finds nothing. An optional `Agribalyse_Exclude` column removes unwanted terms. Search results can be loose, for instance a raw ingredient next to a ready meal: always review the candidates kept in the `All_candidates` sheet of the output before using the results, and refine the keywords where needed.

The three entry points can be mixed in one inventory, and an inventory first produced with C can be turned into B by keeping only the codes judged relevant.

### How far can the matching be automated?

To document this, entry point C was tested on the convention example with no human input at all: the 169 products of the inventory, with their masses but without any Agribalyse code, the product name itself being used as keywords. The table compares the results with the manual matching of the decomposition file.

| Search | Products matched | Mass covered | Climate change, best – worst (kg CO₂ eq) |
|--------|-----------------:|-------------:|-----------------------------------------:|
| Exact | 117 / 169 | 625 kg | 955 – 1 481 |
| Fuzzy, threshold 60 (default) | 110 / 169 | 584 kg | 846 – 1 873 |
| Fuzzy, threshold 45 | 168 / 169 | 992 kg | 1 151 – 7 718 |
| Fuzzy, threshold 30 | 169 / 169 | 997 kg | 1 321 – 7 878 |
| *Manual matching (entry point A)* | *169 / 169* | *997 kg* | *1 231 – 1 495* |

At the default threshold, about 40 % of the mass finds no reference at all. Lowering the threshold covers the whole mass, but with absurd candidates that inflate the worst case fivefold: "Café filtre" (filter coffee) returns a canelé, cooked carrot and fried sole; "Accompagnement féculent (riz ou pommes de terre)" (starchy side dish) returns a lace crêpe, breaded veal escalope and lamb heart; "Pain" (bread) returns pitta bread, but also pastis and gin. Fuzzy search measures how alike two strings of characters are, not what they mean: it works well with well-chosen keywords ("riz cuit", "pomme de terre cuite"), but choosing them is the same translation work as the decomposition file.

Full automation therefore needs an understanding of what the products are, which string matching cannot provide. A large language model can provide it: given the invoice lines, it can draft the decomposition file, with products, portions, sources and candidate references, so that the remaining human task is to review the draft rather than to write it. The review cannot be skipped: portions and cited sources must be checked, as a language model may state a plausible value or cite a reference that does not exist. This step is not part of the tools in this repository.

## How to use

From the repository root, with the environment activated (see [Requirements](#requirements)) and the Agribalyse spreadsheet downloaded into `data/` (see [Agribalyse data](#agribalyse-data)):

```bash
# Step 1: build the inventory
python inventory_builder.py \
  --invoice my_invoice.xlsx \
  --decomposition my_decomposition.yml \
  --output my_inventory.xlsx

# Step 2: compute the impacts
python agribalyse_autoderive.py \
  --inventory my_inventory.xlsx \
  --agribalyse "data/AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx" \
  --output my_results.xlsx \
  --n-mc 1000
```

To find Agribalyse codes while writing a decomposition file:

```bash
# Exact keyword search (all keywords must match, accents ignored)
python agribalyse_autoderive.py -a "data/AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx" --search pain complet

# Fuzzy search, tolerant to typos and word order
python agribalyse_autoderive.py -a "data/AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx" --search vienoiserie --fuzzy
```

Agribalyse product names, searched and reported by the scripts, are in French. The spreadsheet in `data/` also gives the English name of each reference in its `LCI_Name` column.

## Input formats

### Invoice

An Excel or CSV table, one row per invoice line.

| Column | Required | Description |
|--------|:--------:|-------------|
| `Group` | yes | Grouping variable: event, day, site… Results are also given per group |
| `MealType` | yes | Breakfast, Lunch, Coffee break… |
| `Item` | yes | Invoice line, as written on the invoice |
| `NbGuests` | | Number of covers; can also be set per item in the decomposition file |
| `Qty`, `Unit`, `UnitPriceHT`, `TotalPriceTTC`, `Notes` | | Traceability only |

The French column names of earlier versions (`WE`, `Type_Repas`, `Produit_Facture`…) are still accepted.

### Decomposition file

A YAML file listing, for each invoice line, its elementary products:

```yaml
items:
  - group: "Day 1"
    meal_type: "Breakfast"
    invoice_item: "Continental breakfast"
    components:
      - name: "Coffee"
        volume_mL_per_person: 200
        density_kg_L: 1.0
        agribalyse_codes: ["18004"]           # coffee, ready to drink
        confidence: HIGH
        source: "1 hot drink of 200 mL per person"
      - name: "Fruit juice"
        volume_mL_per_person: 150
        agribalyse_codes:                       # several candidates: best / worst scenarios
          - {code: "2012", confidence: HIGH}    # each candidate may carry its own confidence
          - {code: "2048", confidence: MEDIUM}
        mass_cv: 0.2                            # optional, overrides the default mass uncertainty
        source: "1 glass of 150 mL per person"
```

Mass methods, by order of priority: `total_mass_kg`; `mass_per_unit_kg` with `nb_units`; `pieces_per_person` with `mass_per_piece_g`; `volume_mL_per_person` with `density_kg_L` (1.0 by default); `mass_g_per_person`. Write Agribalyse codes between quotes: YAML would read a variant code such as `9341_1` as the number 93411.

### Normalised inventory

This is what `inventory_builder.py` writes, and what `agribalyse_autoderive.py` reads. It can also be written by hand or by any other tool. Required columns are `Product`, `Mass_kg` and `Match_Confidence` (`HIGH`, `MEDIUM` or `LOW`), plus one way of assigning Agribalyse references:

| Column | Mode | Description |
|--------|------|-------------|
| `Agribalyse_Code` | expanded | One row per product × candidate |
| `Agribalyse_Codes` | compact | One row per product, codes separated by semicolons |
| `Agribalyse_Keywords` | search | Keywords; Agribalyse is searched at run time |

An optional `Mass_CV` column overrides the mass uncertainty row by row. Any other column is kept as context; the first one is used to group results, unless `--group-col` says otherwise.

## Outputs

`agribalyse_autoderive.py` writes one Excel workbook with four sheets:

| Sheet | Content |
|-------|---------|
| `All_candidates` | Every product × candidate, with its Agribalyse factors per kg (`AGB_…_per_kg`) and computed impacts (`CALC_…`) |
| `Best_case` | One row per product: the candidate kept in the best case |
| `Worst_case` | One row per product: the candidate kept in the worst case |
| `MC_distributions` | Monte Carlo draws in long format: one row per draw, scenario and group |

A summary is also printed: total mass, climate change and EF single score per scenario and per group, and the Monte Carlo median and 90 % interval.

## Examples

| Example | Inventory | Climate change, best – worst (kg CO₂ eq) |
|---------|-----------|------------------------------------------:|
| [IESF Convention on Knowledge](examples/iesf_knowledge_convention/) | 56 invoice lines, 104 products, 997 kg | 1 231 – 1 495 |
| [Random products](examples/random_products/) | 7 invoice lines, 21 products, 139 kg | 596 – 1 021 |

The convention example reproduces, row for row, the inventory used in the LCA of the convention.

## Agribalyse data

The Agribalyse spreadsheet is not stored in this repository, to keep it light. Download the *Agribalyse 3.2 – Tableur produits alimentaires* spreadsheet published by ADEME in August 2025 from the [Agribalyse website](https://agribalyse.ademe.fr/) ([documentation](https://doc.agribalyse.fr/documentation)), and place it in `data/` under its original name, `AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx`: the examples and tests expect that path. Step-by-step instructions are in [data/README.md](data/README.md). The scripts read the `Synthese` sheet and should work with any Agribalyse 3.x release keeping that layout; the example results were computed with release 3.2. The Agribalyse data are published by ADEME under the [Licence Ouverte / Open Licence (Etalab 2.0)](https://www.etalab.gouv.fr/licence-ouverte-open-licence/); the same data are also available on data.gouv.fr: [AGRIBALYSE – Synthèse](https://www.data.gouv.fr/datasets/agribalyse-synthese), [Détail par ingrédient](https://www.data.gouv.fr/datasets/agribalyse-detail-par-ingredient) and [Détail par étape](https://www.data.gouv.fr/datasets/agribalyse-detail-par-etape).

## Tests performed

Run the test suite from the repository root:

```bash
python -m pytest tests -v
```

| File | Tests | What is checked |
|------|------:|-----------------|
| `tests/test_suite.py` | 167 | Code normalisation, Agribalyse loading and lookup, exact and fuzzy search, the three inventory modes, column roles, candidate expansion, impact computation, best/worst selection, Monte Carlo reproducibility and consistency, command-line interface, and the full regression of the convention example against `reference_totals.json` |
| `tests/test_synthetic_inventory.py` | 14 | An inventory unrelated to any event, built from scratch: every impact is recomputed by hand from the Agribalyse database and compared with the script output, for both input formats and with the legacy French column names |
| `tests/test_inventory_builder.py` | 10 | The five mass estimation methods, confidence and `mass_cv` set per candidate, legacy invoice column names, and the exact rebuild of both example inventories from their invoice and decomposition files |

Last run: 191 tests passed, on Linux with the environment listed below (October 2026). Without the Agribalyse spreadsheet in `data/`, the 119 tests that need it are skipped and the 72 others pass.

Beyond the automated tests, the convention example was checked against the life cycle assessment it comes from: the inventory rebuilt from the invoices and the decomposition file is identical, row for row, to the one used in the study (masses, candidates, confidence levels and mass uncertainties), and the resulting impacts match its totals.

## Requirements

### Environment

Tested with the following package versions (see `environment.yml`):

| Package | Version |
|---------|---------|
| Python | 3.11.10 |
| pandas | 2.2.3 |
| numpy | 2.2.2 |
| openpyxl | 3.1.5 |
| pyyaml | 6.0.2 |
| rapidfuzz | 3.12.2 |
| pytest | 9.0.3 |

`rapidfuzz` is only used by fuzzy search, which falls back to Python's `difflib` without it.

### Reproduce the environment from the yml file

```bash
conda env create -f environment.yml
conda activate agribalyse-autoderive
```

Or with pip:

```bash
pip install -r requirements.txt
```

### Beginner: If you need to setup from scratch

I suggest you to use `Miniconda`:

> Miniconda is a free, miniature installation of Anaconda Distribution that includes only conda, Python, the packages they both depend on, and a small number of other useful packages.

[Miniconda Installation Guide](https://www.anaconda.com/docs/getting-started/miniconda/main)

Then from your Python environment manager console (like miniconda console, or anything else that suits you), apply the "Reproduce the environment from the yml file" section, or create your conda environment manually using:

```
conda create -n myenv_name python=3.11 pandas numpy openpyxl pyyaml rapidfuzz pytest
```

You can then activate the environment:

```
conda activate myenv_name
```

## Use of generative AI

Several models of the [Claude Code](https://claude.com/product/claude-code) agent (Anthropic) were used during the development of this repository, in particular to generate the Python code of the two scripts and of the tests, the random example generator, and to draft the documentation, including this README. The methodological choices, the review of the code and the checking of the results are human work by the author, who takes full responsibility for them.

## Related work

- [KnowledgeSociety-IESFConvention-Tools](https://github.com/Paul-Robineau/KnowledgeSociety-IESFConvention-Tools): drawing of lots for the participants and working groups of the same convention.
- [RailTrace](https://github.com/Oyoerf/IESF-Convention-KnowledgeSociety-TrainTripsDistances), by Silvio Clément: reconstruction of the train distances travelled, used for the transport part of the same LCA.

## License

GNU General Public License v3.0, see [LICENSE](LICENSE): the tools can be used, studied, modified and shared freely, and any modified version that is redistributed must remain under the same licence. The Agribalyse data, downloaded separately, remain under the Licence Ouverte / Open Licence (Etalab 2.0) of their publisher.
