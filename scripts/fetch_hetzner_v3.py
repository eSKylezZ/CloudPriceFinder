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
        """Collect server types with pricing using official library."""
        logger.info("Fetching server types...")
        
        try:
            server_types = self.client.server_types.get_all()
            locations = self.client.locations.get_all()
            
            # Create location mapping
            location_map = self._get_location_mapping(locations)
            
            processed_servers = []
            
            for server_type in server_types:
                try:
                    # Check if server type has pricing information
                    if not hasattr(server_type, 'prices') or not server_type.prices:
                        logger.warning(f"No pricing found for server type: {server_type.name}")
                        continue
                    
                    # Process regional pricing
                    regional_pricing = []
                    locations_list = []
                    
                    for price in server_type.prices:
                        location_code = price.location.name
                        locations_list.append(location_code)
                        
                        regional_pricing.append({
                            'location': location_code,
                            'hourly_net': float(price.price_hourly.net),
                            'monthly_net': float(price.price_monthly.net),
                            'included_traffic': getattr(price, 'included_traffic', 0),
                            'traffic_price_per_tb': float(getattr(price, 'price_per_tb_traffic', {}).get('net', 0))
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
                    ipv6_only_hourly = ipv6_only_monthly / (24 * 30) if ipv6_only_monthly and ipv6_only_monthly > 0 else None
                    
                    # Create server entry with pricing options
                    server_data = {
                        'platform': 'cloud',
                        'type': 'cloud-server',
                        'instanceType': server_type.name,
                        'vCPU': server_type.cores,
                        'memoryGiB': server_type.memory,
                        'diskType': server_type.storage_type,
                        'diskSizeGB': server_type.disk,
                        'cpuType': server_type.cpu_type,
                        'architecture': server_type.architecture,
                        'regions': locations_list,
                        'locationDetails': location_details,
                        'deprecated': server_type.deprecated,
                        'source': 'hetzner_cloud_api',
                        'description': server_type.description,
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
                                        'min': max(0, min_hourly - ipv4_primary_ip_cost / (24 * 30)) if ipv4_primary_ip_cost else None,
                                        'max': max(0, max_hourly - ipv4_primary_ip_cost / (24 * 30)) if ipv4_primary_ip_cost else None
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
        """Collect load balancer types with pricing."""
        logger.info("Fetching load balancer types...")
        
        try:
            lb_types = self.client.load_balancer_types.get_all()
            
            processed_lbs = []
            
            for lb_type in lb_types:
                try:
                    # Check if LB type has pricing information
                    if not hasattr(lb_type, 'prices') or not lb_type.prices:
                        logger.warning(f"No pricing found for load balancer type: {lb_type.name}")
                        continue
                    
                    # Process pricing (usually same across regions for LBs)
                    if lb_type.prices:
                        price = lb_type.prices[0]  # Take first price
                        hourly_price = float(price.price_hourly.net)
                        monthly_price = float(price.price_monthly.net)
                        
                        # Get all locations
                        locations = [p.location.name for p in lb_type.prices]
                    else:
                        continue
                    
                    lb_data = {
                        'platform': 'cloud',
                        'type': 'cloud-loadbalancer',
                        'instanceType': lb_type.name,
                        'max_connections': lb_type.max_connections,
                        'max_services': lb_type.max_services,
                        'max_targets': lb_type.max_targets,
                        'max_assigned_certificates': lb_type.max_assigned_certificates,
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        'regions': locations,
                        'deprecated': lb_type.deprecated,
                        'source': 'hetzner_cloud_api',
                        'description': lb_type.description,
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
        
        if not config.robot_user or not config.robot_password:
            raise ValueError("HETZNER_ROBOT_USER and HETZNER_ROBOT_PASSWORD not provided")
        
        self.robot = Robot(config.robot_user, config.robot_password)
    
    def collect_all_dedicated_services(self) -> List[Dict[str, Any]]:
        """Collect all dedicated server services using official library."""
        logger.info("🖥️  Collecting Hetzner Dedicated services using official library...")
        
        try:
            servers = self.robot.servers
            
            processed_servers = []
            
            for server in servers:
                try:
                    server_data = {
                        'platform': 'dedicated',
                        'type': 'dedicated-server',
                        'instanceType': server.product,
                        'description': f'Dedicated server {server.product}',
                        'server_name': server.name,
                        'server_ip': server.ip,
                        'datacenter': getattr(server, 'datacenter', 'Unknown'),
                        'regions': ['Germany'],  # Hetzner dedicated servers are typically in Germany
                        'source': 'hetzner_robot_api',
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'dedicated',
                            'apiSource': 'hetzner_library',
                            'serviceCategory': 'dedicated_compute',
                            'server_number': server.number
                        }
                    }
                    
                    processed_servers.append(server_data)
                    
                except Exception as e:
                    logger.error(f"Error processing dedicated server: {e}")
            
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
        
        if config.enable_dedicated and HETZNER_ROBOT_AVAILABLE and config.robot_user and config.robot_password:
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
                elif not config.robot_user or not config.robot_password:
                    logger.warning("🔇 Dedicated services disabled - Robot API credentials not provided")
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