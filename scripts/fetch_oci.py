#!/usr/bin/env python3
"""
Oracle Cloud Infrastructure (OCI) Data Fetcher
Fetches OCI compute instance pricing and specifications using public APIs and web scraping.
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime
from typing import Dict, List, Any, Optional

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OCIDataCollector:
    """Collector for Oracle Cloud Infrastructure compute instances using public APIs."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # Known OCI regions and their details
        self.regions = {
            'us-ashburn-1': {'name': 'US East (Ashburn)', 'country': 'United States', 'code': 'US'},
            'us-phoenix-1': {'name': 'US West (Phoenix)', 'country': 'United States', 'code': 'US'},
            'ca-toronto-1': {'name': 'Canada Southeast (Toronto)', 'country': 'Canada', 'code': 'CA'},
            'ca-montreal-1': {'name': 'Canada Southeast (Montreal)', 'country': 'Canada', 'code': 'CA'},
            'eu-frankfurt-1': {'name': 'Germany Central (Frankfurt)', 'country': 'Germany', 'code': 'DE'},
            'eu-zurich-1': {'name': 'Switzerland North (Zurich)', 'country': 'Switzerland', 'code': 'CH'},
            'eu-amsterdam-1': {'name': 'Netherlands Northwest (Amsterdam)', 'country': 'Netherlands', 'code': 'NL'},
            'eu-london-1': {'name': 'UK South (London)', 'country': 'United Kingdom', 'code': 'GB'},
            'ap-mumbai-1': {'name': 'India West (Mumbai)', 'country': 'India', 'code': 'IN'},
            'ap-seoul-1': {'name': 'South Korea Central (Seoul)', 'country': 'South Korea', 'code': 'KR'},
            'ap-tokyo-1': {'name': 'Japan East (Tokyo)', 'country': 'Japan', 'code': 'JP'},
            'ap-osaka-1': {'name': 'Japan Central (Osaka)', 'country': 'Japan', 'code': 'JP'},
            'ap-sydney-1': {'name': 'Australia East (Sydney)', 'country': 'Australia', 'code': 'AU'},
            'ap-melbourne-1': {'name': 'Australia Southeast (Melbourne)', 'country': 'Australia', 'code': 'AU'},
            'sa-saopaulo-1': {'name': 'Brazil East (Sao Paulo)', 'country': 'Brazil', 'code': 'BR'},
            'uk-london-1': {'name': 'UK South (London)', 'country': 'United Kingdom', 'code': 'GB'},
            'me-jeddah-1': {'name': 'Saudi Arabia West (Jeddah)', 'country': 'Saudi Arabia', 'code': 'SA'},
            'ap-singapore-1': {'name': 'Singapore', 'country': 'Singapore', 'code': 'SG'},
            'eu-milan-1': {'name': 'Italy Northwest (Milan)', 'country': 'Italy', 'code': 'IT'}
        }
    
    def collect_all_oci_data(self) -> List[Dict[str, Any]]:
        """Collect all OCI compute instance data."""
        logger.info("🌩️  Collecting Oracle Cloud Infrastructure compute instances...")
        
        all_instances = []
        
        try:
            # Method 1: Try official pricing API
            pricing_instances = self._fetch_from_pricing_api()
            if pricing_instances:
                all_instances.extend(pricing_instances)
                logger.info(f"Fetched {len(pricing_instances)} instances from pricing API")
            
            # Method 2: Parse compute shapes from documentation if needed
            if not all_instances:
                shapes_instances = self._fetch_compute_shapes()
                all_instances.extend(shapes_instances)
                logger.info(f"Fetched {len(shapes_instances)} instances from shapes data")
            
            # Method 3: Fallback to known instance data
            if not all_instances:
                logger.warning("Using fallback OCI instance data")
                all_instances.extend(self._get_fallback_instances())
            
            logger.info(f"✅ Total OCI instances collected: {len(all_instances)}")
            return all_instances
            
        except Exception as e:
            logger.error(f"Error collecting OCI data: {e}")
            # Return fallback data
            return self._get_fallback_instances()
    
    def _fetch_from_pricing_api(self) -> List[Dict[str, Any]]:
        """Fetch pricing data from Oracle's official pricing API."""
        instances = []
        
        try:
            # Oracle's official pricing API endpoint
            pricing_url = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"
            
            logger.info("Fetching from Oracle pricing API...")
            response = self.session.get(pricing_url, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Pricing API response structure: {type(data)}")
                
                # Parse pricing data for compute instances
                if isinstance(data, dict) and 'items' in data:
                    for item in data['items']:
                        if self._is_compute_instance(item):
                            instance = self._parse_pricing_item(item)
                            if instance:
                                instances.append(instance)
                elif isinstance(data, list):
                    for item in data:
                        if self._is_compute_instance(item):
                            instance = self._parse_pricing_item(item)
                            if instance:
                                instances.append(instance)
                
                logger.info(f"Parsed {len(instances)} compute instances from pricing API")
            else:
                logger.warning(f"Pricing API returned status {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error fetching from pricing API: {e}")
        
        return instances
    
    def _fetch_compute_shapes(self) -> List[Dict[str, Any]]:
        """Fetch compute shapes and create instances with estimated pricing."""
        instances = []
        
        try:
            # Create instances based on known OCI compute shapes
            shapes = self._get_known_compute_shapes()
            
            for shape in shapes:
                instance = self._create_instance_from_shape(shape)
                instances.append(instance)
            
            logger.info(f"Created {len(instances)} instances from compute shapes")
            
        except Exception as e:
            logger.error(f"Error creating instances from shapes: {e}")
        
        return instances
    
    def _is_compute_instance(self, item: Dict[str, Any]) -> bool:
        """Check if pricing item is a compute instance."""
        name = item.get('name', '').lower()
        service = item.get('service', '').lower()
        sku = item.get('sku', '').lower()
        
        compute_keywords = ['compute', 'instance', 'vm', 'virtual machine']
        return any(keyword in name or keyword in service or keyword in sku for keyword in compute_keywords)
    
    def _parse_pricing_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a pricing API item into our standard format."""
        try:
            name = item.get('name', '')
            sku = item.get('sku', '')
            price_usd = float(item.get('price', 0))
            
            # Extract compute specifications from name/description
            specs = self._extract_specs_from_name(name)
            
            return {
                'provider': 'oci',
                'type': 'cloud-server',
                'instanceType': sku or name,
                'vCPU': specs['vcpu'],
                'memoryGiB': specs['memory'],
                'priceUSD_hourly': price_usd,
                'priceUSD_monthly': price_usd * 730.44,
                'regions': list(self.regions.keys()),
                'locationDetails': [
                    {
                        'code': region_code,
                        'city': region_info['name'].split('(')[1].rstrip(')') if '(' in region_info['name'] else region_info['name'],
                        'country': region_info['country'],
                        'countryCode': region_info['code'],
                        'region': region_info['name']
                    }
                    for region_code, region_info in self.regions.items()
                ],
                'source': 'oci_pricing_api',
                'description': f"OCI {name}",
                'lastUpdated': datetime.now().isoformat(),
                'architecture': 'x86' if 'amd' in name.lower() or 'intel' in name.lower() else 'ARM' if 'ampere' in name.lower() else 'x86',
                'oci_metadata': {
                    'sku': sku,
                    'original_name': name
                }
            }
            
        except Exception as e:
            logger.error(f"Error parsing pricing item: {e}")
            return None
    
    def _extract_specs_from_name(self, name: str) -> Dict[str, int]:
        """Extract vCPU and memory specs from instance name."""
        import re
        
        # Default values
        vcpu = 1
        memory = 1
        
        # Look for patterns like "2 OCPU", "4 vCPU", "8 GB"
        vcpu_patterns = [
            r'(\d+)\s*OCPU',
            r'(\d+)\s*vCPU',
            r'(\d+)\s*CPU',
            r'(\d+)\s*Core'
        ]
        
        memory_patterns = [
            r'(\d+)\s*GB',
            r'(\d+)\s*GiB',
            r'(\d+)GB'
        ]
        
        name_upper = name.upper()
        
        # Extract vCPU/OCPU
        for pattern in vcpu_patterns:
            match = re.search(pattern, name_upper)
            if match:
                vcpu = int(match.group(1))
                # Convert OCPU to vCPU (1 OCPU = 2 vCPU for x86)
                if 'OCPU' in pattern:
                    vcpu = vcpu * 2
                break
        
        # Extract memory
        for pattern in memory_patterns:
            match = re.search(pattern, name_upper)
            if match:
                memory = int(match.group(1))
                break
        
        return {'vcpu': vcpu, 'memory': memory}
    
    def _get_known_compute_shapes(self) -> List[Dict[str, Any]]:
        """Get list of known OCI compute shapes with specifications."""
        return [
            # Standard E-series (AMD EPYC)
            {'name': 'VM.Standard.E3.Flex', 'ocpu': 1, 'memory': 16, 'price_per_ocpu': 0.0255, 'price_per_gb': 0.00255, 'arch': 'x86'},
            {'name': 'VM.Standard.E4.Flex', 'ocpu': 1, 'memory': 16, 'price_per_ocpu': 0.0306, 'price_per_gb': 0.00306, 'arch': 'x86'},
            {'name': 'VM.Standard.E5.Flex', 'ocpu': 1, 'memory': 16, 'price_per_ocpu': 0.0306, 'price_per_gb': 0.00306, 'arch': 'x86'},
            
            # Fixed shapes
            {'name': 'VM.Standard.E2.1.Micro', 'ocpu': 1, 'memory': 1, 'price_hourly': 0.0085, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.1', 'ocpu': 1, 'memory': 8, 'price_hourly': 0.0408, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.2', 'ocpu': 2, 'memory': 16, 'price_hourly': 0.0816, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.4', 'ocpu': 4, 'memory': 32, 'price_hourly': 0.1632, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.8', 'ocpu': 8, 'memory': 64, 'price_hourly': 0.3264, 'arch': 'x86'},
            
            # AMD E3 series
            {'name': 'VM.Standard.E3.1', 'ocpu': 1, 'memory': 8, 'price_hourly': 0.0510, 'arch': 'x86'},
            {'name': 'VM.Standard.E3.2', 'ocpu': 2, 'memory': 16, 'price_hourly': 0.1020, 'arch': 'x86'},
            {'name': 'VM.Standard.E3.4', 'ocpu': 4, 'memory': 32, 'price_hourly': 0.2040, 'arch': 'x86'},
            {'name': 'VM.Standard.E3.8', 'ocpu': 8, 'memory': 64, 'price_hourly': 0.4080, 'arch': 'x86'},
            
            # Intel E4 series
            {'name': 'VM.Standard.E4.1', 'ocpu': 1, 'memory': 8, 'price_hourly': 0.0612, 'arch': 'x86'},
            {'name': 'VM.Standard.E4.2', 'ocpu': 2, 'memory': 16, 'price_hourly': 0.1224, 'arch': 'x86'},
            {'name': 'VM.Standard.E4.4', 'ocpu': 4, 'memory': 32, 'price_hourly': 0.2448, 'arch': 'x86'},
            {'name': 'VM.Standard.E4.8', 'ocpu': 8, 'memory': 64, 'price_hourly': 0.4896, 'arch': 'x86'},
            
            # Ampere A1 (ARM) - Free tier eligible
            {'name': 'VM.Standard.A1.Flex', 'ocpu': 1, 'memory': 6, 'price_per_ocpu': 0.01, 'price_per_gb': 0.0015, 'arch': 'ARM', 'free_tier': True},
            
            # High Memory shapes
            {'name': 'VM.Standard.E2.1.Micro', 'ocpu': 1, 'memory': 1, 'price_hourly': 0.0085, 'arch': 'x86', 'free_tier': True},
            
            # Dense I/O shapes
            {'name': 'VM.DenseIO.E4.1', 'ocpu': 1, 'memory': 8, 'price_hourly': 0.0884, 'arch': 'x86', 'storage': '6.4 TB NVMe SSD'},
            {'name': 'VM.DenseIO.E4.2', 'ocpu': 2, 'memory': 16, 'price_hourly': 0.1768, 'arch': 'x86', 'storage': '12.8 TB NVMe SSD'},
            {'name': 'VM.DenseIO.E4.4', 'ocpu': 4, 'memory': 32, 'price_hourly': 0.3536, 'arch': 'x86', 'storage': '25.6 TB NVMe SSD'},
            {'name': 'VM.DenseIO.E4.8', 'ocpu': 8, 'memory': 64, 'price_hourly': 0.7072, 'arch': 'x86', 'storage': '51.2 TB NVMe SSD'},
            
            # Optimized shapes
            {'name': 'VM.Optimized3.Flex', 'ocpu': 1, 'memory': 16, 'price_per_ocpu': 0.0459, 'price_per_gb': 0.00459, 'arch': 'x86'},
            
            # GPU shapes (basic examples)
            {'name': 'VM.GPU3.1', 'ocpu': 6, 'memory': 90, 'price_hourly': 1.275, 'arch': 'x86', 'gpu': 'NVIDIA V100'},
            {'name': 'VM.GPU3.2', 'ocpu': 12, 'memory': 180, 'price_hourly': 2.55, 'arch': 'x86', 'gpu': 'NVIDIA V100'},
            {'name': 'VM.GPU3.4', 'ocpu': 24, 'memory': 360, 'price_hourly': 5.10, 'arch': 'x86', 'gpu': 'NVIDIA V100'},
        ]
    
    def _create_instance_from_shape(self, shape: Dict[str, Any]) -> Dict[str, Any]:
        """Create an instance object from a compute shape definition."""
        try:
            name = shape['name']
            ocpu = shape['ocpu']
            memory = shape['memory']
            arch = shape.get('arch', 'x86')
            
            # Calculate pricing
            if 'price_hourly' in shape:
                price_hourly = shape['price_hourly']
            elif 'price_per_ocpu' in shape and 'price_per_gb' in shape:
                price_hourly = (ocpu * shape['price_per_ocpu']) + (memory * shape['price_per_gb'])
            else:
                price_hourly = 0.05  # Default estimate
            
            # Convert OCPU to vCPU
            if arch == 'ARM':
                vcpu = ocpu  # Ampere A1 uses different calculation
            else:
                vcpu = ocpu * 2  # x86: 1 OCPU = 2 vCPU
            
            # Build instance data
            instance = {
                'provider': 'oci',
                'type': 'cloud-server',
                'instanceType': name,
                'vCPU': vcpu,
                'memoryGiB': memory,
                'architecture': arch,
                'priceUSD_hourly': price_hourly,
                'priceUSD_monthly': price_hourly * 730.44,
                'regions': list(self.regions.keys()),
                'locationDetails': [
                    {
                        'code': region_code,
                        'city': region_info['name'].split('(')[1].rstrip(')') if '(' in region_info['name'] else region_info['name'],
                        'country': region_info['country'],
                        'countryCode': region_info['code'],
                        'region': region_info['name']
                    }
                    for region_code, region_info in self.regions.items()
                ],
                'source': 'oci_shapes',
                'description': f"Oracle Cloud {name}",
                'lastUpdated': datetime.now().isoformat(),
                'oci_metadata': {
                    'ocpu_count': ocpu,
                    'shape_type': 'flexible' if 'Flex' in name else 'fixed',
                    'free_tier_eligible': shape.get('free_tier', False)
                }
            }
            
            # Add storage info if available
            if 'storage' in shape:
                instance['diskType'] = 'NVMe SSD'
                instance['storage_description'] = shape['storage']
                instance['oci_metadata']['storage'] = shape['storage']
            
            # Add GPU info if available
            if 'gpu' in shape:
                instance['gpu_description'] = shape['gpu']
                instance['oci_metadata']['gpu'] = shape['gpu']
            
            # Mark free tier instances
            if shape.get('free_tier'):
                instance['description'] += ' (Free Tier Eligible)'
                instance['oci_metadata']['pricing_note'] = 'Always Free or Free Trial eligible'
            
            return instance
            
        except Exception as e:
            logger.error(f"Error creating instance from shape {shape.get('name', 'unknown')}: {e}")
            return None
    
    def _get_fallback_instances(self) -> List[Dict[str, Any]]:
        """Get fallback instance data if API fetching fails."""
        logger.info("Using fallback OCI instance data...")
        
        fallback_shapes = [
            {'name': 'VM.Standard.E2.1.Micro', 'ocpu': 1, 'memory': 1, 'price_hourly': 0.0085, 'arch': 'x86', 'free_tier': True},
            {'name': 'VM.Standard.A1.Flex', 'ocpu': 1, 'memory': 6, 'price_hourly': 0.01, 'arch': 'ARM', 'free_tier': True},
            {'name': 'VM.Standard.E3.Flex', 'ocpu': 1, 'memory': 8, 'price_hourly': 0.051, 'arch': 'x86'},
            {'name': 'VM.Standard.E4.Flex', 'ocpu': 1, 'memory': 8, 'price_hourly': 0.0612, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.1', 'ocpu': 1, 'memory': 8, 'price_hourly': 0.0408, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.2', 'ocpu': 2, 'memory': 16, 'price_hourly': 0.0816, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.4', 'ocpu': 4, 'memory': 32, 'price_hourly': 0.1632, 'arch': 'x86'},
            {'name': 'VM.Standard.E2.8', 'ocpu': 8, 'memory': 64, 'price_hourly': 0.3264, 'arch': 'x86'},
        ]
        
        instances = []
        for shape in fallback_shapes:
            instance = self._create_instance_from_shape(shape)
            if instance:
                instances.append(instance)
        
        return instances

def fetch_oci_data():
    """Main function for compatibility with existing orchestrator."""
    collector = OCIDataCollector()
    return collector.collect_all_oci_data()

def main():
    """Main function for direct execution."""
    print("=== Oracle Cloud Infrastructure Data Fetcher ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    try:
        data = fetch_oci_data()
        
        if data:
            # Save to file
            output_file = "data/oci.json"
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            print(f"\n✅ SUCCESS: Saved {len(data)} OCI instances to {output_file}")
            
            # Summary
            arch_counts = {}
            for item in data:
                arch = item.get('architecture', 'unknown')
                arch_counts[arch] = arch_counts.get(arch, 0) + 1
            
            print(f"\n📊 Summary by Architecture:")
            for arch, count in arch_counts.items():
                print(f"  {arch}: {count} instances")
            
            # Show some examples
            free_tier = [i for i in data if i.get('oci_metadata', {}).get('free_tier_eligible')]
            if free_tier:
                print(f"\n💰 Free Tier Instances: {len(free_tier)}")
                for instance in free_tier[:3]:
                    print(f"  - {instance['instanceType']}: {instance['vCPU']} vCPU, {instance['memoryGiB']} GB RAM")
            
        else:
            print("\n❌ No data was collected")
            return False
        
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        logger.exception("Fatal error in main execution")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)