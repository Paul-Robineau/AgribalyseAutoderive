#!/usr/bin/env python3
"""
test_suite.py
=============
Comprehensive test suite for agribalyse_autoderive.py.

Covers:
  - Internal utilities (_normalize_code, _parse_codes_field, _dqr_to_cv, _lognormal_samples)
  - Agribalyse loading (columns, index, known products)
  - Code lookup (int, float, underscore, invalid)
  - Keyword search (exact and fuzzy)
  - Inventory loading and validation (expanded / compact / search modes)
  - Column role detection (context, traceability, group)
  - Candidate expansion (all modes, missing codes, unknown codes)
  - Impact computation
  - Best/worst selection (confidence_first and score_only)
  - Monte Carlo (reproducibility, positivity, statistical consistency)
  - Full regression pipeline against the IESF Knowledge Convention example

Usage:
    python -m pytest tests/test_suite.py -v

Prerequisites:
    - conda activate agribalyse-autoderive  (or: pip install -r requirements.txt)
    - Agribalyse XLSX file in data/ (shipped with the repository)
    - Example data at examples/iesf_knowledge_convention/ (shipped with the repository)
"""

import io
import json
import os
import sys
import textwrap
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── Path setup ───────────────────────────────────────────────────────────────
TESTS_DIR = Path(__file__).parent
ROOT_DIR  = TESTS_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from agribalyse_autoderive import (
    VERSION,
    IMPACT_COLS,
    DEFAULT_FUZZY_THRESHOLD,
    _normalize_code,
    _parse_codes_field,
    _dqr_to_cv,
    _lognormal_samples,
    _fuzzy_score,
    _HAS_RAPIDFUZZ,
    load_agribalyse,
    get_product_by_code,
    search_agribalyse,
    load_inventory,
    detect_column_roles,
    expand_candidates,
    compute_impacts,
    select_best_worst,
    run_monte_carlo,
    build_distributions_df,
    run_pipeline,
)

# ── Fixtures / constants ──────────────────────────────────────────────────────
AGRIBALYSE_PATH = ROOT_DIR / 'data' / 'AGRIBALYSE3.2_Tableur produits alimentaires_PublieAOUT25.xlsx'
EXAMPLE_DIR     = ROOT_DIR / 'examples' / 'iesf_knowledge_convention'
EXAMPLE_INV     = EXAMPLE_DIR / 'inventory.xlsx'
EXAMPLE_REF_JSON = EXAMPLE_DIR / 'reference_totals.json'

# Known codes verified against Agribalyse (used to validate lookup)
KNOWN_CODES = {
    18071: 'lait',          # lait UHT
    7100:  'pain',          # pain courant
    18020: 'the',           # thé infusé
    18071: 'lait',
    5214:  'vin',           # vin rouge
    31016: 'sucre',         # sucre
}

