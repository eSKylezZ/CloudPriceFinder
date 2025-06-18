#!/usr/bin/env python3
"""
CloudCosts Data Orchestrator
Coordinates all cloud provider data fetching and processing.
"""

import os
import sys
import json
import time
import logging
import asyncio
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Import individual fetchers
try:
    # Try to import official libraries version first
    from fetch_hetzner_v3 import fetch_hetzner_cloud
    HETZNER_VERSION = "v3.0 (Official Libraries)"
except ImportError:
    try:
        # Fallback to enhanced Hetzner v2 script
        from fetch_hetzner_v2 import fetch_hetzner_cloud
        HETZNER_VERSION = "v2.0 (Manual API)"
    except ImportError:
        # Final fallback to original script
        from fetch_hetzner import fetch_hetzner_cloud
        HETZNER_VERSION = "v1.0 (Legacy)"
from utils.currency_converter import convert_currency
from utils.data_validator import validate_instance_data
from utils.data_normalizer import normalize_instance_data

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "all_instances.json"
SUMMARY_FILE = DATA_DIR / "summary.json"
MAX_WORKERS = 4
TIMEOUT_SECONDS = 300  # 5 minutes per provider

class CloudDataOrchestrator:
    """Orchestrates data collection from all cloud providers."""
    
    def __init__(self):
        self.providers = {
            'hetzner': self._fetch_hetzner,
            'aws': self._fetch_aws,
            'azure': self._fetch_azure,
            'gcp': self._fetch_gcp,
            'oci': self._fetch_oci,
            'ovh': self._fetch_ovh,
        }
        self.results = {}
        self.errors = {}
        
    def _fetch_hetzner(self) -> List[Dict[str, Any]]:
        """Fetch Hetzner data using available implementation."""
        try:
            logger.info(f"Fetching Hetzner data using {HETZNER_VERSION}...")
            data = fetch_hetzner_cloud()
            return self._normalize_hetzner_data(data)
        except Exception as e:
            logger.error(f"Hetzner fetch failed: {e}")
            raise
    
    def _fetch_aws(self) -> List[Dict[str, Any]]:
        """Fetch AWS data - placeholder for now."""
        logger.warning("AWS fetcher not implemented yet - returning empty data")
        return []
    
    def _fetch_azure(self) -> List[Dict[str, Any]]:
        """Fetch Azure data - placeholder for now."""
        logger.warning("Azure fetcher not implemented yet - returning empty data")
        return []
    
    def _fetch_gcp(self) -> List[Dict[str, Any]]:
        """Fetch GCP data - placeholder for now."""
        logger.warning("GCP fetcher not implemented yet - returning empty data")
        return []
    
    def _fetch_oci(self) -> List[Dict[str, Any]]:
        """Fetch OCI data - placeholder for now."""
        logger.warning("OCI fetcher not implemented yet - returning empty data")
        return []
    
    def _fetch_ovh(self) -> List[Dict[str, Any]]:
        """Fetch OVH data - placeholder for now."""
        logger.warning("OVH fetcher not implemented yet - returning empty data")
        return []
    
    def _normalize_hetzner_data(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize Hetzner data to standard format."""
        normalized = []
        for item in data:
            try:
                # Check if item is already in new format (v2.0)
                if 'platform' in item and 'hetzner_metadata' in item:
                    # New format - just add provider and convert currency
                    eur_hourly = item.get('priceEUR_hourly_net', 0)
                    eur_monthly = item.get('priceEUR_monthly_net', 0)
                    
                    # Simple conversion rate - in production, use real-time rates
                    usd_hourly = eur_hourly * 1.1 if eur_hourly else 0
                    usd_monthly = eur_monthly * 1.1 if eur_monthly else 0
                    
                    normalized_item = {
                        **item,  # Keep all existing fields
                        'provider': 'hetzner',
                        'priceUSD_hourly': round(usd_hourly, 6),
                        'priceUSD_monthly': round(usd_monthly, 2),
                        'originalPrice': {
                            'hourly': eur_hourly,
                            'monthly': eur_monthly,
                            'currency': 'EUR'
                        },
                        'regions': item.get('regions', item.get('locations', [])),
                    }
                else:
                    # Legacy format (v1.0) - full normalization
                    eur_hourly = item.get('priceEUR_hourly_net', 0)
                    eur_monthly = item.get('priceEUR_monthly_net', 0)
                    
                    usd_hourly = eur_hourly * 1.1 if eur_hourly else 0
                    usd_monthly = eur_monthly * 1.1 if eur_monthly else 0
                    
                    normalized_item = {
                        'provider': 'hetzner',
                        'platform': 'cloud',  # Legacy is cloud only
                        'type': item.get('type', 'cloud-server'),
                        'instanceType': item.get('instanceType', ''),
                        'vCPU': item.get('vCPU', 0),
                        'memoryGiB': item.get('memoryGiB', 0),
                        'diskType': item.get('diskType'),
                        'diskSizeGB': item.get('diskSizeGB'),
                        'priceUSD_hourly': round(usd_hourly, 6),
                        'priceUSD_monthly': round(usd_monthly, 2),
                        'originalPrice': {
                            'hourly': eur_hourly,
                            'monthly': eur_monthly,
                            'currency': 'EUR'
                        },
                        'regions': item.get('locations', []),
                        'deprecated': item.get('deprecated', False),
                        'source': item.get('source', 'hetzner_api'),
                        'description': item.get('description', ''),
                        'lastUpdated': datetime.now().isoformat(),
                        'raw': item
                    }
                
                if validate_instance_data(normalized_item):
                    normalized.append(normalized_item)
                else:
                    logger.warning(f"Invalid data for Hetzner instance: {item.get('instanceType')}")
                    
            except Exception as e:
                logger.error(f"Error normalizing Hetzner item: {e}")
                continue
        
        return normalized
    
    def _fetch_provider_data(self, provider: str) -> tuple[str, List[Dict[str, Any]], Optional[str]]:
        """Fetch data for a single provider with error handling."""
        try:
            logger.info(f"Starting {provider} data fetch...")
            start_time = time.time()
            
            data = self.providers[provider]()
            
            elapsed = time.time() - start_time
            logger.info(f"✅ {provider}: {len(data)} instances in {elapsed:.1f}s")
            
            return provider, data, None
            
        except Exception as e:
            error_msg = f"❌ {provider}: {str(e)}"
            logger.error(error_msg)
            return provider, [], str(e)
    
    async def fetch_all_providers(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch data from all providers concurrently."""
        logger.info("Starting concurrent data fetch from all providers...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all tasks
            future_to_provider = {
                executor.submit(self._fetch_provider_data, provider): provider
                for provider in self.providers.keys()
            }
            
            # Collect results with timeout
            for future in concurrent.futures.as_completed(future_to_provider, timeout=TIMEOUT_SECONDS):
                provider, data, error = future.result()
                
                if error:
                    self.errors[provider] = error
                    self.results[provider] = []
                else:
                    self.results[provider] = data
        
        return self.results
    
    def _generate_summary(self, all_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary statistics."""
        if not all_data:
            return {
                'totalInstances': 0,
                'providersCount': 0,
                'lastUpdated': datetime.now().isoformat(),
                'priceRange': {'min': 0, 'max': 0},
                'byProvider': {},
                'byType': {}
            }
        
        # Calculate statistics
        prices = [item['priceUSD_hourly'] for item in all_data if item['priceUSD_hourly'] > 0]
        
        # Group by provider
        by_provider = {}
        for item in all_data:
            provider = item['provider']
            if provider not in by_provider:
                by_provider[provider] = 0
            by_provider[provider] += 1
        
        # Group by type
        by_type = {}
        for item in all_data:
            item_type = item['type']
            if item_type not in by_type:
                by_type[item_type] = 0
            by_type[item_type] += 1
        
        return {
            'totalInstances': len(all_data),
            'providersCount': len(by_provider),
            'lastUpdated': datetime.now().isoformat(),
            'priceRange': {
                'min': min(prices) if prices else 0,
                'max': max(prices) if prices else 0
            },
            'byProvider': by_provider,
            'byType': by_type,
            'errors': self.errors
        }
    
    async def run(self) -> bool:
        """Run the complete data orchestration process."""
        print("=== CloudCosts Data Orchestrator ===")
        print(f"Timestamp: {datetime.now().isoformat()}")
        
        # Ensure data directory exists
        DATA_DIR.mkdir(exist_ok=True)
        
        try:
            # Fetch all provider data
            print(f"\n📡 Fetching data from {len(self.providers)} providers...")
            await self.fetch_all_providers()
            
            # Combine all data
            all_data = []
            for provider, data in self.results.items():
                all_data.extend(data)
            
            print(f"\n📊 Processing {len(all_data)} total instances...")
            
            # Generate summary
            summary = self._generate_summary(all_data)
            
            # Save combined data
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_data, f, indent=2, ensure_ascii=False)
            
            # Save summary
            with open(SUMMARY_FILE, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            
            print(f"\n✅ SUCCESS: Saved {len(all_data)} instances to {OUTPUT_FILE}")
            print(f"✅ Summary saved to {SUMMARY_FILE}")
            
            # Print summary
            print(f"\n📈 Data Summary:")
            for provider, count in summary['byProvider'].items():
                print(f"  {provider}: {count} instances")
            
            if summary['errors']:
                print(f"\n⚠️ Errors encountered:")
                for provider, error in summary['errors'].items():
                    print(f"  {provider}: {error}")
            
            return len(all_data) > 0
            
        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            print(f"\n❌ FATAL ERROR: {e}")
            return False

async def main():
    """Main function."""
    orchestrator = CloudDataOrchestrator()
    success = await orchestrator.run()
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)