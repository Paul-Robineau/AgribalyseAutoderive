# Example: IESF Knowledge Convention

This example is the real use case the tools were developed for: the food served during the [IESF Convention on Knowledge](https://conventions.iesf.fr/convention-connaissances/home), a deliberative process of four residential weekends held in France between October 2025 and January 2026, with about 50 to 65 people per weekend. All meals were vegetarian.

## Context

| Group | Dates | City |
|-------|-------|------|
| WE1 | 4–5 October 2025 | Fontainebleau |
| WE2 | 15–16 November 2025 | Chambéry |
| WE3 | 6–7 December 2025 | Troyes |
| WE4 | 17–18 January 2026 | Nantes |

The catering invoices give product descriptions, quantities and prices, but no masses. Each invoice line was decomposed into elementary products, whose masses were estimated from the French GEMRCN portion references where they apply, and from documented assumptions of the study otherwise. Each product was matched to one or several plausible Agribalyse references.

## Files

| File | Role | Description |
|------|------|-------------|
| `invoice.xlsx` | input | 56 invoice lines from the caterers of the four weekends |
| `decomposition.yml` | input | Decomposition of each invoice line into elementary products, with mass estimate, source, Agribalyse candidates and confidence |
| `inventory.xlsx` | output 1 | Normalised inventory written by `inventory_builder.py`: 104 elementary products, 236 product × candidate rows |
| `results.xlsx` | output 2 | Impacts written by `agribalyse_autoderive.py`: all candidates, best and worst scenarios, Monte Carlo draws |
| `reference_totals.json` | test | Expected totals, used by the regression tests |

## Reproduce

From the repository root:

```bash
python inventory_builder.py \
  --invoice examples/iesf_knowledge_convention/invoice.xlsx \
  --decomposition examples/iesf_knowledge_convention/decomposition.yml \
  --output examples/iesf_knowledge_convention/inventory.xlsx

python agribalyse_autoderive.py \
  --inventory examples/iesf_knowledge_convention/inventory.xlsx \
  --agribalyse "data/AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx" \
  --output examples/iesf_knowledge_convention/results.xlsx
```

The inventory produced is exactly the one used in the life cycle assessment of the convention: same masses, candidates, confidence levels and mass uncertainties, row for row.

## Key results

Total mass: 997 kg. 62 distinct Agribalyse references are used.

**Climate change (kg CO₂ eq)**

| Scenario | Deterministic | Monte Carlo median | 90 % interval (P5–P95) |
|----------|--------------:|-------------------:|-----------------------:|
| Best case | 1 231 | 1 228 | 1 162 – 1 301 |
| Worst case | 1 495 | 1 494 | 1 404 – 1 592 |

**Per weekend, deterministic (kg CO₂ eq)**

| Group | Mass (kg) | Best case | Worst case |
|-------|----------:|----------:|-----------:|
| WE1 | 309 | 336 | 391 |
| WE2 | 167 | 310 | 322 |
| WE3 | 222 | 292 | 320 |
| WE4 | 299 | 294 | 463 |

WE4 shows the widest gap between scenarios: several of its invoices were lump-sum catering contracts without menu detail, so its products have many low-confidence candidates.

## Notes on the decomposition

- The four portions that sit below the adult range of the GEMRCN reference (bread, starchy side dish, breakfast fruit juice, cooked vegetables) keep the masses reconstructed from the invoices, with a widened `mass_cv` that places the GEMRCN upper bound at the 95th percentile.
- Where candidates differ in how well they match the invoiced product, each candidate carries its own confidence level, for instance an orange juice reference rated `HIGH` next to a multi-fruit juice rated `MEDIUM`.
