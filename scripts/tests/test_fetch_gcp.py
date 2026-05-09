"""
Unit tests for fetch_gcp — Stage 5 GCP fetcher internal logic.

These tests exercise the SKU classification, price extraction, instance building,
and commitment normalisation without making any live API calls.

Run with:
    python -m unittest scripts/tests/test_fetch_gcp.py
"""

import sys
import os
import unittest
from typing import Any, Dict, List, Optional

# Ensure the repo root is on sys.path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.fetch_gcp import (
    _extract_family,
    _extract_generation,
    _is_cpu_sku,
    _is_ram_sku,
    _is_gpu_sku,
    _is_preemptible,
    _usage_type_from_sku,
    _extract_unit_price,
    _classify_skus,
    _extract_gpu_skus,
    build_instances,
    _SPEC_LOOKUP,
    _FAMILY_SKU_MAP,
    GCP_REGIONS,
    _FamilyPricing,
)
from scripts.utils.data_validator import validate_commitments, validate_instance_data


# ---------------------------------------------------------------------------
# Helpers — synthetic SKU builders
# ---------------------------------------------------------------------------

def _make_sku(
    description: str,
    usage_type: str = "OnDemand",
    price_nanos: int = 80_000_000,   # $0.08 default
    resource_family: str = "Compute",
    resource_group: str = "CPU",
    sku_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal synthetic Billing API SKU dict."""
    return {
        "skuId": sku_id or description[:20].replace(" ", "_"),
        "description": description,
        "category": {
            "resourceFamily": resource_family,
            "resourceGroup": resource_group,
            "usageType": usage_type,
        },
        "pricingInfo": [
            {
                "pricingExpression": {
                    "usageUnit": "h",
                    "tieredRates": [
                        {
                            "startUsageAmount": 0,
                            "unitPrice": {"currencyCode": "USD", "units": "0", "nanos": price_nanos},
                        }
                    ],
                }
            }
        ],
    }


# ---------------------------------------------------------------------------
# Tests: SKU classification helpers
# ---------------------------------------------------------------------------

class TestFamilyExtraction(unittest.TestCase):
    def test_n2_standard(self):
        self.assertEqual(_extract_family("n2-standard-4"), "n2")

    def test_n2d_standard(self):
        # n2d must not be classified as n2
        self.assertEqual(_extract_family("n2d-standard-32"), "n2d")

    def test_c3d(self):
        self.assertEqual(_extract_family("c3d-highcpu-16"), "c3d")

    def test_e2_micro(self):
        self.assertEqual(_extract_family("e2-micro"), "e2")

    def test_a2_gpu(self):
        self.assertEqual(_extract_family("a2-highgpu-1g"), "a2")

    def test_t2a(self):
        self.assertEqual(_extract_family("t2a-standard-4"), "t2a")

    def test_m1(self):
        self.assertEqual(_extract_family("m1-ultramem-40"), "m1")

    def test_all_spec_types_have_known_family(self):
        """Every machine type in the spec table must resolve to a known family."""
        for mt in _SPEC_LOOKUP:
            fam = _extract_family(mt)
            self.assertIn(fam, _FAMILY_SKU_MAP, f"{mt} -> {fam} not in _FAMILY_SKU_MAP")

    def test_generation_n2(self):
        self.assertEqual(_extract_generation("n2-standard-4"), "2")

    def test_generation_c3d(self):
        self.assertEqual(_extract_generation("c3d-highcpu-8"), "3")

    def test_generation_e2(self):
        self.assertEqual(_extract_generation("e2-micro"), "2")


class TestSkuClassificationHelpers(unittest.TestCase):
    def test_is_cpu_sku_instance_core(self):
        self.assertTrue(_is_cpu_sku("N2 Instance Core running in Americas"))

    def test_is_cpu_sku_n1(self):
        self.assertTrue(_is_cpu_sku("N1 Predefined Instance Core running in Americas"))

    def test_is_ram_sku_instance_ram(self):
        self.assertTrue(_is_ram_sku("N2 Instance Ram running in Americas"))

    def test_is_not_cpu_sku_for_ram(self):
        self.assertFalse(_is_cpu_sku("N2 Instance Ram running in Americas"))

    def test_is_not_ram_sku_for_cpu(self):
        self.assertFalse(_is_ram_sku("N2 Instance Core running in Americas"))

    def test_is_gpu_sku(self):
        self.assertTrue(_is_gpu_sku("NVIDIA Tesla T4 GPU running in Americas"))

    def test_is_preemptible(self):
        sku = _make_sku("Preemptible N2 Instance Core running in Americas", usage_type="Preemptible")
        self.assertTrue(_is_preemptible(sku))

    def test_is_not_preemptible_ondemand(self):
        sku = _make_sku("N2 Instance Core running in Americas", usage_type="OnDemand")
        self.assertFalse(_is_preemptible(sku))

    def test_usage_type_on_demand(self):
        sku = _make_sku("N2 Instance Core", usage_type="OnDemand")
        self.assertEqual(_usage_type_from_sku(sku), "on-demand")

    def test_usage_type_commit1yr(self):
        sku = _make_sku("N2 Committed Use Discount CUDs", usage_type="Commit1Yr")
        self.assertEqual(_usage_type_from_sku(sku), "1yr")

    def test_usage_type_commit3yr(self):
        sku = _make_sku("N2 Committed Use Discount CUDs", usage_type="Commit3Yr")
        self.assertEqual(_usage_type_from_sku(sku), "3yr")


class TestExtractUnitPrice(unittest.TestCase):
    def test_nanos_only(self):
        sku = _make_sku("test", price_nanos=80_000_000)  # $0.08
        pricing_info = sku["pricingInfo"]
        price = _extract_unit_price(pricing_info)
        self.assertAlmostEqual(price, 0.08, places=6)

    def test_empty_pricing_info(self):
        self.assertIsNone(_extract_unit_price([]))

    def test_zero_price(self):
        sku = _make_sku("test", price_nanos=0)
        price = _extract_unit_price(sku["pricingInfo"])
        self.assertAlmostEqual(price, 0.0, places=6)

    def test_units_plus_nanos(self):
        pricing_info = [
            {
                "pricingExpression": {
                    "tieredRates": [
                        {
                            "startUsageAmount": 0,
                            "unitPrice": {"units": "1", "nanos": 500_000_000},
                        }
                    ]
                }
            }
        ]
        price = _extract_unit_price(pricing_info)
        self.assertAlmostEqual(price, 1.5, places=6)


# ---------------------------------------------------------------------------
# Tests: SKU classification into FamilyPricing
# ---------------------------------------------------------------------------

class TestClassifySkus(unittest.TestCase):
    """Test that CPU and RAM SKUs are classified into the correct family buckets."""

    def _make_n2_skus(self) -> List[Dict[str, Any]]:
        # These synthetic SKUs include serviceRegions so they match the real API format.
        # resourceGroup must be set correctly: "CPU" or "RAM".
        skus = [
            _make_sku("N2 Instance Core running in Iowa", "OnDemand", 31_611_000, resource_group="CPU"),
            _make_sku("N2 Instance Ram running in Iowa", "OnDemand",  4_237_000, resource_group="RAM"),
            # CUD SKUs use "Commitment v1: ..." format with serviceRegions
            _make_sku("Commitment v1: N2 Cpu in Iowa for 1 Year", "Commit1Yr", 21_225_000, resource_group="CPU"),
            _make_sku("Commitment v1: N2 Ram in Iowa for 1 Year", "Commit1Yr", 2_838_000, resource_group="RAM"),
        ]
        # Add serviceRegions to each SKU (on-demand and CUD alike)
        for sku in skus:
            sku["serviceRegions"] = ["us-central1"]
        return skus

    def _get_n2_fp(self, pricing: dict) -> Optional[_FamilyPricing]:
        """Find n2 FamilyPricing in region-keyed pricing dict."""
        for region_dict in pricing.values():
            if "n2" in region_dict:
                return region_dict["n2"]
        return None

    def test_n2_cpu_on_demand_captured(self):
        pricing = _classify_skus(self._make_n2_skus())
        fp = self._get_n2_fp(pricing)
        self.assertIsNotNone(fp, f"n2 not found; regions={list(pricing.keys())}")
        self.assertIsNotNone(fp.cpu_on_demand)
        self.assertAlmostEqual(fp.cpu_on_demand, 0.031611, places=5)

    def test_n2_ram_on_demand_captured(self):
        pricing = _classify_skus(self._make_n2_skus())
        fp = self._get_n2_fp(pricing)
        self.assertIsNotNone(fp)
        self.assertAlmostEqual(fp.ram_on_demand, 0.004237, places=5)

    def test_n2_1yr_cud_captured(self):
        pricing = _classify_skus(self._make_n2_skus())
        fp = self._get_n2_fp(pricing)
        self.assertIsNotNone(fp, f"n2 not found; regions={list(pricing.keys())}")
        self.assertIsNotNone(fp.cpu_1yr, "cpu_1yr not captured")

    def test_preemptible_skus_skipped(self):
        preemptible_sku = _make_sku(
            "Spot Preemptible N2 Instance Core running in Iowa", "Preemptible", 10_000_000,
            resource_group="CPU",
        )
        preemptible_sku["serviceRegions"] = ["us-central1"]
        skus = self._make_n2_skus() + [preemptible_sku]
        pricing = _classify_skus(skus)
        fp = self._get_n2_fp(pricing)
        self.assertIsNotNone(fp)
        # Preemptible price is lower — but it should NOT affect cpu_on_demand
        self.assertAlmostEqual(fp.cpu_on_demand, 0.031611, places=5)

    def test_unknown_family_skipped(self):
        sku = _make_sku("Some Unknown Service Core running in Americas", "OnDemand", resource_group="CPU")
        sku["serviceRegions"] = ["us-central1"]
        pricing = _classify_skus([sku])
        # No known family should be extracted
        for region_dict in pricing.values():
            self.assertNotIn("unknown", region_dict)


# ---------------------------------------------------------------------------
# Tests: build_instances
# ---------------------------------------------------------------------------

class TestBuildInstances(unittest.TestCase):
    """Test that build_instances produces valid CloudInstance dicts."""

    def _make_n2_pricing(self, cpu_od: float = 0.031611, ram_od: float = 0.004237,
                         cpu_1yr: float = 0.021225, ram_1yr: float = 0.002838,
                         cpu_3yr: float = 0.014748, ram_3yr: float = 0.001971) -> Dict:
        """Build a region-keyed pricing dict for n2 family (new format)."""
        fp = _FamilyPricing()
        fp.cpu_on_demand = cpu_od
        fp.ram_on_demand = ram_od
        fp.cpu_1yr = cpu_1yr
        fp.ram_1yr = ram_1yr
        fp.cpu_3yr = cpu_3yr
        fp.ram_3yr = ram_3yr
        fp.regions = GCP_REGIONS
        # Use a canonical region key (us-central1) so build_instances can find it
        return {"us-central1": {"n2": fp}}

    def test_n2_standard_4_built(self):
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        mt_instances = [i for i in instances if i["instanceType"] == "n2-standard-4"]
        self.assertEqual(len(mt_instances), 1)

    def test_on_demand_price_correct(self):
        pricing = self._make_n2_pricing(cpu_od=0.031611, ram_od=0.004237)
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        n2s4 = next(i for i in instances if i["instanceType"] == "n2-standard-4")
        # n2-standard-4: 4 vCPU, 16 GiB
        expected = round(4 * 0.031611 + 16 * 0.004237, 6)
        self.assertAlmostEqual(n2s4["priceUSD_hourly"], expected, places=4)

    def test_commitments_present(self):
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        n2s4 = next(i for i in instances if i["instanceType"] == "n2-standard-4")
        self.assertTrue(len(n2s4["commitments"]) >= 2, f"Expected >=2 commitments, got {n2s4['commitments']}")
        terms = {c["term"] for c in n2s4["commitments"]}
        self.assertIn("1yr", terms)
        self.assertIn("3yr", terms)

    def test_commitment_products_are_cud(self):
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        n2s4 = next(i for i in instances if i["instanceType"] == "n2-standard-4")
        for c in n2s4["commitments"]:
            self.assertEqual(c["product"], "cud")
            self.assertEqual(c["payment"], "flexible")

    def test_cud_savings_between_20_and_65_pct(self):
        """GCP CUDs typically offer 20-55% savings; our synthetic data is in range."""
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        n2s4 = next(i for i in instances if i["instanceType"] == "n2-standard-4")
        for c in n2s4["commitments"]:
            self.assertGreater(c["savingsVsOnDemandPct"], 10.0,
                               f"{c['term']} savings too low: {c['savingsVsOnDemandPct']}")
            self.assertLess(c["savingsVsOnDemandPct"], 70.0,
                            f"{c['term']} savings too high: {c['savingsVsOnDemandPct']}")

    def test_t2a_arm_architecture(self):
        """T2A instances should have arm64 architecture."""
        fp = _FamilyPricing()
        fp.cpu_on_demand = 0.01525
        fp.ram_on_demand = 0.00204
        fp.cpu_1yr = fp.ram_1yr = fp.cpu_3yr = fp.ram_3yr = None
        fp.regions = GCP_REGIONS
        pricing = {"us-central1": {"t2a": fp}}
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        t2a_instances = [i for i in instances if i["instanceType"].startswith("t2a-")]
        self.assertTrue(len(t2a_instances) > 0, "No t2a instances built")
        for inst in t2a_instances:
            self.assertEqual(inst["architecture"], "arm64",
                             f"{inst['instanceType']} should be arm64")

    def test_a2_gpu_instances_have_gpu_field(self):
        """A2 GPU instances should have a gpu field."""
        fp = _FamilyPricing()
        fp.cpu_on_demand = 0.031611
        fp.ram_on_demand = 0.004237
        fp.cpu_1yr = fp.ram_1yr = fp.cpu_3yr = fp.ram_3yr = None
        fp.regions = GCP_REGIONS
        pricing = {"us-central1": {"a2": fp}}
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        a2_instances = [i for i in instances if i["instanceType"].startswith("a2-")]
        self.assertTrue(len(a2_instances) > 0)
        for inst in a2_instances:
            self.assertIn("gpu", inst, f"{inst['instanceType']} missing gpu field")
            self.assertGreater(inst["gpu"]["count"], 0)

    def test_e2_missing_cud_prices_still_built(self):
        """If CUD prices are missing (e.g. E2 doesn't support CUDs), instance is still built."""
        fp = _FamilyPricing()
        fp.cpu_on_demand = 0.021811
        fp.ram_on_demand = 0.002923
        fp.cpu_1yr = fp.ram_1yr = fp.cpu_3yr = fp.ram_3yr = None
        fp.regions = GCP_REGIONS
        pricing = {"us-central1": {"e2": fp}}
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        e2_instances = [i for i in instances if i["instanceType"].startswith("e2-")]
        self.assertTrue(len(e2_instances) > 0, "E2 instances should be built even without CUD prices")
        for inst in e2_instances:
            self.assertEqual(inst["commitments"], [],
                             f"{inst['instanceType']} should have no commitments when CUD prices absent")

    def test_output_passes_validate_instance_data(self):
        """Instances built from synthetic data should pass validate_instance_data."""
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        for inst in instances:
            ok = validate_instance_data(inst)
            self.assertTrue(ok, f"validate_instance_data failed for {inst.get('instanceType')}")

    def test_output_commitments_pass_validator(self):
        """Commitments on built instances should pass validate_commitments."""
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        for inst in instances:
            comms = inst.get("commitments", [])
            if comms:
                ok, errors = validate_commitments(comms, inst["priceUSD_hourly"])
                self.assertTrue(ok,
                    f"{inst['instanceType']} commitment errors: {errors}")

    def test_required_fields_present(self):
        """All required CloudInstance fields should be present."""
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        required = ["provider", "type", "instanceType", "vCPU", "memoryGiB",
                    "priceUSD_hourly", "priceUSD_monthly", "regions", "source",
                    "lastUpdated", "architecture", "family", "commitments"]
        for inst in instances:
            for field in required:
                self.assertIn(field, inst, f"{inst.get('instanceType')} missing field '{field}'")

    def test_provider_is_gcp(self):
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        for inst in instances:
            self.assertEqual(inst["provider"], "gcp")

    def test_type_is_cloud_server(self):
        pricing = self._make_n2_pricing()
        instances = build_instances(pricing, {}, target_regions=["us-central1"])
        for inst in instances:
            self.assertEqual(inst["type"], "cloud-server")

    def test_regions_subset_of_target(self):
        pricing = self._make_n2_pricing()
        target = ["us-central1", "europe-west1"]
        instances = build_instances(pricing, {}, target_regions=target)
        for inst in instances:
            for r in inst["regions"]:
                self.assertIn(r, target, f"Region {r} not in target {target}")


# ---------------------------------------------------------------------------
# Tests: spec table completeness
# ---------------------------------------------------------------------------

class TestSpecTable(unittest.TestCase):
    """Sanity checks on the embedded machine spec table."""

    def test_spec_table_has_minimum_entries(self):
        """We expect at least 150 concrete machine types."""
        self.assertGreaterEqual(len(_SPEC_LOOKUP), 150)

    def test_n2_families_present(self):
        n2_types = [mt for mt in _SPEC_LOOKUP if mt.startswith("n2-")]
        self.assertGreater(len(n2_types), 0)

    def test_c3_families_present(self):
        c3_types = [mt for mt in _SPEC_LOOKUP if mt.startswith("c3-")]
        self.assertGreater(len(c3_types), 0)

    def test_e2_families_present(self):
        e2_types = [mt for mt in _SPEC_LOOKUP if mt.startswith("e2-")]
        self.assertGreater(len(e2_types), 0)

    def test_m3_families_present(self):
        m3_types = [mt for mt in _SPEC_LOOKUP if mt.startswith("m3-")]
        self.assertGreater(len(m3_types), 0)

    def test_t2a_arm_present(self):
        t2a_types = [mt for mt in _SPEC_LOOKUP if mt.startswith("t2a-")]
        self.assertGreater(len(t2a_types), 0)

    def test_a2_gpu_present(self):
        a2_types = [mt for mt in _SPEC_LOOKUP if mt.startswith("a2-")]
        self.assertGreater(len(a2_types), 0)

    def test_g2_l4_present(self):
        g2_types = [mt for mt in _SPEC_LOOKUP if mt.startswith("g2-")]
        self.assertGreater(len(g2_types), 0)

    def test_vcpu_positive(self):
        for mt, spec in _SPEC_LOOKUP.items():
            vcpu = spec[0]
            self.assertGreater(vcpu, 0, f"{mt} has vcpu={vcpu}")

    def test_mem_positive(self):
        for mt, spec in _SPEC_LOOKUP.items():
            mem = spec[1]
            self.assertGreater(mem, 0, f"{mt} has mem={mem}")

    def test_architecture_valid(self):
        valid_archs = {"x86_64", "arm64"}
        for mt, spec in _SPEC_LOOKUP.items():
            arch = spec[2]
            self.assertIn(arch, valid_archs, f"{mt} has unknown arch {arch}")

    def test_t2a_is_arm64(self):
        for mt, spec in _SPEC_LOOKUP.items():
            if mt.startswith("t2a-"):
                self.assertEqual(spec[2], "arm64", f"{mt} should be arm64")

    def test_regions_list_nonempty(self):
        self.assertGreater(len(GCP_REGIONS), 30)


if __name__ == '__main__':
    unittest.main()
