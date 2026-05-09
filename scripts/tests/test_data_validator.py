"""
Unit tests for data_validator — Stage 1 commitment validation.

Run with:
    python -m unittest scripts/tests/test_data_validator.py
"""

import sys
import os
import unittest

# Ensure the repo root is on sys.path so `scripts.utils` is importable.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.utils.data_validator import validate_commitments
from scripts.utils.data_normalizer import normalize_commitments


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ON_DEMAND = 0.10  # $0.10/hr

VALID_COMMITMENT_RESERVED_1YR_NO_UPFRONT = {
    'term': '1yr',
    'payment': 'no-upfront',
    'product': 'reserved',
    'priceUSD_hourly': 0.065,
    'effectiveHourlyUSD': 0.065,
    'savingsVsOnDemandPct': 35.0,
}

VALID_COMMITMENT_SAVINGS_PLAN_3YR_PARTIAL = {
    'term': '3yr',
    'payment': 'partial-upfront',
    'product': 'savings-plan',
    'priceUSD_hourly': 0.025,
    'effectiveHourlyUSD': 0.040,
    'savingsVsOnDemandPct': 60.0,
}

VALID_COMMITMENT_CUD_1YR_FLEXIBLE = {
    'term': '1yr',
    'payment': 'flexible',
    'product': 'cud',
    'priceUSD_hourly': 0.070,
    'effectiveHourlyUSD': 0.070,
    'savingsVsOnDemandPct': 30.0,
}

VALID_COMMITMENT_ALL_UPFRONT = {
    'term': '3yr',
    'payment': 'all-upfront',
    'product': 'reserved',
    'priceUSD_hourly': 0.0,
    'effectiveHourlyUSD': 0.035,
    'savingsVsOnDemandPct': 65.0,
}


class TestValidateCommitmentsValid(unittest.TestCase):
    """validate_commitments should return (True, []) for well-formed input."""

    def test_empty_list_is_valid(self):
        ok, errors = validate_commitments([], ON_DEMAND)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])

    def test_single_valid_entry(self):
        ok, errors = validate_commitments([VALID_COMMITMENT_RESERVED_1YR_NO_UPFRONT], ON_DEMAND)
        self.assertTrue(ok, errors)

    def test_multiple_valid_entries(self):
        fixtures = [
            VALID_COMMITMENT_RESERVED_1YR_NO_UPFRONT,
            VALID_COMMITMENT_SAVINGS_PLAN_3YR_PARTIAL,
            VALID_COMMITMENT_CUD_1YR_FLEXIBLE,
            VALID_COMMITMENT_ALL_UPFRONT,
        ]
        ok, errors = validate_commitments(fixtures, ON_DEMAND)
        self.assertTrue(ok, errors)

    def test_zero_price_all_upfront_is_valid(self):
        """priceUSD_hourly of 0 is valid for all-upfront products."""
        ok, errors = validate_commitments([VALID_COMMITMENT_ALL_UPFRONT], ON_DEMAND)
        self.assertTrue(ok, errors)


class TestValidateCommitmentsInvalid(unittest.TestCase):
    """validate_commitments should return (False, [<messages>]) for bad data."""

    def _make(self, **overrides):
        base = dict(VALID_COMMITMENT_RESERVED_1YR_NO_UPFRONT)
        base.update(overrides)
        return base

    def test_missing_required_field_term(self):
        bad = {k: v for k, v in VALID_COMMITMENT_RESERVED_1YR_NO_UPFRONT.items() if k != 'term'}
        ok, errors = validate_commitments([bad], ON_DEMAND)
        self.assertFalse(ok)
        self.assertTrue(any('term' in e for e in errors), errors)

    def test_missing_required_field_product(self):
        bad = {k: v for k, v in VALID_COMMITMENT_RESERVED_1YR_NO_UPFRONT.items() if k != 'product'}
        ok, errors = validate_commitments([bad], ON_DEMAND)
        self.assertFalse(ok)

    def test_invalid_term(self):
        ok, errors = validate_commitments([self._make(term='5yr')], ON_DEMAND)
        self.assertFalse(ok)
        self.assertTrue(any('term' in e for e in errors), errors)

    def test_invalid_payment(self):
        ok, errors = validate_commitments([self._make(payment='monthly')], ON_DEMAND)
        self.assertFalse(ok)
        self.assertTrue(any('payment' in e for e in errors), errors)

    def test_invalid_product(self):
        ok, errors = validate_commitments([self._make(product='spot')], ON_DEMAND)
        self.assertFalse(ok)
        self.assertTrue(any('product' in e for e in errors), errors)

    def test_negative_price_hourly(self):
        ok, errors = validate_commitments([self._make(priceUSD_hourly=-0.01)], ON_DEMAND)
        self.assertFalse(ok)
        self.assertTrue(any('priceUSD_hourly' in e for e in errors), errors)

    def test_savings_above_100(self):
        ok, errors = validate_commitments([self._make(savingsVsOnDemandPct=101)], ON_DEMAND)
        self.assertFalse(ok)
        self.assertTrue(any('savingsVsOnDemandPct' in e for e in errors), errors)

    def test_savings_below_0(self):
        ok, errors = validate_commitments([self._make(savingsVsOnDemandPct=-5)], ON_DEMAND)
        self.assertFalse(ok)

    def test_effective_exceeds_on_demand(self):
        """effectiveHourlyUSD > on_demand_hourly should raise a warning error."""
        ok, errors = validate_commitments([self._make(effectiveHourlyUSD=0.15)], ON_DEMAND)
        self.assertFalse(ok)
        self.assertTrue(any('effectiveHourlyUSD' in e for e in errors), errors)

    def test_not_a_list(self):
        ok, errors = validate_commitments({'term': '1yr'}, ON_DEMAND)  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertTrue(any('list' in e for e in errors), errors)


