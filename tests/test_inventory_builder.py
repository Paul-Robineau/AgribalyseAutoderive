#!/usr/bin/env python3
"""Tests for inventory_builder.py: invoice + decomposition file -> normalised inventory.

Run from the repository root:
    python -m pytest tests/test_inventory_builder.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inventory_builder as ib                # noqa: E402

EXAMPLES = ROOT / 'examples'


def _build(example):
    folder = EXAMPLES / example
    return ib.build_inventory(ib.load_invoice(folder / 'invoice.xlsx'),
                              ib.load_decomposition(folder / 'decomposition.yml'),
                              verbose=False)


@pytest.mark.parametrize('example', ['iesf_knowledge_convention', 'random_products'])
def test_examples_rebuild_exactly(example):
    """The shipped inventory.xlsx is exactly what the builder makes of the inputs."""
    built = _build(example)
    shipped = pd.read_excel(EXAMPLES / example / 'inventory.xlsx')
    assert list(built.columns) == list(shipped.columns)
    assert len(built) == len(shipped)
    assert (built['Product'].values == shipped['Product'].values).all()
    assert (built['Agribalyse_Code'].astype(str).values ==
            shipped['Agribalyse_Code'].astype(str).values).all()
    assert (built['Match_Confidence'].values == shipped['Match_Confidence'].values).all()
    assert np.allclose(built['Mass_kg'], shipped['Mass_kg'])
    assert np.allclose(built['Mass_CV'].astype(float).fillna(-1), shipped['Mass_CV'].fillna(-1))


def test_convention_total_mass():
    """Each component counts once, whatever its number of candidates."""
    inv = _build('iesf_knowledge_convention')
    keys = ['Group', 'Meal_Type', 'Invoice_Item', 'Product']
    assert inv.drop_duplicates(keys)['Mass_kg'].sum() == pytest.approx(997.07, abs=0.01)
    assert inv['Product'].nunique() == 104
    assert len(inv) == 236


def _invoice():
    """A one-line invoice for 10 guests, with canonical column names."""
    return pd.DataFrame([{'Group': 'G', 'Meal_Type': 'Lunch', 'Invoice_Item': 'Menu',
                          'Invoice_Covers': 10}])


def _item(*components):
    return [{'group': 'G', 'meal_type': 'Lunch', 'invoice_item': 'Menu',
             'components': list(components)}]


@pytest.mark.parametrize('spec,total', [
    ({'total_mass_kg': 3.2}, 3.2),
    ({'mass_per_unit_kg': 0.75, 'nb_units': 4}, 3.0),
    ({'pieces_per_person': 2, 'mass_per_piece_g': 50}, 1.0),
    ({'volume_mL_per_person': 200, 'density_kg_L': 1.03}, 2.06),
    ({'mass_g_per_person': 120}, 1.2),
])
def test_mass_methods(spec, total):
    """The five mass estimation methods, for 10 guests."""
    inv = ib.build_inventory(_invoice(), _item({'name': 'X', 'agribalyse_code': 1, **spec}),
                             verbose=False)
    assert inv['Mass_kg'].iloc[0] == pytest.approx(total)


def test_candidate_overrides_confidence_and_mass_cv():
    """A candidate's own confidence and mass_cv override those of its component."""
    comp = {'name': 'Juice', 'mass_g_per_person': 150, 'confidence': 'LOW', 'mass_cv': 0.3,
            'agribalyse_codes': [{'code': 2012, 'confidence': 'HIGH', 'mass_cv': 0.187},
                                 {'code': 2048},
                                 '13024_1']}
    inv = ib.build_inventory(_invoice(), _item(comp), verbose=False)
    assert list(inv['Agribalyse_Code'].astype(str)) == ['2012', '2048', '13024_1']
    assert list(inv['Match_Confidence']) == ['HIGH', 'LOW', 'LOW']
    assert list(inv['Mass_CV']) == [0.187, 0.3, 0.3]
    assert (inv['Mass_kg'] == 1.5).all()


def test_legacy_french_invoice_columns(tmp_path):
    """Invoices written with the French column names of earlier versions still load."""
    path = tmp_path / 'invoice.csv'
    pd.DataFrame([{'WE': 'G', 'Type_Repas': 'Lunch', 'Produit_Facture': 'Menu',
                   'Nb_Couverts_Facture': 10}]).to_csv(path, index=False)
    inv = ib.build_inventory(ib.load_invoice(path),
                             _item({'name': 'X', 'mass_g_per_person': 100, 'agribalyse_code': 1}),
                             verbose=False)
    assert inv['Group'].iloc[0] == 'G'
    assert inv['Mass_kg'].iloc[0] == pytest.approx(1.0)
