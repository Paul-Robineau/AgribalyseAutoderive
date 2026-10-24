# Agribalyse database

This folder must contain the Agribalyse food products spreadsheet published by ADEME. It is not stored in the repository to keep it light (about 9 MB): download it before running the scripts or the tests.

1. Go to the [Agribalyse website](https://agribalyse.ademe.fr/) and its [documentation](https://doc.agribalyse.fr/documentation), where the spreadsheets of each release are published.
2. Download the *Agribalyse 3.2 – Tableur produits alimentaires* spreadsheet (release of August 2025).
3. Place it in this folder under its original name:

```
data/AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx
```

The examples and the tests expect this exact name. The scripts read the `Synthese` sheet and should work with any Agribalyse 3.x release keeping that layout: with another release or file name, pass its path to `--agribalyse`. Note that the example results in `examples/` were computed with release 3.2 and will differ with another one.

Without the file, the tests that need it are skipped rather than failed.

The Agribalyse data are published by ADEME under the [Licence Ouverte / Open Licence (Etalab 2.0)](https://www.etalab.gouv.fr/licence-ouverte-open-licence/). The same data are also available on data.gouv.fr, as tables rather than as this spreadsheet: [AGRIBALYSE – Synthèse](https://www.data.gouv.fr/datasets/agribalyse-synthese), [Détail par ingrédient](https://www.data.gouv.fr/datasets/agribalyse-detail-par-ingredient) and [Détail par étape](https://www.data.gouv.fr/datasets/agribalyse-detail-par-etape).
