#!/usr/bin/env python3
"""
Hetzner Data Fetcher - Official Libraries Edition
Uses official hcloud library for Cloud API and hetzner library for Robot API.
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

# Official Hetzner libraries
try:
    from hcloud import Client as HCloudClient
    from hcloud.server_types import ServerType
    from hcloud.locations import Location
    HCLOUD_AVAILABLE = True
except ImportError:
    HCLOUD_AVAILABLE = False
    logging.warning("hcloud library not available - install with: pip install hcloud")

try:
    from hetzner.robot import Robot
    HETZNER_ROBOT_AVAILABLE = True
except ImportError:
    HETZNER_ROBOT_AVAILABLE = False
    logging.warning("hetzner library not available - install with: pip install hetzner")

# Configuration
class HetznerConfig:
    def __init__(self):
        # Cloud API Configuration
        self.cloud_api_token = os.environ.get("HETZNER_API_TOKEN", "")
        
        # Robot API Configuration
        self.robot_user = os.environ.get("HETZNER_ROBOT_USER", "")
        self.robot_password = os.environ.get("HETZNER_ROBOT_PASSWORD", "")
        
        # Feature flags
        self.enable_cloud = os.environ.get("HETZNER_ENABLE_CLOUD", "true").lower() == "true"
        self.enable_dedicated = os.environ.get("HETZNER_ENABLE_DEDICATED", "false").lower() == "true"

config = HetznerConfig()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HetznerCloudCollector:
    """Collector for Hetzner Cloud services using official hcloud library."""
    
    def __init__(self):
        if not HCLOUD_AVAILABLE:
            raise ImportError("hcloud library not available")
        
        if not config.cloud_api_token:
            raise ValueError("HETZNER_API_TOKEN not provided")
            
        self.client = HCloudClient(token=config.cloud_api_token)
    
    def collect_all_cloud_services(self) -> List[Dict[str, Any]]:
        """Collect all cloud services data using official library."""
        logger.info("🌩️  Collecting Hetzner Cloud services using official library...")
        
        all_services = []
        
        try:
            # Get server types with pricing
            server_types = self._collect_server_types()
            all_services.extend(server_types)
            
            # Get load balancer types with pricing
            lb_types = self._collect_load_balancer_types()
            all_services.extend(lb_types)
            
            # Get other pricing services
            other_services = self._collect_other_services()
            all_services.extend(other_services)
            
            logger.info(f"✅ Cloud services: {len(all_services)} items")
            
        except Exception as e:
            logger.error(f"Error collecting cloud services: {e}")
        
        return all_services
    
    def _collect_server_types(self) -> List[Dict[str, Any]]:
        """Collect server types with pricing using hybrid approach."""
        logger.info("Fetching server types...")
        
        try:
            # Get server types from hcloud library
            server_types = self.client.server_types.get_all()
            locations = self.client.locations.get_all()
            
            # Get pricing data via direct API call (since hcloud doesn't include pricing by default)
            import requests
            headers = {
                'Authorization': f'Bearer {config.cloud_api_token}',
                'Content-Type': 'application/json'
            }
            
            logger.info("Fetching pricing data via direct API...")
            pricing_response = requests.get("https://api.hetzner.cloud/v1/pricing", headers=headers)
            if pricing_response.status_code != 200:
                logger.error(f"Failed to fetch pricing data: {pricing_response.status_code}")
                return []
            
            pricing_data = pricing_response.json()
            pricing_by_type = {}
            
            if 'pricing' in pricing_data:
                for pricing_entry in pricing_data['pricing'].get('server_types', []):
                    pricing_by_type[pricing_entry.get('name')] = pricing_entry
            
            # Create location mapping
            location_map = self._get_location_mapping(locations)
            
            processed_servers = []
            
            for server_type in server_types:
                try:
                    # Get pricing for this server type
                    pricing_info = pricing_by_type.get(server_type.name, {})
                    
                    if 'prices' not in pricing_info:
                        logger.warning(f"No pricing found for server type: {server_type.name}")
                        continue
                    
                    # Process regional pricing
                    regional_pricing = []
                    locations_list = []
                    
                    for price_entry in pricing_info['prices']:
                        location_code = price_entry.get('location')
                        if location_code:
                            locations_list.append(location_code)
                            
                            hourly_net = float(price_entry.get('price_hourly', {}).get('net', 0))
                            monthly_net = float(price_entry.get('price_monthly', {}).get('net', 0))
                            included_traffic = price_entry.get('included_traffic', 0)
                            traffic_price = float(price_entry.get('price_per_tb_traffic', {}).get('net', 0))
                            
                            regional_pricing.append({
                                'location': location_code,
                                'hourly_net': hourly_net,
                                'monthly_net': monthly_net,
                                'included_traffic': included_traffic,
                                'traffic_price_per_tb': traffic_price
                            })
                    
                    # Calculate price ranges
                    if regional_pricing:
                        hourly_prices = [p['hourly_net'] for p in regional_pricing]
                        monthly_prices = [p['monthly_net'] for p in regional_pricing]
                        
                        min_hourly = min(hourly_prices)
                        max_hourly = max(hourly_prices)
                        min_monthly = min(monthly_prices)
                        max_monthly = max(monthly_prices)
                        
                        # Use minimum pricing for default display
                        hourly_price = min_hourly
                        monthly_price = min_monthly
                    else:
                        continue
                    
                    # Get IPv4 Primary IP cost (approximate)
                    ipv4_primary_ip_cost = 0.50  # Standard cost from Hetzner
                    
                    # Process location details
                    location_details = []
                    for location_code in locations_list:
                        location_info = location_map.get(location_code, {})
                        location_details.append({
                            'code': location_code,
                            'city': location_info.get('city', location_code),
                            'country': location_info.get('country', 'Unknown'),
                            'countryCode': location_info.get('countryCode', 'XX'),
                            'region': location_info.get('region', 'Unknown')
                        })
                    
                    # Calculate IPv6-only pricing
                    ipv6_only_monthly = max(0, monthly_price - ipv4_primary_ip_cost) if ipv4_primary_ip_cost > 0 else None
                    ipv6_only_hourly = ipv6_only_monthly / 730.44 if ipv6_only_monthly and ipv6_only_monthly > 0 else None
                    
                    # Create server entry with pricing options
                    server_data = {
                        'platform': 'cloud',
                        'type': 'cloud-server',
                        'instanceType': getattr(server_type, 'name', ''),
                        'vCPU': getattr(server_type, 'cores', 0),
                        'memoryGiB': getattr(server_type, 'memory', 0),
                        'diskType': getattr(server_type, 'storage_type', ''),
                        'diskSizeGB': getattr(server_type, 'disk', 0),
                        'cpuType': getattr(server_type, 'cpu_type', ''),
                        'architecture': getattr(server_type, 'architecture', ''),
                        'regions': locations_list,
                        'locationDetails': location_details,
                        'deprecated': getattr(server_type, 'deprecated', False),
                        'source': 'hetzner_cloud_api',
                        'description': getattr(server_type, 'description', ''),
                        'lastUpdated': datetime.now().isoformat(),
                        
                        # Pricing display (minimum pricing for sorting/filtering)
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        
                        # Regional pricing information
                        'regionalPricing': regional_pricing,
                        'priceRange': {
                            'hourly': {
                                'min': min_hourly,
                                'max': max_hourly,
                                'hasVariation': min_hourly != max_hourly
                            },
                            'monthly': {
                                'min': min_monthly,
                                'max': max_monthly,
                                'hasVariation': min_monthly != max_monthly
                            }
                        },
                        
                        # Network configuration options
                        'networkOptions': {
                            'ipv4_ipv6': {
                                'available': True,
                                'hourly': hourly_price,
                                'monthly': monthly_price,
                                'description': 'IPv4 + IPv6 included',
                                'priceRange': {
                                    'hourly': {'min': min_hourly, 'max': max_hourly},
                                    'monthly': {'min': min_monthly, 'max': max_monthly}
                                }
                            },
                            'ipv6_only': {
                                'available': ipv6_only_monthly is not None,
                                'hourly': ipv6_only_hourly,
                                'monthly': ipv6_only_monthly,
                                'savings': ipv4_primary_ip_cost if ipv4_primary_ip_cost > 0 else None,
                                'description': f'IPv6-only (saves €{ipv4_primary_ip_cost:.2f}/month)' if ipv4_primary_ip_cost > 0 else 'IPv6-only',
                                'priceRange': {
                                    'hourly': {
                                        'min': max(0, min_hourly - ipv4_primary_ip_cost / 730.44) if ipv4_primary_ip_cost else None,
                                        'max': max(0, max_hourly - ipv4_primary_ip_cost / 730.44) if ipv4_primary_ip_cost else None
                                    },
                                    'monthly': {
                                        'min': max(0, min_monthly - ipv4_primary_ip_cost) if ipv4_primary_ip_cost else None,
                                        'max': max(0, max_monthly - ipv4_primary_ip_cost) if ipv4_primary_ip_cost else None
                                    }
                                }
                            }
                        },
                        
                        # Default network type for filtering
                        'defaultNetworkType': 'ipv4_ipv6',
                        'supportsIPv6Only': ipv6_only_monthly is not None,
                        
                        'hetzner_metadata': {
                            'platform': 'cloud',
                            'apiSource': 'hcloud_library',
                            'serviceCategory': 'compute',
                            'ipv4_primary_ip_cost': ipv4_primary_ip_cost
                        }
                    }
                    
                    processed_servers.append(server_data)
                    
                except Exception as e:
                    logger.error(f"Error processing server type {server_type.name}: {e}")
            
            logger.info(f"Processed {len(processed_servers)} server types")
            return processed_servers
            
        except Exception as e:
            logger.error(f"Error fetching server types: {e}")
            return []
    
    def _collect_load_balancer_types(self) -> List[Dict[str, Any]]:
        """Collect load balancer types with pricing using hybrid approach."""
        logger.info("Fetching load balancer types...")
        
        try:
            # Get LB types from hcloud library
            lb_types = self.client.load_balancer_types.get_all()
            locations = self.client.locations.get_all()
            
            # Get pricing data via direct API call
            import requests
            headers = {
                'Authorization': f'Bearer {config.cloud_api_token}',
                'Content-Type': 'application/json'
            }
            
            pricing_response = requests.get("https://api.hetzner.cloud/v1/pricing", headers=headers)
            if pricing_response.status_code != 200:
                logger.error(f"Failed to fetch pricing data: {pricing_response.status_code}")
                return []
            
            pricing_data = pricing_response.json()
            pricing_by_type = {}
            
            if 'pricing' in pricing_data:
                for pricing_entry in pricing_data['pricing'].get('load_balancer_types', []):
                    pricing_by_type[pricing_entry.get('name')] = pricing_entry
            
            # Create location mapping for flags
            location_map = self._get_location_mapping(locations)
            
            processed_lbs = []
            
            for lb_type in lb_types:
                try:
                    # Get pricing for this LB type
                    pricing_info = pricing_by_type.get(lb_type.name, {})
                    
                    if 'prices' not in pricing_info:
                        logger.warning(f"No pricing found for load balancer type: {lb_type.name}")
                        continue
                    
                    # Process pricing (usually same across regions for LBs)
                    if pricing_info['prices']:
                        price = pricing_info['prices'][0]  # Take first price
                        hourly_price = float(price.get('price_hourly', {}).get('net', 0))
                        monthly_price = float(price.get('price_monthly', {}).get('net', 0))
                        
                        # Get all locations
                        locations_list = [p.get('location') for p in pricing_info['prices'] if p.get('location')]
                        
                        if hourly_price == 0 and monthly_price == 0:
                            continue
                    else:
                        continue
                    
                    # Process location details for flags
                    location_details = []
                    for location_code in locations_list:
                        location_info = location_map.get(location_code, {})
                        location_details.append({
                            'code': location_code,
                            'city': location_info.get('city', location_code),
                            'country': location_info.get('country', 'Unknown'),
                            'countryCode': location_info.get('countryCode', 'XX'),
                            'region': location_info.get('region', 'Unknown')
                        })
                    
                    lb_data = {
                        'platform': 'cloud',
                        'type': 'cloud-loadbalancer',
                        'instanceType': getattr(lb_type, 'name', ''),
                        'max_connections': getattr(lb_type, 'max_connections', 0),
                        'max_services': getattr(lb_type, 'max_services', 0),
                        'max_targets': getattr(lb_type, 'max_targets', 0),
                        'max_assigned_certificates': getattr(lb_type, 'max_assigned_certificates', 0),
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        'regions': locations_list,
                        'locationDetails': location_details,
                        'deprecated': getattr(lb_type, 'deprecated', False),
                        'source': 'hetzner_cloud_api',
                        'description': getattr(lb_type, 'description', ''),
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'cloud',
                            'apiSource': 'hcloud_library',
                            'serviceCategory': 'networking'
                        }
                    }
                    
                    processed_lbs.append(lb_data)
                    
                except Exception as e:
                    logger.error(f"Error processing LB type {lb_type.name}: {e}")
            
            logger.info(f"Processed {len(processed_lbs)} load balancer types")
            return processed_lbs
            
        except Exception as e:
            logger.error(f"Error fetching load balancer types: {e}")
            return []
    
    def _collect_other_services(self) -> List[Dict[str, Any]]:
        """Collect other services (for now, skip since pricing API not directly available)."""
        logger.info("Skipping other service pricing (not available via hcloud library)")
        
        # Note: The hcloud library doesn't expose a separate pricing client
        # Volume, floating IP, and other service pricing would need to be 
        # fetched via direct API calls or alternative methods
        
        return []
    
    def _get_location_mapping(self, locations: List[Location]) -> Dict[str, Dict[str, str]]:
        """Create mapping of location codes to detailed information."""
        location_map = {}
        
        # Known location mappings (can be enhanced with API data)
        known_locations = {
            'ash': {'city': 'Ashburn', 'country': 'United States', 'countryCode': 'US', 'region': 'Virginia'},
            'fsn1': {'city': 'Falkenstein', 'country': 'Germany', 'countryCode': 'DE', 'region': 'Saxony'},
            'hel1': {'city': 'Helsinki', 'country': 'Finland', 'countryCode': 'FI', 'region': 'Uusimaa'},
            'hil': {'city': 'Hildesheim', 'country': 'Germany', 'countryCode': 'DE', 'region': 'Lower Saxony'},
            'nbg1': {'city': 'Nuremberg', 'country': 'Germany', 'countryCode': 'DE', 'region': 'Bavaria'},
            'sin': {'city': 'Singapore', 'country': 'Singapore', 'countryCode': 'SG', 'region': 'Singapore'},
        }
        
        for location in locations:
            if location.name in known_locations:
                location_map[location.name] = known_locations[location.name]
            else:
                # Fallback based on API data
                location_map[location.name] = {
                    'city': location.city or location.name,
                    'country': location.country or 'Unknown',
                    'countryCode': location.country[:2].upper() if location.country else 'XX',
                    'region': location.description or 'Unknown'
                }
        
        return location_map

class HetznerDedicatedCollector:
    """Collector for Hetzner Dedicated services using hetzner library."""
    
    def __init__(self):
        if not HETZNER_ROBOT_AVAILABLE:
            raise ImportError("hetzner library not available")
        
        self.has_credentials = bool(config.robot_user and config.robot_password)
        
        if self.has_credentials:
            self.robot = Robot(config.robot_user, config.robot_password)
        else:
            logger.warning("Robot API credentials not provided - will attempt public endpoints only")
            self.robot = None
    
    def collect_all_dedicated_services(self) -> List[Dict[str, Any]]:
        """Collect all dedicated server services using official library."""
        logger.info("🖥️  Collecting Hetzner Dedicated services using official library...")
        
        try:
            # For now, provide sample dedicated server data since the Robot API endpoints are complex
            processed_servers = []
            
            logger.info("Using sample dedicated server data (Robot API integration pending)")
            
            # Sample Hetzner dedicated server offerings
            sample_servers = [
                {
                    'name': 'AX41-NVMe',
                    'cpu': 'AMD Ryzen 5 3600',
                    'cores': 6,
                    'ram': 64,
                    'storage': '2x 512 GB NVMe SSD',
                    'price': 39.0,
                    'datacenter': 'FSN1-DC14'
                },
                {
                    'name': 'AX51-NVMe', 
                    'cpu': 'AMD Ryzen 7 3700X',
                    'cores': 8,
                    'ram': 64,
                    'storage': '2x 512 GB NVMe SSD',
                    'price': 49.0,
                    'datacenter': 'FSN1-DC14'
                },
                {
                    'name': 'AX61-NVMe',
                    'cpu': 'AMD Ryzen 7 3700X',
                    'cores': 8,
                    'ram': 64,
                    'storage': '2x 1 TB NVMe SSD',
                    'price': 59.0,
                    'datacenter': 'NBG1-DC3'
                },
                {
                    'name': 'AX101',
                    'cpu': 'AMD Ryzen 9 5950X',
                    'cores': 16,
                    'ram': 128,
                    'storage': '2x 3.84 TB NVMe SSD',
                    'price': 129.0,
                    'datacenter': 'FSN1-DC14'
                }
            ]
            
            for server in sample_servers:
                try:
                    server_data = {
                        'platform': 'dedicated',
                        'type': 'dedicated-server',
                        'instanceType': server['name'],
                        'description': f"Dedicated server {server['name']} - {server['cpu']}",
                        'vCPU': server['cores'],
                        'memoryGiB': server['ram'],
                        'diskType': 'NVMe SSD' if 'NVMe' in server['storage'] else 'SSD',
                        'diskSizeGB': 1024 if '512 GB' in server['storage'] else 2048 if '1 TB' in server['storage'] else 7680,
                        'priceEUR_monthly_net': float(server['price']),
                        'priceEUR_hourly_net': float(server['price']) / 730.44,
                        'cpu_description': server['cpu'],
                        'ram_description': f"{server['ram']} GB DDR4",
                        'storage_description': server['storage'],
                        'datacenter': server['datacenter'],
                        'regions': ['Germany'],
                        'source': 'hetzner_sample_data',
                        'lastUpdated': datetime.now().isoformat(),
                        'locationDetails': [{
                            'code': server['datacenter'],
                            'city': 'Falkenstein' if 'FSN' in server['datacenter'] else 'Nuremberg',
                            'country': 'Germany',
                            'countryCode': 'DE',
                            'region': 'Germany'
                        }],
                        'hetzner_metadata': {
                            'platform': 'dedicated',
                            'apiSource': 'sample_data',
                            'serviceCategory': 'dedicated_server',
                            'datacenter': server['datacenter']
                        }
                    }
                    
                    processed_servers.append(server_data)
                    
                except Exception as e:
                    logger.error(f"Error processing sample server: {e}")
            
            
            logger.info(f"✅ Dedicated services: {len(processed_servers)} items")
            return processed_servers
            
        except Exception as e:
            logger.error(f"Error collecting dedicated services: {e}")
            return []

class HetznerDataCollector:
    """Main collector orchestrating both cloud and dedicated services."""
    
    def __init__(self):
        self.cloud_collector = None
        self.dedicated_collector = None
        
        # Initialize collectors based on configuration and library availability
        if config.enable_cloud and HCLOUD_AVAILABLE and config.cloud_api_token:
            try:
                self.cloud_collector = HetznerCloudCollector()
            except Exception as e:
                logger.error(f"Failed to initialize cloud collector: {e}")
        
        if config.enable_dedicated and HETZNER_ROBOT_AVAILABLE:
            try:
                self.dedicated_collector = HetznerDedicatedCollector()
            except Exception as e:
                logger.error(f"Failed to initialize dedicated collector: {e}")
    
    def collect_all_hetzner_data(self) -> List[Dict[str, Any]]:
        """Collect all Hetzner data from both platforms."""
        logger.info("🚀 Starting complete Hetzner data collection using official libraries...")
        
        all_data = []
        
        # Collect cloud services
        if self.cloud_collector:
            try:
                cloud_data = self.cloud_collector.collect_all_cloud_services()
                all_data.extend(cloud_data)
            except Exception as e:
                logger.error(f"Cloud collection failed: {e}")
        else:
            if config.enable_cloud:
                if not HCLOUD_AVAILABLE:
                    logger.warning("🔇 Cloud services disabled - hcloud library not available")
                elif not config.cloud_api_token:
                    logger.warning("🔇 Cloud services disabled - HETZNER_API_TOKEN not provided")
            else:
                logger.info("🔇 Cloud services disabled")
        
        # Collect dedicated services
        if self.dedicated_collector:
            try:
                dedicated_data = self.dedicated_collector.collect_all_dedicated_services()
                all_data.extend(dedicated_data)
            except Exception as e:
                logger.error(f"Dedicated collection failed: {e}")
        else:
            if config.enable_dedicated:
                if not HETZNER_ROBOT_AVAILABLE:
                    logger.warning("🔇 Dedicated services disabled - hetzner library not available")
                else:
                    logger.warning("🔇 Dedicated services disabled - collector initialization failed")
            else:
                logger.info("🔇 Dedicated services disabled")
        
        logger.info(f"📊 Total Hetzner services collected: {len(all_data)}")
        return all_data

# Main execution function (compatible with existing orchestrator)
def fetch_hetzner_cloud():
    """Main function for compatibility with existing orchestrator."""
    collector = HetznerDataCollector()
    return collector.collect_all_hetzner_data()

def main():
    """Main function for direct execution."""
    print("=== Hetzner Data Fetcher - Official Libraries Edition ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    # Show configuration
    print(f"\n🔧 Configuration:")
    print(f"  hcloud library: {'✅ Available' if HCLOUD_AVAILABLE else '❌ Not Available'}")
    print(f"  hetzner library: {'✅ Available' if HETZNER_ROBOT_AVAILABLE else '❌ Not Available'}")
    print(f"  Cloud API: {'✅ Enabled' if config.enable_cloud else '❌ Disabled'}")
    print(f"  Dedicated: {'✅ Enabled' if config.enable_dedicated else '❌ Disabled'}")
    
    if config.enable_cloud and not config.cloud_api_token:
        print(f"  ⚠️  Cloud API token not provided (set HETZNER_API_TOKEN)")
    
    if config.enable_dedicated and (not config.robot_user or not config.robot_password):
        print(f"  ⚠️  Robot API credentials not provided (set HETZNER_ROBOT_USER and HETZNER_ROBOT_PASSWORD)")
    
    try:
        data = fetch_hetzner_cloud()
        
        if data:
            # Save to file
            output_file = "data/hetzner.json"
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            print(f"\n✅ SUCCESS: Saved {len(data)} total entries to {output_file}")
            
            # Summary by platform and type
            platform_counts = {}
            type_counts = {}
            
            for item in data:
                platform = item.get('platform', 'unknown')
                item_type = item.get('type', 'unknown')
                
                platform_counts[platform] = platform_counts.get(platform, 0) + 1
                type_counts[item_type] = type_counts.get(item_type, 0) + 1
            
            print(f"\n📊 Summary by Platform:")
            for platform, count in platform_counts.items():
                print(f"  {platform}: {count} services")
            
            print(f"\n📊 Summary by Type:")
            for service_type, count in type_counts.items():
                print(f"  {service_type}: {count} services")
            
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