# Skip markers
NEEDS_AGRIBALYSE = pytest.mark.skipif(
    not AGRIBALYSE_PATH.exists(),
    reason=f"Agribalyse file not found: {AGRIBALYSE_PATH}"
)
NEEDS_EXAMPLE = pytest.mark.skipif(
    not (EXAMPLE_INV.exists() and EXAMPLE_REF_JSON.exists()),
    reason="Example data not found. Expected: examples/iesf_knowledge_convention/"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1.  UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeCode:
    def test_integer(self):
        assert _normalize_code(5214)    == "5214"
    def test_float(self):
        assert _normalize_code(5214.0)  == "5214"
    def test_str_plain(self):
        assert _normalize_code("5214")  == "5214"
    def test_str_float(self):
        assert _normalize_code("5214.0") == "5214"
    def test_underscore_variant(self):
        # Variant codes like "13024_1" must be preserved exactly
        assert _normalize_code("13024_1") == "13024_1"
    def test_leading_trailing_spaces(self):
        assert _normalize_code("  5214  ") == "5214"
    def test_negative(self):
        # Negative codes: unusual but should not break
        assert _normalize_code(-1)       == "-1"
    def test_large_code(self):
        assert _normalize_code(25404)    == "25404"


class TestParseCodesField:
    def test_single_int(self):
        assert _parse_codes_field(5001)           == ["5001"]
    def test_single_float(self):
        assert _parse_codes_field(5001.0)         == ["5001"]
    def test_single_str(self):
        assert _parse_codes_field("5001")         == ["5001"]
    def test_semicolon_separated(self):
        assert _parse_codes_field("5001;5011")    == ["5001", "5011"]
    def test_comma_separated(self):
        assert _parse_codes_field("5001,5011")    == ["5001", "5011"]
    def test_pipe_separated(self):
        assert _parse_codes_field("5001|5011")    == ["5001", "5011"]
    def test_spaces_around(self):
        assert _parse_codes_field(" 5001 ; 5011 ") == ["5001", "5011"]
    def test_three_codes(self):
        result = _parse_codes_field("18071;18020;31016")
        assert result == ["18071", "18020", "31016"]
    def test_nan(self):
        assert _parse_codes_field(float('nan')) == []
    def test_none(self):
        assert _parse_codes_field(None) == []
    def test_empty_string(self):
        assert _parse_codes_field("") == []
    def test_nan_string(self):
        assert _parse_codes_field("nan") == []
    def test_float_codes(self):
        assert _parse_codes_field("5001.0;5011.0") == ["5001", "5011"]
    def test_underscore_preserved(self):
        assert _parse_codes_field("13024_1") == ["13024_1"]


class TestDqrToCv:
    def test_dqr1(self):
        assert _dqr_to_cv(1.0) == pytest.approx(0.05)
    def test_dqr2(self):
        assert _dqr_to_cv(2.0) == pytest.approx(0.10)
    def test_dqr3(self):
        assert _dqr_to_cv(3.0) == pytest.approx(0.20)
    def test_dqr4(self):
        assert _dqr_to_cv(4.0) == pytest.approx(0.35)
    def test_dqr5(self):
        assert _dqr_to_cv(5.0) == pytest.approx(0.50)
    def test_interpolation(self):
        cv = _dqr_to_cv(2.5)
        assert 0.10 < cv < 0.20
    def test_clamp_below(self):
        assert _dqr_to_cv(0.0) == pytest.approx(0.05)
    def test_clamp_above(self):
        assert _dqr_to_cv(6.0) == pytest.approx(0.50)
    def test_string_input(self):
        assert _dqr_to_cv("2.0") == pytest.approx(0.10)
    def test_none_defaults_to_medium(self):
        cv = _dqr_to_cv(None)
        assert 0.05 < cv < 0.50


class TestLognormalSamples:
    def test_positive_mean(self):
        s = _lognormal_samples(1.0, 0.2, 1000)
        assert s.shape == (1000,)
        assert np.all(s > 0)
    def test_mean_preserved(self):
        # Mean of lognormal samples should be close to input mean
        s = _lognormal_samples(5.0, 0.1, 100_000)
        assert abs(np.mean(s) - 5.0) / 5.0 < 0.01
    def test_zero_mean(self):
        s = _lognormal_samples(0.0, 0.2, 10)
        assert np.all(s == 0.0)
    def test_zero_cv(self):
        s = _lognormal_samples(3.0, 0.0, 10)
        assert np.all(s == 3.0)
    def test_negative_mean(self):
        s = _lognormal_samples(-2.0, 0.1, 1000)
        assert np.all(s < 0)
        assert abs(np.mean(s) - (-2.0)) / 2.0 < 0.02
    def test_reproducible_with_seed(self):
        np.random.seed(42)
        s1 = _lognormal_samples(1.0, 0.2, 100)
        np.random.seed(42)
        s2 = _lognormal_samples(1.0, 0.2, 100)
        np.testing.assert_array_equal(s1, s2)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  AGRIBALYSE LOADING
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def agri():
    """Load Agribalyse once for the entire test module.
    Skips if Agribalyse file is not found (applied at test class level via NEEDS_AGRIBALYSE).
    """
    if not AGRIBALYSE_PATH.exists():
        pytest.skip(f"Agribalyse file not found: {AGRIBALYSE_PATH}")
    return load_agribalyse(str(AGRIBALYSE_PATH))


@NEEDS_AGRIBALYSE
class TestLoadAgribalyse:
    def test_loads_without_error(self, agri):
        assert agri is not None

    def test_expected_row_count(self, agri):
        # Agribalyse v3.2 should have ~2458 products
        assert 2400 <= len(agri) <= 2600

    def test_required_columns(self, agri):
        required = ['Code_AGB', 'Nom_FR', 'Groupe', 'SousGroupe', 'DQR',
                    'Score_EF_mPt', 'CC_kgCO2eq', '_nom_lower', '_code_str']
        for col in required:
            assert col in agri.columns, f"Missing column: {col}"

    def test_impact_columns_present(self, agri):
        for ind in IMPACT_COLS:
            assert ind in agri.columns, f"Missing impact column: {ind}"

    def test_nom_lower_is_lowercase(self, agri):
        sample = agri['_nom_lower'].dropna().head(50)
        for val in sample:
            assert val == val.lower(), f"Not lowercase: {val}"

    def test_code_str_no_float_suffix(self, agri):
        # No code should end with ".0" in _code_str
        bad = agri['_code_str'][agri['_code_str'].str.endswith('.0')]
        assert len(bad) == 0, f"Codes with .0 suffix: {bad.tolist()[:5]}"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_agribalyse('/nonexistent/path/agribalyse.xlsx')


# ─────────────────────────────────────────────────────────────────────────────
# 3.  PRODUCT LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
class TestGetProductByCode:
    def test_known_code_int(self, agri):
        p = get_product_by_code(agri, 7100)
        assert p is not None
        assert p['Agribalyse_Code'] is not None
        # Should be a bread product
        assert 'pain' in str(p['Agribalyse_Nom']).lower()

    def test_known_code_float(self, agri):
        p = get_product_by_code(agri, 7100.0)
        assert p is not None

    def test_known_code_str(self, agri):
        p = get_product_by_code(agri, '7100')
        assert p is not None

    def test_float_str_code(self, agri):
        # Code stored as "7100.0" (common when reading from CSV saved from Excel)
        p = get_product_by_code(agri, '7100.0')
        assert p is not None

    def test_invalid_code_returns_none(self, agri):
        p = get_product_by_code(agri, 9999999)
        assert p is None

    def test_string_invalid_code(self, agri):
        p = get_product_by_code(agri, 'invalid_code_xyz')
        assert p is None

    def test_result_has_all_impact_cols(self, agri):
        p = get_product_by_code(agri, 18071)
        assert p is not None
        for ind in IMPACT_COLS:
            assert f'AGB_{ind}_per_kg' in p, f"Missing AGB_{ind}_per_kg"

    def test_result_has_agribalyse_meta(self, agri):
        p = get_product_by_code(agri, 5214)
        assert p is not None
        for key in ('Agribalyse_Code', 'Agribalyse_Nom',
                    'Agribalyse_Groupe', 'Agribalyse_SousGroupe', 'Agribalyse_DQR'):
            assert key in p, f"Missing {key}"

    def test_all_known_codes(self, agri):
        # Verify all known example codes exist in Agribalyse
        codes = [18071, 7100, 18020, 5214, 31016, 5001, 5011, 5215,
                 19041, 13039, 25404, 25590, 25018, 8297]
        missing = []
        for code in codes:
            if get_product_by_code(agri, code) is None:
                missing.append(code)
        assert not missing, f"Codes not found in Agribalyse: {missing}"

    def test_code_lait_uht(self, agri):
        p = get_product_by_code(agri, 19041)
        assert p is not None
        assert 'lait' in str(p['Agribalyse_Nom']).lower()

    def test_dqr_is_numeric(self, agri):
        p = get_product_by_code(agri, 7100)
        dqr = p['Agribalyse_DQR']
        assert isinstance(dqr, (int, float))
        assert 0.5 <= float(dqr) <= 5.5


# ─────────────────────────────────────────────────────────────────────────────
# 4.  KEYWORD SEARCH
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
class TestSearchAgribalyse:
    def test_single_keyword(self, agri):
        results = search_agribalyse(agri, ['cafe'])
        assert len(results) > 0
        for r in results:
            assert 'caf' in r['Nom_FR'].lower()

    def test_multiple_keywords_and_logic(self, agri):
        # Both keywords must match
        results = search_agribalyse(agri, ['vin', 'rouge'])
        assert len(results) > 0
        for r in results:
            name = r['Nom_FR'].lower()
            assert 'vin' in name and 'rouge' in name

    def test_exclude_keyword(self, agri):
        results_all     = search_agribalyse(agri, ['cafe'])
        results_excl    = search_agribalyse(agri, ['cafe'], exclude=['instantane'])
        names_excl = [r['Nom_FR'].lower() for r in results_excl]
        # Excluded keyword should not appear
        for name in names_excl:
            assert 'instantane' not in name, f"Excluded keyword found: {name}"

    def test_no_match_returns_empty(self, agri):
        results = search_agribalyse(agri, ['zxqwerty_unlikely_xyz'])
        assert results == []

    def test_max_results_respected(self, agri):
        results = search_agribalyse(agri, ['pain'], max_results=3)
        assert len(results) <= 3

    def test_result_structure(self, agri):
        results = search_agribalyse(agri, ['the'], max_results=1)
        assert len(results) >= 1
        r = results[0]
        for key in ('Code_AGB', 'Nom_FR', 'Groupe', 'SousGroupe', 'Score_EF_mPt', 'CC_kgCO2eq'):
            assert key in r, f"Missing key {key} in search result"


# ─────────────────────────────────────────────────────────────────────────────
# 5.  INVENTORY LOADING
# ─────────────────────────────────────────────────────────────────────────────

def _make_minimal_csv(extra_cols: dict | None = None) -> str:
    """Build a minimal valid inventory CSV as a string."""
    rows = [
        {
            'Product': 'Pain',
            'Mass_kg':     1.5,
            'Match_Confidence':     'HIGH',
            'Agribalyse_Code':     7100,
        },
        {
            'Product': 'Café',
            'Mass_kg':     0.5,
            'Match_Confidence':     'MEDIUM',
            'Agribalyse_Code':     18071,
        },
    ]
    if extra_cols:
        for row in rows:
            row.update(extra_cols)
    df = pd.DataFrame(rows)
    return df.to_csv(index=False)


def _df_from_csv_str(csv_str: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv_str))