class TestNormalizeCommitments(unittest.TestCase):
    """normalize_commitments should compute effectiveHourlyUSD and savingsVsOnDemandPct."""

    def test_no_upfront_1yr(self):
        raw = [{'term': '1yr', 'payment': 'no-upfront', 'product': 'reserved', 'priceUSD_hourly': 0.065}]
        result = normalize_commitments(raw, on_demand_hourly=0.10)
        self.assertEqual(len(result), 1)
        r = result[0]
        self.assertAlmostEqual(r['effectiveHourlyUSD'], 0.065, places=4)
        self.assertAlmostEqual(r['savingsVsOnDemandPct'], 35.0, places=1)

    def test_all_upfront_3yr(self):
        # $262.80 upfront over 26280 hours = $0.01/hr amortised; hourly rate = 0
        raw = [{'term': '3yr', 'payment': 'all-upfront', 'product': 'reserved',
                'priceUSD_hourly': 0.0, 'upfront_usd': 262.80}]
        result = normalize_commitments(raw, on_demand_hourly=0.10)
        r = result[0]
        self.assertAlmostEqual(r['effectiveHourlyUSD'], 0.01, places=4)
        self.assertAlmostEqual(r['savingsVsOnDemandPct'], 90.0, places=1)

    def test_partial_upfront_1yr(self):
        # Upfront: $87.60 over 8760 hours = $0.01/hr amortised; hourly: $0.05
        raw = [{'term': '1yr', 'payment': 'partial-upfront', 'product': 'reserved',
                'priceUSD_hourly': 0.05, 'upfront_usd': 87.60}]
        result = normalize_commitments(raw, on_demand_hourly=0.10)
        r = result[0]
        self.assertAlmostEqual(r['effectiveHourlyUSD'], 0.06, places=4)
        self.assertAlmostEqual(r['savingsVsOnDemandPct'], 40.0, places=1)

    def test_zero_on_demand_savings_is_zero(self):
        raw = [{'term': '1yr', 'payment': 'no-upfront', 'product': 'cud', 'priceUSD_hourly': 0.05}]
        result = normalize_commitments(raw, on_demand_hourly=0.0)
        self.assertEqual(result[0]['savingsVsOnDemandPct'], 0.0)

    def test_empty_input(self):
        self.assertEqual(normalize_commitments([], 0.10), [])

    def test_malformed_entry_skipped(self):
        raw = [
            {'term': '1yr', 'payment': 'no-upfront', 'product': 'reserved', 'priceUSD_hourly': 0.065},
            {'term': '1yr', 'payment': 'no-upfront', 'product': 'reserved', 'priceUSD_hourly': 'bad-value'},
        ]
        # 'bad-value' causes a ValueError in float(); the second entry is skipped
        result = normalize_commitments(raw, on_demand_hourly=0.10)
        # Only the first entry should survive
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]['effectiveHourlyUSD'], 0.065, places=4)

    def test_normalised_output_passes_validator(self):
        """Round-trip: normalize then validate."""
        from scripts.utils.data_validator import validate_commitments as _val
        raw = [
            {'term': '1yr', 'payment': 'no-upfront', 'product': 'reserved', 'priceUSD_hourly': 0.065},
            {'term': '3yr', 'payment': 'all-upfront', 'product': 'reserved', 'priceUSD_hourly': 0.0, 'upfront_usd': 175.2},
        ]
        normalised = normalize_commitments(raw, on_demand_hourly=0.10)
        ok, errors = _val(normalised, on_demand_hourly=0.10)
        self.assertTrue(ok, errors)


if __name__ == '__main__':
    unittest.main()
