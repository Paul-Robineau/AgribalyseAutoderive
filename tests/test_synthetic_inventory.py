#!/usr/bin/env python3
"""Generality test: an inventory built from scratch and checked by hand.

The convention inventory only exercises one path through the script. The one built
here is unrelated to it and deliberately covers the awkward cases: both input
formats, the three confidence levels, a product with a single candidate and products
with several, a context column used for grouping, and traceability columns.

Expected values are recomputed here from the Agribalyse database, independently of
the script, then compared with its outputs. The test fails if either path drifts.

Run from the repository root:
    python -m pytest tests/test_synthetic_inventory.py -v
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'agribalyse_autoderive.py'
AGRIBALYSE = ROOT / 'data' / 'AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx'

sys.path.insert(0, str(ROOT))
import agribalyse_autoderive as agb          # noqa: E402


# ── The fabricated inventory ─────────────────────────────────────────────────
# Six products, unrelated to any event. Agribalyse codes are taken from
# different groups so that impacts are clearly apart. Codes are real and checked
# against Agribalyse 3.2; the EF scores in comments fix in advance which
# candidate each scenario must keep.
PRODUCTS = [
    # Product,             mass,   confidence, candidate codes
    ('Sandwich complet',   2.400, 'HIGH',   ['25574']),                 # 0.3540
    ('Jus de pomme',       1.250, 'HIGH',   ['2074']),                  # 0.0766
    ('Nem au poulet',      3.000, 'MEDIUM', ['25582', '25568']),        # 0.4520 / 0.6360
    ('Ratatouille',        4.750, 'LOW',    ['25018', '20266']),        # 0.2180 / 0.1000
    ('Galette lentilles',  0.900, 'MEDIUM', ['25591']),                 # 0.2130
    ('Plat lentilles',     0.180, 'LOW',    ['25010', '25591']),        # 0.1960 / 0.2130
]
MEALS = ['Day 1', 'Day 1', 'Day 1', 'Day 2', 'Day 2', 'Day 2']

# Expected candidate per scenario (best, worst), decided by hand from the scores above.
EXPECTED_CHOICE = {
    'Sandwich complet':  ('25574', '25574'),
    'Jus de pomme':      ('2074',  '2074'),
    'Nem au poulet':     ('25582', '25568'),
    'Ratatouille':       ('20266', '25018'),
    'Galette lentilles': ('25591', '25591'),
    'Plat lentilles':    ('25010', '25591'),
}


def _compact_inventory() -> pd.DataFrame:
    """Compact format: one row per product, codes separated by semicolons."""
    return pd.DataFrame({
        'Meal':             MEALS,
        'Product':          [p[0] for p in PRODUCTS],
        'Mass_kg':          [p[1] for p in PRODUCTS],
        'Match_Confidence': [p[2] for p in PRODUCTS],
        'Agribalyse_Codes': [';'.join(p[3]) for p in PRODUCTS],
        'N_People':         [12, 12, 12, 12, 12, 12],
        'Estimation_Source': ['fictitious invoice'] * 6,
    })


def _expanded_inventory() -> pd.DataFrame:
    """Expanded format: one row per product x code pair."""
    rows = []
    for (name, mass, conf, codes), meal in zip(PRODUCTS, MEALS):
        for code in codes:
            rows.append({'Meal': meal, 'Product': name, 'Mass_kg': mass,
                         'Match_Confidence': conf, 'Agribalyse_Code': code,
                         'N_People': 12, 'Estimation_Source': 'fictitious invoice'})
    return pd.DataFrame(rows)


def _french_names_inventory() -> pd.DataFrame:
    """The same inventory, with the legacy French column names."""
    df = _compact_inventory()
    inverse = {v: k for k, v in agb.COLUMN_ALIASES.items()}
    return df.rename(columns={c: inverse[c] for c in df.columns if c in inverse})


# ── Reference computed independently of the script ──────────────────────────

@pytest.fixture(scope='module')
def agri():
    if not AGRIBALYSE.exists():
        pytest.skip(f'Agribalyse database not found: {AGRIBALYSE}')
    return agb.load_agribalyse(str(AGRIBALYSE))


@pytest.fixture(scope='module')
def expected(agri):
    """Expected impacts, recomputed by hand from the database.

    For each product: candidate impact = factor per kg x mass. The best scenario
    keeps the candidate with the lowest Score_EF_mPt, the worst scenario the
    highest.
    """
    ref = {}
    for name, mass, _conf, codes in PRODUCTS:
        candidates = []
        for code in codes:
            product = agb.get_product_by_code(agri, code)
            assert product is not None, f'Agribalyse code not found: {code}'
            candidates.append(
                {ind: float(product[f'AGB_{ind}_per_kg']) * mass for ind in agb.IMPACT_COLS}
                | {'code': code, 'ef': float(product['AGB_Score_EF_mPt_per_kg'])})
        best = min(candidates, key=lambda c: c['ef'])
        worst = max(candidates, key=lambda c: c['ef'])
        ref[name] = {'best': best, 'worst': worst, 'n_candidates': len(candidates)}
    return ref


@pytest.fixture(scope='module')
def outputs(tmp_path_factory):
    """Run the script on the three inventory variants."""
    if not AGRIBALYSE.exists():
        pytest.skip('Agribalyse database not found')
    folder = tmp_path_factory.mktemp('synthetic')
    res = {}
    for name, builder in [('compact', _compact_inventory),
                          ('expanded', _expanded_inventory),
                          ('french', _french_names_inventory)]:
        inp = folder / f'inv_{name}.csv'
        builder().to_csv(inp, index=False)
        out = folder / f'out_{name}.xlsx'
        proc = subprocess.run(
            [sys.executable, str(TOOL), '-i', str(inp), '-a', str(AGRIBALYSE),
             '-o', str(out), '--group-col', 'Meal', '--n-mc', '200', '--seed', '7'],
            capture_output=True, text=True)
        assert proc.returncode == 0, f'failure on {name}:\n{proc.stdout}\n{proc.stderr}'
        assert out.exists(), f'no output written for {name}'
        res[name] = {'xlsx': out, 'stdout': proc.stdout}
    return res


# ── Tests ────────────────────────────────────────────────────────────────────

def test_sheet_names(outputs):
    """The output sheets carry the documented English names."""
    sheets = pd.ExcelFile(outputs['compact']['xlsx']).sheet_names
    for name in ('All_candidates', 'Best_case', 'Worst_case', 'MC_distributions'):
        assert name in sheets, f'missing sheet: {name} (found: {sheets})'


def test_all_candidates_expanded(outputs):
    """The compact format is expanded to one candidate per row."""
    df = pd.read_excel(outputs['compact']['xlsx'], sheet_name='All_candidates')
    n_expected = sum(len(p[3]) for p in PRODUCTS)
    assert len(df) == n_expected, f'{len(df)} expanded rows instead of {n_expected}'
    assert set(df['Product']) == {p[0] for p in PRODUCTS}


def test_one_row_per_product_in_scenarios(outputs):
    """Each scenario keeps exactly one candidate per product."""
    for sheet in ('Best_case', 'Worst_case'):
        df = pd.read_excel(outputs['compact']['xlsx'], sheet_name=sheet)
        assert len(df) == len(PRODUCTS), f'{sheet}: {len(df)} rows'
        assert df['Product'].is_unique


@pytest.mark.parametrize('scenario,key', [('Best_case', 'best'), ('Worst_case', 'worst')])
def test_impacts_match_manual_computation(outputs, expected, scenario, key):
    """Each impact equals factor per kg x mass, for all twenty indicators."""
    df = pd.read_excel(outputs['compact']['xlsx'], sheet_name=scenario).set_index('Product')
    gaps = []
    for name, _mass, _conf, _codes in PRODUCTS:
        for ind in agb.IMPACT_COLS:
            col = f'CALC_{ind}'
            if col not in df.columns:
                continue
            got = float(df.loc[name, col])
            target = expected[name][key][ind]
            if abs(target) < 1e-15:
                continue
            gap = abs(got - target) / abs(target)
            if gap > 1e-9:
                gaps.append(f'{name} / {ind}: {got:.6g} vs {target:.6g}')
    assert not gaps, 'gaps from the manual computation:\n  ' + '\n  '.join(gaps[:10])


def test_worst_not_below_best(outputs):
    """By construction the worst scenario cannot be better than the best one."""
    best = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Best_case').set_index('Product')
    worst = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Worst_case').set_index('Product')
    col = 'CALC_Score_EF_mPt'
    for name in best.index:
        assert float(worst.loc[name, col]) >= float(best.loc[name, col]) - 1e-12, name


def test_single_candidate_identical_in_both_scenarios(outputs):
    """A product without alternative gives exactly the same value on both sides."""
    best = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Best_case').set_index('Product')
    worst = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Worst_case').set_index('Product')
    for name, _m, _c, codes in PRODUCTS:
        if len(codes) == 1:
            assert np.isclose(float(best.loc[name, 'CALC_Score_EF_mPt']),
                              float(worst.loc[name, 'CALC_Score_EF_mPt']), rtol=1e-12), name


def test_compact_and_expanded_formats_agree(outputs):
    """Both input formats describe the same inventory and must converge."""
    for sheet in ('Best_case', 'Worst_case'):
        a = pd.read_excel(outputs['compact']['xlsx'], sheet_name=sheet).set_index('Product')
        b = pd.read_excel(outputs['expanded']['xlsx'], sheet_name=sheet).set_index('Product')
        assert set(a.index) == set(b.index)
        for name in a.index:
            assert np.isclose(float(a.loc[name, 'CALC_Score_EF_mPt']),
                              float(b.loc[name, 'CALC_Score_EF_mPt']), rtol=1e-12), \
                f'{sheet} / {name}'


def test_legacy_french_column_names_accepted(outputs):
    """An inventory written for the earlier version must run unchanged."""
    assert 'Legacy French column names renamed' in outputs['french']['stdout']
    a = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Worst_case').set_index('Product')
    b = pd.read_excel(outputs['french']['xlsx'], sheet_name='Worst_case').set_index('Product')
    for name in a.index:
        assert np.isclose(float(a.loc[name, 'CALC_Score_EF_mPt']),
                          float(b.loc[name, 'CALC_Score_EF_mPt']), rtol=1e-12), name


def test_monte_carlo_covers_groups_and_scenarios(outputs):
    """The Monte Carlo output is in long format, one draw per row."""
    mc = pd.read_excel(outputs['compact']['xlsx'], sheet_name='MC_distributions')
    for col in ('Scenario', 'Group', 'Simulation'):
        assert col in mc.columns, f'column {col} missing: {list(mc.columns)[:6]}'
    assert set(mc['Group']) == set(MEALS), f"groups found: {set(mc['Group'])}"
    assert len(set(mc['Scenario'])) == 2, set(mc['Scenario'])
    assert mc['Simulation'].max() == 200, mc['Simulation'].max()

    for (sc, grp), block in mc.groupby(['Scenario', 'Group']):
        series = pd.to_numeric(block['Score_EF_mPt'], errors='coerce').dropna()
        assert len(series) == 200, f'{sc}/{grp}: {len(series)} draws'
        assert (series > 0).all(), f'{sc}/{grp} has negative draws'
        assert series.std() > 0, f'{sc}/{grp} is constant, propagation did not happen'


def test_monte_carlo_brackets_deterministic(outputs):
    """The deterministic total of a group must fall within the range of its draws."""
    mc = pd.read_excel(outputs['compact']['xlsx'], sheet_name='MC_distributions')
    worst = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Worst_case')
    for grp in set(MEALS):
        det = worst.loc[worst['Meal'] == grp, 'CALC_Score_EF_mPt'].sum()
        block = mc[(mc['Group'] == grp) & (mc['Scenario'].astype(str).str.contains('orst|ire'))]
        series = pd.to_numeric(block['Score_EF_mPt'], errors='coerce').dropna()
        assert not series.empty, f'no draws for {grp}'
        assert series.min() <= det <= series.max(), \
            f'{grp}: deterministic {det:.4g} outside [{series.min():.4g}, {series.max():.4g}]'


def test_selected_candidate_matches_manual_choice(outputs):
    """Each scenario keeps the code decided by hand from the EF scores."""
    best = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Best_case').set_index('Product')
    worst = pd.read_excel(outputs['compact']['xlsx'], sheet_name='Worst_case').set_index('Product')
    for name, (code_b, code_w) in EXPECTED_CHOICE.items():
        got_b = str(best.loc[name, 'Agribalyse_Code']).strip()
        got_w = str(worst.loc[name, 'Agribalyse_Code']).strip()
        assert got_b == code_b, f'{name}: best = {got_b}, expected {code_b}'
        assert got_w == code_w, f'{name}: worst = {got_w}, expected {code_w}'


def test_uncertainty_grows_as_confidence_drops(agri):
    """The coefficient of variation applied follows the confidence level."""
    assert agb.CONFIDENCE_CV['HIGH'] < agb.CONFIDENCE_CV['MEDIUM'] < agb.CONFIDENCE_CV['LOW']
    draws = {level: agb._lognormal_samples(10.0, cv, 20000)
             for level, cv in agb.CONFIDENCE_CV.items()}
    spread = {level: float(np.std(t)) for level, t in draws.items()}
    assert spread['HIGH'] < spread['MEDIUM'] < spread['LOW'], spread
    for level, t in draws.items():
        assert abs(float(np.mean(t)) - 10.0) / 10.0 < 0.05, f'{level}: shifted mean'


# ── Per-row override of the mass CV ──────────────────────────────────────────

def test_mass_cv_overrides_confidence_level(tmp_path):
    """An explicit Mass_CV takes precedence over the CV derived from Match_Confidence."""
    if not AGRIBALYSE.exists():
        pytest.skip('Agribalyse database not found')
    base = _compact_inventory()

    def run(with_cv):
        df = base.copy()
        # Every row HIGH (CV 0.10), then optionally forced to a much wider CV.
        df['Match_Confidence'] = 'HIGH'
        if with_cv:
            df['Mass_CV'] = 0.80
        inp = tmp_path / f'inv_{with_cv}.csv'
        df.to_csv(inp, index=False)
        out = tmp_path / f'out_{with_cv}.xlsx'
        proc = subprocess.run(
            [sys.executable, str(TOOL), '-i', str(inp), '-a', str(AGRIBALYSE),
             '-o', str(out), '--group-col', 'Meal', '--n-mc', '400', '--seed', '3'],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        mc = pd.read_excel(out, sheet_name='MC_distributions')
        block = mc[mc['Scenario'].astype(str).str.contains('orst|ire')]
        return pd.to_numeric(block['Score_EF_mPt'], errors='coerce').dropna()

    narrow, wide = run(False), run(True)
    assert narrow.std() > 0 and wide.std() > 0
    # A CV forced to 0.80 clearly widens the spread. The factor stays below the ratio
    # of the CVs because the mass uncertainty combines with the DQR one, unchanged.
    assert wide.std() > 1.5 * narrow.std(), (narrow.std(), wide.std())
    # Widening must not move the central value.
    assert abs(wide.median() / narrow.median() - 1) < 0.20, (narrow.median(), wide.median())