# ─────────────────────────────────────────────────────────────────────────────
# 5.  FUZZY MATCHING
# ─────────────────────────────────────────────────────────────────────────────

class TestFuzzyScore:
    """Unit tests for _fuzzy_score – independent of Agribalyse file."""

    def test_identical_strings(self):
        assert _fuzzy_score("pain", "pain") == pytest.approx(100.0)

    def test_accent_normalisation(self):
        # "cafe" should score perfectly against "café"
        score = _fuzzy_score("cafe", "cafe expresso non instantane non sucre pret a boire")
        assert score >= 80, f"Expected high score for accent match, got {score}"

    def test_word_order_independence(self):
        # token_set_ratio is order-independent
        a = _fuzzy_score("vin rouge", "vin rouge")
        b = _fuzzy_score("rouge vin", "vin rouge")
        assert abs(a - b) < 5, f"Word order should not matter: {a} vs {b}"

    def test_exact_token_subset(self):
        # Short query vs verbose product name
        score = _fuzzy_score("pain", "Pain courant francais")
        assert score >= 85

    def test_typo_tolerance(self):
        # Single character typo
        score = _fuzzy_score("vienoiserie", "viennoiserie")
        assert score >= 70, f"Typo score too low: {score}"

    def test_unrelated_strings_low_score(self):
        score = _fuzzy_score("chocolat", "vin rouge")
        assert score < 40, f"Unrelated strings should score low: {score}"

    def test_empty_query(self):
        assert _fuzzy_score("", "pain courant") == 0.0

    def test_empty_target(self):
        assert _fuzzy_score("pain", "") == 0.0

    def test_both_empty(self):
        assert _fuzzy_score("", "") == 0.0

    def test_returns_float(self):
        result = _fuzzy_score("vin", "vin rouge francais")
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0


@NEEDS_AGRIBALYSE
class TestFuzzySearch:
    """Integration tests for search_agribalyse with fuzzy=True."""

    def test_fuzzy_finds_what_exact_misses(self, agri):
        # "cafe filtre" – exact AND search finds nothing (no product with both words)
        exact   = search_agribalyse(agri, ['cafe', 'filtre'], fuzzy=False)
        fuzzy_r = search_agribalyse(agri, ['cafe', 'filtre'], fuzzy=True, fuzzy_threshold=50)
        assert len(exact)   == 0, f"Exact should find nothing, got {len(exact)}"
        assert len(fuzzy_r) >  0, "Fuzzy should find at least one match"

    def test_fuzzy_returns_score_matching(self, agri):
        results = search_agribalyse(agri, ['pain'], fuzzy=True, fuzzy_threshold=70)
        assert len(results) > 0
        for r in results:
            assert 'Score_Matching' in r, "Fuzzy results must include Score_Matching"
            assert 70 <= r['Score_Matching'] <= 100

    def test_exact_mode_no_score_matching(self, agri):
        results = search_agribalyse(agri, ['pain'], fuzzy=False)
        # Exact mode should NOT add Score_Matching
        for r in results:
            assert 'Score_Matching' not in r

    def test_fuzzy_sorted_by_score_desc(self, agri):
        results = search_agribalyse(agri, ['vin'], fuzzy=True, fuzzy_threshold=50, max_results=10)
        if len(results) >= 2:
            scores = [r['Score_Matching'] for r in results]
            assert scores == sorted(scores, reverse=True), "Fuzzy results must be score-sorted"

    def test_typo_recovery(self, agri):
        # "vienoiserie" (missing 'n') should still find viennoiserie products
        results = search_agribalyse(agri, ['vienoiserie'], fuzzy=True, fuzzy_threshold=60)
        names   = [r['Nom_FR'].lower() for r in results]
        assert any('vienno' in n or 'croissant' in n or 'pain' in n for n in names), (
            f"Typo recovery failed, top results: {names[:3]}")

    def test_word_order_insensitive(self, agri):
        r1 = search_agribalyse(agri, ['rouge', 'vin'], fuzzy=True, fuzzy_threshold=60, max_results=5)
        r2 = search_agribalyse(agri, ['vin', 'rouge'], fuzzy=True, fuzzy_threshold=60, max_results=5)
        codes1 = {r['Code_AGB'] for r in r1}
        codes2 = {r['Code_AGB'] for r in r2}
        # Should return a highly overlapping set (≥ 60% overlap)
        # Note: tie-breaking across equally-scored products may cause 1-2 differences
        overlap = codes1 & codes2
        min_len = min(len(codes1), len(codes2))
        assert len(overlap) >= max(1, int(0.6 * min_len)), (
            f"Word order should not change results significantly: {codes1} vs {codes2}")

    def test_threshold_filters_low_scores(self, agri):
        low_thr  = search_agribalyse(agri, ['pain'], fuzzy=True, fuzzy_threshold=30,  max_results=20)
        high_thr = search_agribalyse(agri, ['pain'], fuzzy=True, fuzzy_threshold=90,  max_results=20)
        assert len(low_thr) >= len(high_thr), \
            "Lower threshold should yield >= results than higher threshold"

    def test_exclude_still_works_in_fuzzy(self, agri):
        results = search_agribalyse(agri, ['vin'], exclude=['blanc'],
                                    fuzzy=True, fuzzy_threshold=50, max_results=10)
        for r in results:
            assert 'blanc' not in r['Nom_FR'].lower(), f"Excluded term found: {r['Nom_FR']}"

    def test_max_results_respected_fuzzy(self, agri):
        results = search_agribalyse(agri, ['lait'], fuzzy=True, fuzzy_threshold=50, max_results=3)
        assert len(results) <= 3

    def test_no_keywords_returns_empty(self, agri):
        results = search_agribalyse(agri, [], fuzzy=True)
        assert results == []

    def test_fuzzy_exact_match_scores_near_100(self, agri):
        # An exact product name should score very high
        results = search_agribalyse(agri, ['pain', 'courant'], fuzzy=True, fuzzy_threshold=50)
        if results:
            best = results[0]
            assert best['Score_Matching'] >= 80, (
                f"Exact-ish match scored only {best['Score_Matching']}: {best['Nom_FR']}")

    def test_very_low_threshold_returns_many(self, agri):
        results = search_agribalyse(agri, ['legume'], fuzzy=True, fuzzy_threshold=20, max_results=50)
        assert len(results) >= 5

    def test_very_high_threshold_returns_few_or_none(self, agri):
        results = search_agribalyse(agri, ['curry'], fuzzy=True, fuzzy_threshold=99, max_results=10)
        # "curry" may not be in Agribalyse – high threshold should return nothing or very few
        assert len(results) <= 5


