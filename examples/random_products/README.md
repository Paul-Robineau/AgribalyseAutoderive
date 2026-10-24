# Example: random products

This example has no link with any real event. It shows that the tools run on any food inventory laid out in the expected format, whatever its products, groups or meals.

`make_random_example.py` draws products at random from the Agribalyse database, along with fictitious groups (two seminar days and a field trip), meals, guest numbers, portion sizes and matching confidences. The seed is fixed, so the files are reproducible. The generated decomposition exercises every feature of the format at least once:

- the five mass estimation methods: mass per person, pieces per person, volume per person with density, total mass, mass per unit with a number of units;
- products with a single Agribalyse candidate and products with several, taken from the same Agribalyse sub-group;
- confidence and `mass_cv` set for a whole component, or for one candidate only;
- a variant reference whose code carries a suffix (`9341_1`).

## Files

| File | Role | Description |
|------|------|-------------|
| `make_random_example.py` | generator | Writes `invoice.xlsx` and `decomposition.yml` |
| `invoice.xlsx` | input | 7 fictitious invoice lines |
| `decomposition.yml` | input | 21 elementary products drawn at random |
| `inventory.xlsx` | output 1 | Normalised inventory written by `inventory_builder.py`: 44 product × candidate rows |
| `results.xlsx` | output 2 | Impacts written by `agribalyse_autoderive.py` |

## Reproduce

From the repository root:

```bash
python examples/random_products/make_random_example.py

python inventory_builder.py \
  --invoice examples/random_products/invoice.xlsx \
  --decomposition examples/random_products/decomposition.yml \
  --output examples/random_products/inventory.xlsx

python agribalyse_autoderive.py \
  --inventory examples/random_products/inventory.xlsx \
  --agribalyse "data/AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx" \
  --output examples/random_products/results.xlsx
```

## Results

Total mass 139 kg, climate change between 596 kg CO₂ eq (best case) and 1 021 kg CO₂ eq (worst case). The wide gap is expected: candidates drawn at random within an Agribalyse sub-group can be far apart, which is precisely the situation the two scenarios are meant to bound.