@NEEDS_AGRIBALYSE
class TestFuzzyFallbackInExpandCandidates:
    """Test the auto-fuzzy-fallback when exact search fails in search mode."""

    def test_auto_fallback_triggers_warning(self, agri):
        # "cafe filtre" exact search finds nothing → should auto-retry with fuzzy
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Keywords\n"
               "Cafe filtre,1.0,HIGH,cafe filtre\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = expand_candidates(df, agri, roles)
        # Should produce rows via fuzzy fallback
        assert len(result) >= 1
        # Should emit a warning about fuzzy fallback
        fallback_warnings = [x for x in w if 'fuzzy' in str(x.message).lower()]
        assert len(fallback_warnings) >= 1, \
            f"Expected fuzzy fallback warning, got: {[str(x.message) for x in w]}"

    def test_explicit_fuzzy_column(self, agri):
        # Agribalyse_Fuzzy=True column should enable fuzzy search
        csv = ("Product,Mass_kg,Match_Confidence,"
               "Agribalyse_Keywords,Agribalyse_Fuzzy\n"
               "Plat legumes,5.0,LOW,legumes cuisines,True\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert len(result) >= 1, "Fuzzy search should find at least one legume product"

    def test_fuzzy_column_false_uses_exact(self, agri):
        # Agribalyse_Fuzzy=False should use exact search
        csv = ("Product,Mass_kg,Match_Confidence,"
               "Agribalyse_Keywords,Agribalyse_Fuzzy\n"
               "Pain,1.5,HIGH,pain courant,False\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        # "pain courant" exact search should work
        assert len(result) >= 1

    def test_score_matching_present_for_fuzzy_rows(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,"
               "Agribalyse_Keywords,Agribalyse_Fuzzy\n"
               "Dessert,2.0,MEDIUM,gateau chocolat,True\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        if len(result) > 0 and 'Score_Matching' in result.columns:
            assert (result['Score_Matching'] >= 0).all()


@NEEDS_AGRIBALYSE
class TestFuzzyCLI:
    def test_cli_fuzzy_flag(self, capsys):
        from agribalyse_autoderive import main
        main(['--agribalyse', str(AGRIBALYSE_PATH),
              '--search', 'cafe', 'filtre',
              '--fuzzy', '--max-results', '5'])
        captured = capsys.readouterr()
        assert 'fuzzy' in captured.out.lower()
        # Should show Score_Matching column header
        assert '%' in captured.out

    def test_cli_exact_no_results_shows_tip(self, capsys):
        from agribalyse_autoderive import main
        main(['--agribalyse', str(AGRIBALYSE_PATH),
              '--search', 'cafe', 'filtre',
              '--max-results', '5'])
        captured = capsys.readouterr()
        # When exact finds nothing, should suggest --fuzzy
        assert 'fuzzy' in captured.out.lower() or 'tip' in captured.out.lower()

    def test_cli_threshold_flag(self, capsys):
        from agribalyse_autoderive import main
        main(['--agribalyse', str(AGRIBALYSE_PATH),
              '--search', 'vienoiserie',
              '--fuzzy', '--threshold', '65', '--max-results', '5'])
        captured = capsys.readouterr()
        assert '65' in captured.out  # threshold shown in header


class TestDetectColumnRoles:
    def test_expanded_mode_detected(self):
        df = _df_from_csv_str(_make_minimal_csv())
        roles = detect_column_roles(df)
        assert roles['mode'] == 'expanded'

    def test_compact_mode_detected(self):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Codes\n"
               "Pain,1.5,HIGH,7100\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        assert roles['mode'] == 'compact'

    def test_search_mode_detected(self):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Keywords\n"
               "Pain,1.5,HIGH,pain courant\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        assert roles['mode'] == 'search'

    def test_missing_required_col_raises(self):
        csv = "Product,Match_Confidence,Agribalyse_Code\nPain,HIGH,7100\n"
        df = _df_from_csv_str(csv)
        with pytest.raises(ValueError, match="Mass_kg"):
            detect_column_roles(df)

    def test_missing_assignment_col_raises(self):
        csv = "Product,Mass_kg,Match_Confidence\nPain,1.5,HIGH\n"
        df = _df_from_csv_str(csv)
        with pytest.raises(ValueError, match="No Agribalyse assignment"):
            detect_column_roles(df)

    def test_context_cols_detected(self):
        csv_str = _make_minimal_csv({'WE': 'WE1', 'Type_Repas': 'Dejeuner'})
        df = _df_from_csv_str(csv_str)
        roles = detect_column_roles(df)
        assert 'WE' in roles['context_cols']
        assert 'Type_Repas' in roles['context_cols']

    def test_agb_cols_not_context(self):
        # AGB_ and CALC_ prefixed columns from a previous run should not be context
        csv_str = _make_minimal_csv({'AGB_CC_kgCO2eq_per_kg': 2.5})
        df = _df_from_csv_str(csv_str)
        roles = detect_column_roles(df)
        assert 'AGB_CC_kgCO2eq_per_kg' not in roles['context_cols']

    def test_traceability_cols_detected(self):
        csv_str = _make_minimal_csv({'N_People': 60, 'Estimation_Source': 'GEMRCN'})
        df = _df_from_csv_str(csv_str)
        roles = detect_column_roles(df)
        assert 'N_People' in roles['traceability_cols']
        assert 'Estimation_Source' in roles['traceability_cols']

    def test_group_col_defaults_to_first_context(self):
        csv_str = _make_minimal_csv({'WE': 'WE1', 'Type_Repas': 'Dejeuner'})
        df = _df_from_csv_str(csv_str)
        roles = detect_column_roles(df)
        assert roles['group_col'] == 'WE'

    def test_group_col_no_context(self):
        df = _df_from_csv_str(_make_minimal_csv())
        roles = detect_column_roles(df)
        assert roles['group_col'] is None

    def test_override_group_col(self):
        csv_str = _make_minimal_csv({'WE': 'WE1', 'Type_Repas': 'Dejeuner'})
        df = _df_from_csv_str(csv_str)
        roles = detect_column_roles(df, override_group_col='Type_Repas')
        assert roles['group_col'] == 'Type_Repas'

    def test_override_nonexistent_group_col_warns(self):
        df = _df_from_csv_str(_make_minimal_csv({'WE': 'WE1'}))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            roles = detect_column_roles(df, override_group_col='NONEXISTENT')
        assert any('nonexistent' in str(wn.message).lower() for wn in w)

    def test_invalid_confidence_warns(self):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,1.5,INVALID,7100\n")
        df = _df_from_csv_str(csv)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            detect_column_roles(df)
        assert any('INVALID' in str(wn.message) for wn in w)

    def test_product_identity_includes_context_and_product(self):
        csv_str = _make_minimal_csv({'WE': 'WE1'})
        df = _df_from_csv_str(csv_str)
        roles = detect_column_roles(df)
        assert 'WE' in roles['product_identity_cols']
        assert 'Product' in roles['product_identity_cols']


# ─────────────────────────────────────────────────────────────────────────────
# 6.  CANDIDATE EXPANSION
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
class TestExpandCandidates:
    def test_expanded_mode_basic(self, agri):
        df = _df_from_csv_str(_make_minimal_csv())
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert len(result) == 2
        assert 'Agribalyse_Nom' in result.columns
        assert 'AGB_CC_kgCO2eq_per_kg' in result.columns

    def test_expanded_mode_preserves_mass(self, agri):
        df = _df_from_csv_str(_make_minimal_csv())
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert set(result['Mass_kg'].tolist()) == {1.5, 0.5}

    def test_compact_mode_expands_two_codes(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Codes\n"
               "Biere,5.0,MEDIUM,5001;5011\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert len(result) == 2
        assert result['Product'].nunique() == 1

    def test_compact_mode_three_codes(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Codes\n"
               "Jus,3.0,HIGH,18071;5214;7100\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert len(result) == 3

    def test_invalid_code_skipped_with_warning(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,1.5,HIGH,7100\n"
               "Inconnu,1.0,LOW,9999999\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = expand_candidates(df, agri, roles)
        assert len(result) == 1  # invalid code row skipped
        assert result.iloc[0]['Product'] == 'Pain'

    def test_all_invalid_codes_raises(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "X,1.0,LOW,9999998\n"
               "Y,2.0,LOW,9999999\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with pytest.raises(ValueError, match="No valid rows"):
                expand_candidates(df, agri, roles)

    def test_empty_code_skipped_with_warning(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,1.5,HIGH,7100\n"
               "Vide,1.0,LOW,\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = expand_candidates(df, agri, roles)
        assert len(result) == 1
        assert any('vide' in str(wn.message).lower() or 'empty' in str(wn.message).lower()
                   for wn in w)

    def test_context_cols_preserved(self, agri):
        csv_str = _make_minimal_csv({'WE': 'WE1', 'Type': 'PDJ'})
        df = _df_from_csv_str(csv_str)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert 'WE' in result.columns
        assert (result['WE'] == 'WE1').all()

    def test_float_code_from_csv(self, agri):
        # Simulate code stored as float in CSV (e.g. "7100.0")
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,1.5,HIGH,7100.0\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert len(result) == 1

    def test_search_mode_basic(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Keywords\n"
               "Pain,1.5,HIGH,pain courant\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert len(result) >= 1
        assert 'Agribalyse_Nom' in result.columns

    def test_search_mode_exclude(self, agri):
        # Search for "vin" and exclude "blanc" – should only return non-blanc wines
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Keywords,Agribalyse_Exclude\n"
               "Vin rouge,1.0,MEDIUM,vin,blanc\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        result = expand_candidates(df, agri, roles)
        assert len(result) >= 1
        for name in result['Agribalyse_Nom']:
            assert 'blanc' not in str(name).lower(), f"Excluded term 'blanc' found in: {name}"

    def test_search_mode_no_match_skipped(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Keywords\n"
               "Inconnu,1.0,LOW,zxqwerty_xyz_impossible\n"
               "Pain,1.5,HIGH,pain courant\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = expand_candidates(df, agri, roles)
        assert len(result) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 7.  IMPACT COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
class TestComputeImpacts:
    @pytest.fixture
    def simple_expanded(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,2.0,HIGH,7100\n"
               "Cafe,1.0,MEDIUM,18071\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        return expand_candidates(df, agri, roles), agri

    def test_calc_cols_created(self, simple_expanded):
        df_exp, _ = simple_expanded
        result = compute_impacts(df_exp)
        for ind in IMPACT_COLS:
            assert f'CALC_{ind}' in result.columns, f"Missing CALC_{ind}"

    def test_cc_calculation_correct(self, simple_expanded):
        df_exp, agri = simple_expanded
        result = compute_impacts(df_exp)
        pain_row = result[result['Product'] == 'Pain'].iloc[0]
        expected = pain_row['AGB_CC_kgCO2eq_per_kg'] * pain_row['Mass_kg']
        assert abs(pain_row['CALC_CC_kgCO2eq'] - expected) < 1e-6

    def test_nan_factor_gives_nan(self):
        # If AGB factor is NaN, CALC should also be NaN
        df = pd.DataFrame({
            'Product': ['Test'],
            'Mass_kg': [2.0],
            'Match_Confidence': ['HIGH'],
            'Agribalyse_Code': [1],
            'Agribalyse_Nom': ['x'],
            'Agribalyse_Groupe': ['x'],
            'Agribalyse_SousGroupe': ['x'],
            'Agribalyse_DQR': [2.0],
            **{f'AGB_{ind}_per_kg': [np.nan] for ind in IMPACT_COLS},
        })
        result = compute_impacts(df)
        for ind in IMPACT_COLS:
            assert pd.isna(result[f'CALC_{ind}'].iloc[0])

    def test_zero_mass_gives_zero_impact(self, simple_expanded):
        df_exp, _ = simple_expanded
        df_exp = df_exp.copy()
        df_exp['Mass_kg'] = 0.0
        result = compute_impacts(df_exp)
        for ind in IMPACT_COLS:
            assert result[f'CALC_{ind}'].fillna(0.0).sum() == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 8.  BEST / WORST SELECTION
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
class TestSelectBestWorst:
    @pytest.fixture
    def multi_candidate_df(self, agri):
        """Two products, each with two Agribalyse candidates."""
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Biere,5.0,MEDIUM,5001\n"  # lower score
               "Biere,5.0,LOW,5011\n"     # higher score (LOW confidence)
               "Pain,2.0,HIGH,7100\n")    # single candidate
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        expanded = expand_candidates(df, agri, roles)
        return compute_impacts(expanded), roles['product_identity_cols']

    def test_best_has_one_row_per_product(self, multi_candidate_df):
        df, id_cols = multi_candidate_df
        best, _ = select_best_worst(df, id_cols)
        assert len(best) == 2  # Biere + Pain

    def test_worst_has_one_row_per_product(self, multi_candidate_df):
        df, id_cols = multi_candidate_df
        _, worst = select_best_worst(df, id_cols)
        assert len(worst) == 2

    def test_confidence_first_prefers_medium_over_low(self, multi_candidate_df):
        """With confidence_first=True, MEDIUM should be picked over LOW
        (5001 before 5011) for Bière – even if 5011 has higher or lower score."""
        df, id_cols = multi_candidate_df
        best, worst = select_best_worst(df, id_cols, confidence_first=True)
        biere_best  = best [best ['Product'] == 'Biere']
        biere_worst = worst[worst['Product'] == 'Biere']
        assert (biere_best ['Match_Confidence'] == 'MEDIUM').all()
        assert (biere_worst['Match_Confidence'] == 'MEDIUM').all()

    def test_score_only_selects_lowest_for_best(self, multi_candidate_df):
        df, id_cols = multi_candidate_df
        best, _ = select_best_worst(df, id_cols, confidence_first=False)
        biere = best[best['Product'] == 'Biere'].iloc[0]
        all_biere = df[df['Product'] == 'Biere']
        assert biere['AGB_Score_EF_mPt_per_kg'] == pytest.approx(
            all_biere['AGB_Score_EF_mPt_per_kg'].min())

    def test_score_only_selects_highest_for_worst(self, multi_candidate_df):
        df, id_cols = multi_candidate_df
        _, worst = select_best_worst(df, id_cols, confidence_first=False)
        biere = worst[worst['Product'] == 'Biere'].iloc[0]
        all_biere = df[df['Product'] == 'Biere']
        assert biere['AGB_Score_EF_mPt_per_kg'] == pytest.approx(
            all_biere['AGB_Score_EF_mPt_per_kg'].max())

    def test_single_candidate_same_best_worst(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,2.0,HIGH,7100\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        expanded = compute_impacts(expand_candidates(df, agri, roles))
        best, worst = select_best_worst(expanded, roles['product_identity_cols'])
        assert len(best)  == 1
        assert len(worst) == 1
        assert best.iloc[0]['Agribalyse_Code'] == worst.iloc[0]['Agribalyse_Code']

    def test_calc_cols_preserved_in_output(self, multi_candidate_df):
        df, id_cols = multi_candidate_df
        best, worst = select_best_worst(df, id_cols)
        for ind in IMPACT_COLS:
            assert f'CALC_{ind}' in best.columns
            assert f'CALC_{ind}' in worst.columns

    def test_no_private_cols_in_output(self, multi_candidate_df):
        df, id_cols = multi_candidate_df
        best, worst = select_best_worst(df, id_cols)
        for col in best.columns:
            assert not col.startswith('_'), f"Private column leaked: {col}"
        for col in worst.columns:
            assert not col.startswith('_'), f"Private column leaked: {col}"


# ─────────────────────────────────────────────────────────────────────────────
# 9.  MONTE CARLO
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
class TestMonteCarlo:
    @pytest.fixture
    def simple_scenario(self, agri):
        csv = ("WE,Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "WE1,Pain,2.0,HIGH,7100\n"
               "WE1,Cafe,0.5,MEDIUM,18071\n"
               "WE2,Vin,1.0,LOW,5214\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        expanded = expand_candidates(df, agri, roles)
        expanded  = compute_impacts(expanded)
        best, _ = select_best_worst(expanded, roles['product_identity_cols'])
        return best, roles

    def test_results_are_positive(self, simple_scenario):
        best_df, roles = simple_scenario
        mc = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=200, seed=1)
        for grp, inds in mc.items():
            for ind, arr in inds.items():
                assert np.all(arr >= 0), f"Negative MC values for {grp}/{ind}"

    def test_reproducible_with_seed(self, simple_scenario):
        best_df, roles = simple_scenario
        mc1 = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=100, seed=99)
        mc2 = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=100, seed=99)
        for grp in mc1:
            np.testing.assert_array_equal(
                mc1[grp]['CC_kgCO2eq'], mc2[grp]['CC_kgCO2eq'])

    def test_different_seeds_differ(self, simple_scenario):
        best_df, roles = simple_scenario
        mc1 = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=100, seed=1)
        mc2 = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=100, seed=2)
        # Arrays from different seeds should differ (with high probability)
        for grp in mc1:
            if mc1[grp]['CC_kgCO2eq'].sum() > 0:
                assert not np.allclose(mc1[grp]['CC_kgCO2eq'],
                                       mc2[grp]['CC_kgCO2eq'])
                break

    def test_mc_groups_match_group_col(self, simple_scenario):
        best_df, roles = simple_scenario
        mc = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=50, seed=0)
        assert set(mc.keys()) == {'WE1', 'WE2'}

    def test_mc_no_group_col_uses_total(self, simple_scenario):
        best_df, roles = simple_scenario
        mc = run_monte_carlo(best_df, None, roles['product_identity_cols'], n_mc=50, seed=0)
        assert set(mc.keys()) == {'Total'}

    def test_mc_mean_close_to_deterministic(self, agri):
        """MC mean should be ≈ deterministic value (within 5% for large N)."""
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,10.0,HIGH,7100\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        expanded = compute_impacts(expand_candidates(df, agri, roles))
        best, _ = select_best_worst(expanded, roles['product_identity_cols'])

        deterministic_cc = float(best['CALC_CC_kgCO2eq'].sum())
        mc = run_monte_carlo(best, None, roles['product_identity_cols'], n_mc=50_000, seed=0)
        mc_mean_cc = float(np.mean(mc['Total']['CC_kgCO2eq']))

        # Lognormal: mean = mu, so MC mean should ≈ deterministic (within 5%)
        rel_error = abs(mc_mean_cc - deterministic_cc) / (deterministic_cc + 1e-12)
        assert rel_error < 0.05, (
            f"MC mean {mc_mean_cc:.4f} too far from deterministic {deterministic_cc:.4f} "
            f"(relative error = {rel_error:.2%})"
        )

    def test_build_distributions_df(self, simple_scenario):
        best_df, roles = simple_scenario
        mc = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=10, seed=0)
        df_mc = build_distributions_df(mc, mc, n_mc=10)
        assert 'Scenario' in df_mc.columns
        assert 'Group'   in df_mc.columns
        assert 'Simulation' in df_mc.columns
        assert 'CC_kgCO2eq' in df_mc.columns
        # 2 scenarios × 2 WE groups × 10 simulations
        assert len(df_mc) == 2 * 2 * 10

    def test_distributions_df_simulation_column(self, simple_scenario):
        best_df, roles = simple_scenario
        mc = run_monte_carlo(best_df, 'WE', roles['product_identity_cols'], n_mc=5, seed=0)
        df_mc = build_distributions_df(mc, mc, n_mc=5)
        sim_vals = sorted(df_mc[df_mc['Group'] == 'WE1']['Simulation'].unique())
        assert sim_vals == [1, 2, 3, 4, 5]


# ─────────────────────────────────────────────────────────────────────────────
# 10. FULL PIPELINE – REFERENCE REGRESSION TEST (IESF Knowledge Convention)
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
@NEEDS_EXAMPLE
class TestExampleRegression:
    """Verify the generic script reproduces the reference values from the example.

    Tolerances:
      - Masses: ±1% (floating-point rounding)
      - CC impacts: ±1% (same codes, same masses)
    """

    @pytest.fixture(scope='class')
    def example_result(self):
        with open(EXAMPLE_REF_JSON) as f:
            ref = json.load(f)
        output_path = TESTS_DIR / 'test_example_output.xlsx'
        result = run_pipeline(
            inventory_path=str(EXAMPLE_INV),
            agribalyse_path=str(AGRIBALYSE_PATH),
            output_path=str(output_path),
            group_col='Group',
            n_mc=1000,
            seed=42,
        )
        return result, ref

    def test_detail_row_count(self, example_result):
        result, ref = example_result
        assert len(result['detail']) == ref['detail_rows'], (
            f"Expected {ref['detail_rows']} rows, got {len(result['detail'])}")

    def test_unique_products(self, example_result):
        result, ref = example_result
        n = result['detail']['Product'].nunique()
        assert n == ref['unique_products'], (
            f"Expected {ref['unique_products']} unique products, got {n}")

    def test_unique_agribalyse_codes(self, example_result):
        result, ref = example_result
        n = result['detail']['Agribalyse_Code'].nunique()
        assert n == ref['unique_agribalyse_codes'], (
            f"Expected {ref['unique_agribalyse_codes']} unique AGB codes, got {n}")

    def test_confidence_high_count(self, example_result):
        result, ref = example_result
        n = (result['detail']['Match_Confidence'] == 'HIGH').sum()
        assert n == ref['conf_HIGH']

    def test_confidence_medium_count(self, example_result):
        result, ref = example_result
        n = (result['detail']['Match_Confidence'] == 'MEDIUM').sum()
        assert n == ref['conf_MEDIUM']

    def test_confidence_low_count(self, example_result):
        result, ref = example_result
        n = (result['detail']['Match_Confidence'] == 'LOW').sum()
        assert n == ref['conf_LOW']

    def test_total_mass_kg(self, example_result):
        result, ref = example_result
        id_cols = ['Group', 'Meal_Type', 'Invoice_Item', 'Product']
        best_u = result['best'].drop_duplicates(subset=id_cols)
        total  = best_u['Mass_kg'].sum()
        assert abs(total - ref['total_mass_kg']) / ref['total_mass_kg'] < 0.01, (
            f"Total mass {total:.1f} kg ≠ reference {ref['total_mass_kg']:.1f} kg")

    def test_cc_best_total(self, example_result):
        result, ref = example_result
        id_cols = ['Group', 'Meal_Type', 'Invoice_Item', 'Product']
        best_u = result['best'].drop_duplicates(subset=id_cols)
        cc     = best_u['CALC_CC_kgCO2eq'].sum()
        assert abs(cc - ref['cc_best_kgCO2eq']) / ref['cc_best_kgCO2eq'] < 0.01, (
            f"CC best {cc:.1f} ≠ reference {ref['cc_best_kgCO2eq']:.1f} kgCO2eq")

    def test_cc_worst_total(self, example_result):
        result, ref = example_result
        id_cols = ['Group', 'Meal_Type', 'Invoice_Item', 'Product']
        worst_u = result['worst'].drop_duplicates(subset=id_cols)
        cc      = worst_u['CALC_CC_kgCO2eq'].sum()
        assert abs(cc - ref['cc_worst_kgCO2eq']) / ref['cc_worst_kgCO2eq'] < 0.01, (
            f"CC worst {cc:.1f} ≠ reference {ref['cc_worst_kgCO2eq']:.1f} kgCO2eq")

    def test_per_group_cc_best(self, example_result):
        result, ref = example_result
        id_cols = ['Group', 'Meal_Type', 'Invoice_Item', 'Product']
        best_u  = result['best'].drop_duplicates(subset=id_cols)
        for we, expected in ref['per_group'].items():
            cc = best_u[best_u['Group'] == we]['CALC_CC_kgCO2eq'].sum()
            exp = expected['cc_best']
            assert abs(cc - exp) / (exp + 1e-6) < 0.01, (
                f"{we} CC best {cc:.1f} ≠ reference {exp:.1f}")

    def test_per_group_cc_worst(self, example_result):
        result, ref = example_result
        id_cols = ['Group', 'Meal_Type', 'Invoice_Item', 'Product']
        worst_u = result['worst'].drop_duplicates(subset=id_cols)
        for we, expected in ref['per_group'].items():
            cc = worst_u[worst_u['Group'] == we]['CALC_CC_kgCO2eq'].sum()
            exp = expected['cc_worst']
            assert abs(cc - exp) / (exp + 1e-6) < 0.01, (
                f"{we} CC worst {cc:.1f} ≠ reference {exp:.1f}")

    def test_mc_cc_best_median_in_range(self, example_result):
        """MC median should be within ±5 % of deterministic for large N."""
        result, ref = example_result
        mc_best = result['mc_best']
        total_cc = sum(mc_best[g]['CC_kgCO2eq'] for g in mc_best)
        p50 = float(np.percentile(total_cc, 50))
        ref_cc = ref['cc_best_kgCO2eq']
        assert abs(p50 - ref_cc) / ref_cc < 0.05, (
            f"MC median CC best {p50:.0f} too far from {ref_cc:.0f} kgCO2eq")

    def test_output_excel_has_4_sheets(self, example_result):
        output_path = TESTS_DIR / 'test_example_output.xlsx'
        xl = pd.ExcelFile(output_path)
        assert set(xl.sheet_names) == {
            'All_candidates', 'Best_case',
            'Worst_case', 'MC_distributions'}

    def test_detail_sheet_has_agb_columns(self, example_result):
        result, _ = example_result
        for ind in IMPACT_COLS:
            assert f'AGB_{ind}_per_kg' in result['detail'].columns

    def test_detail_sheet_has_calc_columns(self, example_result):
        result, _ = example_result
        for ind in IMPACT_COLS:
            assert f'CALC_{ind}' in result['detail'].columns

    def test_no_private_cols_in_detail(self, example_result):
        result, _ = example_result
        for col in result['detail'].columns:
            assert not col.startswith('_'), f"Private column in output: {col}"

    def test_distributions_mc_shape(self, example_result):
        result, _ = example_result
        df_mc = result['distributions']
        # 4 WE groups × 2 scenarios × 1000 simulations
        expected_rows = 4 * 2 * 1000
        assert len(df_mc) == expected_rows, (
            f"Expected {expected_rows} MC rows, got {len(df_mc)}")

    def test_we_context_col_in_output(self, example_result):
        result, _ = example_result
        assert 'Group' in result['detail'].columns
        assert 'Group' in result['best'].columns


# ─────────────────────────────────────────────────────────────────────────────
# 11. EDGE CASES & ROBUSTNESS
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
class TestEdgeCases:
    def test_single_product_single_code(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,1.0,HIGH,7100\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        exp = compute_impacts(expand_candidates(df, agri, roles))
        best, worst = select_best_worst(exp, roles['product_identity_cols'])
        assert len(best) == 1 and len(worst) == 1

    def test_very_small_mass(self, agri):
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Sel,0.0001,HIGH,31016\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        exp = compute_impacts(expand_candidates(df, agri, roles))
        assert exp['CALC_CC_kgCO2eq'].iloc[0] >= 0

    def test_many_identical_products_same_code(self, agri):
        """Same product repeated N times – expansion should produce N rows."""
        rows = [{'Product': f'Prod{i}', 'Mass_kg': float(i+1),
                 'Match_Confidence': 'HIGH', 'Agribalyse_Code': 7100}
                for i in range(20)]
        df = pd.DataFrame(rows)
        roles = detect_column_roles(df)
        exp = expand_candidates(df, agri, roles)
        assert len(exp) == 20

    def test_missing_dqr_uses_default(self, agri):
        """If DQR is NaN, MC should still run without error."""
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,1.0,HIGH,7100\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        exp = compute_impacts(expand_candidates(df, agri, roles))
        exp['Agribalyse_DQR'] = np.nan  # Force missing DQR
        best, _ = select_best_worst(exp, roles['product_identity_cols'])
        # Should not raise
        mc = run_monte_carlo(best, None, roles['product_identity_cols'], n_mc=50, seed=0)
        assert 'Total' in mc

    def test_all_high_confidence_products(self, agri):
        """All HIGH confidence: CV on masses = 0.10."""
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Pain,2.0,HIGH,7100\nCafe,1.0,HIGH,18071\nThé,0.5,HIGH,18020\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        exp = compute_impacts(expand_candidates(df, agri, roles))
        best, _ = select_best_worst(exp, roles['product_identity_cols'])
        mc = run_monte_carlo(best, None, roles['product_identity_cols'], n_mc=500, seed=0)
        # HIGH confidence → tight distribution, CV ≈ 10%
        arr = mc['Total']['CC_kgCO2eq']
        cv_obs = np.std(arr) / np.mean(arr)
        # Should be < 25% (rough upper bound for low uncertainty)
        assert cv_obs < 0.25

    def test_all_low_confidence_products(self, agri):
        """All LOW confidence: CV on masses = 0.35 → wider distribution."""
        csv = ("Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "Proxy,5.0,LOW,25018\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        exp = compute_impacts(expand_candidates(df, agri, roles))
        best, _ = select_best_worst(exp, roles['product_identity_cols'])
        mc_low = run_monte_carlo(best, None, roles['product_identity_cols'], n_mc=2000, seed=0)
        # HIGH confidence
        df_h = df.copy(); df_h['Match_Confidence'] = 'HIGH'
        roles_h = detect_column_roles(df_h)
        exp_h = compute_impacts(expand_candidates(df_h, agri, roles_h))
        best_h, _ = select_best_worst(exp_h, roles_h['product_identity_cols'])
        mc_high = run_monte_carlo(best_h, None, roles_h['product_identity_cols'], n_mc=2000, seed=0)

        cv_low  = np.std(mc_low ['Total']['CC_kgCO2eq']) / np.mean(mc_low ['Total']['CC_kgCO2eq'])
        cv_high = np.std(mc_high['Total']['CC_kgCO2eq']) / np.mean(mc_high['Total']['CC_kgCO2eq'])
        assert cv_low > cv_high, "LOW confidence should give wider distribution than HIGH"

    def test_multiple_context_groups(self, agri):
        csv = ("Categorie,Product,Mass_kg,Match_Confidence,Agribalyse_Code\n"
               "A,Pain,2.0,HIGH,7100\n"
               "A,Cafe,0.5,MEDIUM,18071\n"
               "B,Vin,1.0,LOW,5214\n"
               "B,Biere,2.0,MEDIUM,5001\n")
        df = _df_from_csv_str(csv)
        roles = detect_column_roles(df)
        exp = compute_impacts(expand_candidates(df, agri, roles))
        best, _ = select_best_worst(exp, roles['product_identity_cols'])
        mc = run_monte_carlo(best, 'Categorie', roles['product_identity_cols'], n_mc=100, seed=0)
        assert set(mc.keys()) == {'A', 'B'}
        assert mc['A']['CC_kgCO2eq'].sum() > 0
        assert mc['B']['CC_kgCO2eq'].sum() > 0


# ─────────────────────────────────────────────────────────────────────────────
# 12. CLI SMOKE TEST
# ─────────────────────────────────────────────────────────────────────────────

@NEEDS_AGRIBALYSE
@NEEDS_EXAMPLE
class TestCLI:
    def test_search_mode_no_error(self, capsys):
        """--search should print results without error."""
        from agribalyse_autoderive import main
        main(['--agribalyse', str(AGRIBALYSE_PATH),
              '--search', 'pain', 'courant',
              '--max-results', '3'])
        captured = capsys.readouterr()
        assert 'pain' in captured.out.lower()

    def test_pipeline_produces_output(self, tmp_path):
        out = tmp_path / 'test_out.xlsx'
        from agribalyse_autoderive import main
        main([
            '--inventory',   str(EXAMPLE_INV),
            '--agribalyse',  str(AGRIBALYSE_PATH),
            '--output',      str(out),
            '--n-mc',        '100',
            '--group-col',   'Group',
        ])
        assert out.exists()
        xl = pd.ExcelFile(out)
        assert 'All_candidates' in xl.sheet_names

    def test_missing_inventory_exits_with_error(self):
        from agribalyse_autoderive import main
        with pytest.raises(SystemExit) as exc:
            main([
                '--inventory',  '/nonexistent/inventory.csv',
                '--agribalyse', str(AGRIBALYSE_PATH),
            ])
        assert exc.value.code != 0

    def test_missing_agribalyse_exits_with_error(self):
        from agribalyse_autoderive import main
        with pytest.raises(SystemExit) as exc:
            main([
                '--inventory',  str(EXAMPLE_INV),
                '--agribalyse', '/nonexistent/agribalyse.xlsx',
            ])
        assert exc.value.code != 0


if __name__ == '__main__':
    # Quick smoke run without pytest
    import subprocess
    result = subprocess.run(
        ['python', '-m', 'pytest', __file__, '-v', '--tb=short'],
        cwd=str(ROOT_DIR)
    )
    sys.exit(result.returncode)
