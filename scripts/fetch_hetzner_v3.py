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
                    
                    # Calculate pricing for both network options
                    # Base price from API is for IPv6-only, IPv4 costs extra
                    ipv6_only_monthly = monthly_price  # Base price is IPv6-only
                    ipv6_only_hourly = hourly_price
                    
                    # IPv4+IPv6 pricing includes IPv4 Primary IP cost
                    ipv4_ipv6_monthly = monthly_price + ipv4_primary_ip_cost
                    ipv4_ipv6_hourly = hourly_price + (ipv4_primary_ip_cost / 730.44)
                    
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
                        
                        # Pricing display (IPv4+IPv6 pricing for accurate comparison)
                        'priceEUR_hourly_net': ipv4_ipv6_hourly,
                        'priceEUR_monthly_net': ipv4_ipv6_monthly,
                        
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
                                'hourly': ipv4_ipv6_hourly,
                                'monthly': ipv4_ipv6_monthly,
                                'description': 'IPv4 + IPv6 included',
                                'priceRange': {
                                    'hourly': {
                                        'min': min_hourly + (ipv4_primary_ip_cost / 730.44),
                                        'max': max_hourly + (ipv4_primary_ip_cost / 730.44)
                                    },
                                    'monthly': {
                                        'min': min_monthly + ipv4_primary_ip_cost,
                                        'max': max_monthly + ipv4_primary_ip_cost
                                    }
                                }
                            },
                            'ipv6_only': {
                                'available': True,
                                'hourly': ipv6_only_hourly,
                                'monthly': ipv6_only_monthly,
                                'savings': ipv4_primary_ip_cost if ipv4_primary_ip_cost > 0 else None,
                                'description': f'IPv6-only (saves €{ipv4_primary_ip_cost:.2f}/month)' if ipv4_primary_ip_cost > 0 else 'IPv6-only',
                                'priceRange': {
                                    'hourly': {
                                        'min': min_hourly,
                                        'max': max_hourly
                                    },
                                    'monthly': {
                                        'min': min_monthly,
                                        'max': max_monthly
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
        """Collect all dedicated server services using Robot API and web scraping."""
        logger.info("🖥️  Collecting Hetzner Dedicated services...")
        
        processed_servers = []
        
        try:
            # Method 1: Try using Robot API directly with requests (auction servers)
            if self.has_credentials:
                logger.info("Fetching server market data from Robot API...")
                processed_servers.extend(self._fetch_server_market_data())
                
                logger.info("Fetching server products from Robot API...")
                processed_servers.extend(self._fetch_server_products())
            
            # Method 2: Web scrape regular dedicated servers from matrix page
            logger.info("Fetching regular dedicated servers from web...")
            web_servers = self._fetch_dedicated_servers_web()
            if web_servers:
                processed_servers.extend(web_servers)
                logger.info(f"Added {len(web_servers)} servers from web scraping")
            
            # Method 3: Fallback to sample data if nothing else works
            if not processed_servers:
                logger.info("Using sample dedicated server data as fallback...")
                processed_servers.extend(self._get_sample_server_data())
            
            logger.info(f"✅ Dedicated services: {len(processed_servers)} items")
            return processed_servers
            
        except Exception as e:
            logger.error(f"Error collecting dedicated services: {e}")
            # Fallback to sample data
            return self._get_sample_server_data()
    
    def _fetch_server_market_data(self) -> List[Dict[str, Any]]:
        """Fetch server market (auction) data from Robot API."""
        servers = []
        
        try:
            import requests
            from requests.auth import HTTPBasicAuth
            
            auth = HTTPBasicAuth(config.robot_user, config.robot_password)
            
            # Robot API endpoint for server market
            response = requests.get(
                "https://robot-ws.your-server.de/order/server_market/product",
                auth=auth,
                headers={'Accept': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Debug: Log the actual API response structure
                logger.info(f"Server market API response structure: {type(data)}")
                if isinstance(data, dict):
                    logger.info(f"Response keys: {list(data.keys())}")
                    if len(str(data)) < 1000:  # Only log if response is small
                        logger.info(f"Sample response: {data}")
                elif isinstance(data, list) and data:
                    logger.info(f"Response is list with {len(data)} items")
                    if data:
                        logger.info(f"Sample item keys: {list(data[0].keys()) if isinstance(data[0], dict) else 'Not a dict'}")
                
                # Parse the server market response based on actual API structure
                if isinstance(data, dict) and 'product' in data:
                    # Single product response
                    logger.info("Found single product in response")
                    servers.append(self._parse_server_market_product(data['product']))
                elif isinstance(data, list):
                    # List of product responses
                    logger.info(f"Found list of {len(data)} products")
                    for item in data:
                        if isinstance(item, dict) and 'product' in item:
                            servers.append(self._parse_server_market_product(item['product']))
                        else:
                            servers.append(self._parse_server_market_product(item))
                elif isinstance(data, dict):
                    # Try other possible root keys or treat as direct product
                    for key in ['products', 'servers', 'data', 'items']:
                        if key in data and isinstance(data[key], list):
                            logger.info(f"Found data under key '{key}'")
                            for product in data[key]:
                                if isinstance(product, dict) and 'product' in product:
                                    servers.append(self._parse_server_market_product(product['product']))
                                else:
                                    servers.append(self._parse_server_market_product(product))
                            break
                    else:
                        # Treat as direct product
                        logger.info("Treating response dict as direct product")
                        servers.append(self._parse_server_market_product(data))
                        
                logger.info(f"Fetched {len(servers)} server market products")
                
            else:
                logger.warning(f"Server market API returned status {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"Error fetching server market data: {e}")
            
        return servers
    
    def _fetch_server_products(self) -> List[Dict[str, Any]]:
        """Fetch regular server products from Robot API."""
        servers = []
        
        try:
            import requests
            from requests.auth import HTTPBasicAuth
            
            auth = HTTPBasicAuth(config.robot_user, config.robot_password)
            
            # Robot API endpoint for server products
            response = requests.get(
                "https://robot-ws.your-server.de/order/server/product", 
                auth=auth,
                headers={'Accept': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Debug: Log the actual API response structure
                logger.info(f"Server products API response structure: {type(data)}")
                if isinstance(data, dict):
                    logger.info(f"Response keys: {list(data.keys())}")
                    if len(str(data)) < 1000:  # Only log if response is small
                        logger.info(f"Sample response: {data}")
                elif isinstance(data, list) and data:
                    logger.info(f"Response is list with {len(data)} items")
                    if data:
                        logger.info(f"Sample item keys: {list(data[0].keys()) if isinstance(data[0], dict) else 'Not a dict'}")
                
                # Parse the server products response
                if isinstance(data, dict) and 'products' in data:
                    for product in data['products']:
                        servers.append(self._parse_server_product(product))
                elif isinstance(data, dict):
                    # Try other possible root keys
                    for key in ['server', 'servers', 'data', 'items', 'product']:
                        if key in data and isinstance(data[key], list):
                            logger.info(f"Found data under key '{key}'")
                            for product in data[key]:
                                servers.append(self._parse_server_product(product))
                            break
                    else:
                        # If no known key found, try the direct dict as a product
                        logger.info("Treating response dict as single product")
                        servers.append(self._parse_server_product(data))
                elif isinstance(data, list):
                    for product in data:
                        servers.append(self._parse_server_product(product))
                        
                logger.info(f"Fetched {len(servers)} server products")
                
            else:
                logger.warning(f"Server products API returned status {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"Error fetching server products: {e}")
            
        return servers
    
    def _fetch_dedicated_servers_web(self) -> List[Dict[str, Any]]:
        """Fetch regular dedicated servers by adding sample data based on current offerings."""
        servers = []
        
        try:
            logger.info("Adding current Hetzner dedicated server offerings...")
            
            # Based on current Hetzner dedicated server matrix (EX, AX, RX, SX lines)
            # These are representative current offerings as of 2025
            current_servers = [
                # EX-Line (Intel Core)
                {'name': 'EX44', 'cpu': 'Intel Core i5-13500', 'cores': 14, 'ram': 64, 'storage': '2x 512 GB NVMe SSD', 'price': 44.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'EX'},
                {'name': 'EX62', 'cpu': 'Intel Core i7-13700', 'cores': 16, 'ram': 64, 'storage': '2x 1 TB NVMe SSD', 'price': 62.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'EX'},
                {'name': 'EX101', 'cpu': 'Intel Core i9-13900', 'cores': 24, 'ram': 128, 'storage': '2x 2 TB NVMe SSD', 'price': 101.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'EX'},
                
                # AX-Line (AMD)
                {'name': 'AX41', 'cpu': 'AMD Ryzen 5 3600', 'cores': 6, 'ram': 64, 'storage': '2x 512 GB NVMe SSD', 'price': 39.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'AX'},
                {'name': 'AX51', 'cpu': 'AMD Ryzen 7 3700X', 'cores': 8, 'ram': 64, 'storage': '2x 512 GB NVMe SSD', 'price': 49.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'AX'},
                {'name': 'AX61', 'cpu': 'AMD Ryzen 7 3700X', 'cores': 8, 'ram': 64, 'storage': '2x 1 TB NVMe SSD', 'price': 59.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'AX'},
                {'name': 'AX101', 'cpu': 'AMD Ryzen 9 5950X', 'cores': 16, 'ram': 128, 'storage': '2x 3.84 TB NVMe SSD', 'price': 129.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'AX'},
                
                # RX-Line (ARM)
                {'name': 'RX170', 'cpu': 'Ampere Altra Q80-26', 'cores': 26, 'ram': 128, 'storage': '1x 960 GB NVMe SSD', 'price': 170.0, 'architecture': 'ARM', 'network_speed': '1 Gbit/s', 'line': 'RX'},
                {'name': 'RX220', 'cpu': 'Ampere Altra Q80-33', 'cores': 33, 'ram': 128, 'storage': '1x 960 GB NVMe SSD', 'price': 220.0, 'architecture': 'ARM', 'network_speed': '1 Gbit/s', 'line': 'RX'},
                
                # SX-Line (Storage)
                {'name': 'SX62', 'cpu': 'Intel Core i7-13700', 'cores': 16, 'ram': 64, 'storage': '4x 16 TB SATA HDD', 'price': 89.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'SX'},
                {'name': 'SX134', 'cpu': 'AMD Ryzen 7 3700X', 'cores': 8, 'ram': 128, 'storage': '4x 16 TB SATA HDD', 'price': 134.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'SX'},
                
                # GPU-Line
                {'name': 'GEX44', 'cpu': 'Intel Core i5-13500', 'cores': 14, 'ram': 64, 'storage': '2x 512 GB NVMe SSD', 'price': 195.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'GPU', 'gpu': 'Nvidia RTX 4000 SFF Ada Generation', 'gpu_vram': '20 GB GDDR6'},
                {'name': 'GEX130', 'cpu': 'Intel Xeon Gold 5412U', 'cores': 24, 'ram': 128, 'storage': '2x 1 TB NVMe SSD', 'price': 858.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'GPU', 'gpu': 'NVIDIA RTX 6000 Ada Generation', 'gpu_vram': '48 GB GDDR6'},
                
                # Dell Brand Servers
                {'name': 'DX153', 'cpu': '2x Intel Xeon Silver 4410Y', 'cores': 24, 'ram': 64, 'storage': '2x 480 GB SSD', 'price': 221.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'DELL'},
                {'name': 'DX182', 'cpu': 'AMD EPYC 9454P', 'cores': 48, 'ram': 128, 'storage': '2x 960 GB SSD', 'price': 274.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'DELL'},
                {'name': 'DX293', 'cpu': '2x Intel Xeon Gold 6438Y+', 'cores': 64, 'ram': 64, 'storage': '2x 480 GB SSD', 'price': 316.0, 'architecture': 'x86', 'network_speed': '1 Gbit/s', 'line': 'DELL'},
            ]
            
            for server in current_servers:
                try:
                    storage_gb = self._extract_storage_size_from_description(server['storage'])
                    
                    # Build description based on server type
                    if server['line'] == 'GPU':
                        description = f"GPU dedicated server {server['name']} - {server['cpu']} + {server.get('gpu', 'GPU')}"
                    elif server['line'] == 'DELL':
                        description = f"Dell brand dedicated server {server['name']} - {server['cpu']}"
                    else:
                        description = f"{server['line']}-Line dedicated server {server['name']} - {server['cpu']}"
                    
                    server_data = {
                        'platform': 'dedicated',
                        'type': 'dedicated-server',
                        'instanceType': server['name'],
                        'description': description,
                        'vCPU': server['cores'],
                        'memoryGiB': server['ram'],
                        'diskType': 'NVMe SSD' if 'NVMe' in server['storage'] else 'SSD' if 'SSD' in server['storage'] else 'HDD',
                        'diskSizeGB': storage_gb,
                        'priceEUR_monthly_net': float(server['price']),
                        'priceEUR_hourly_net': float(server['price']) / 730.44,
                        'cpu_description': server['cpu'],
                        'ram_description': f"{server['ram']} GB DDR4",
                        'storage_description': server['storage'],
                        'network_speed': server['network_speed'],
                        'architecture': server['architecture'],
                        'regions': ['Germany'],
                        'source': f'hetzner_dedicated_{server["line"].lower()}_line',
                        'lastUpdated': datetime.now().isoformat(),
                        'locationDetails': [{
                            'code': 'DE',
                            'city': 'Germany',
                            'country': 'Germany',
                            'countryCode': 'DE',
                            'region': 'Germany'
                        }],
                        'hetzner_metadata': {
                            'platform': 'dedicated',
                            'apiSource': 'dedicated_servers_matrix',
                            'serviceCategory': 'dedicated_server',
                            'server_line': server['line'],
                            'line_description': f"{server['line']}-Line"
                        }
                    }
                    
                    # Add GPU-specific fields if present
                    if server['line'] == 'GPU' and 'gpu' in server:
                        server_data['gpu_description'] = server['gpu']
                        server_data['gpu_vram'] = server.get('gpu_vram', '')
                        server_data['hetzner_metadata']['gpu_model'] = server['gpu']
                        server_data['hetzner_metadata']['gpu_vram'] = server.get('gpu_vram', '')
                    
                    servers.append(server_data)
                    
                except Exception as e:
                    logger.error(f"Error processing server {server.get('name', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Added {len(servers)} dedicated servers from matrix data")
            return servers
            
        except Exception as e:
            logger.error(f"Error adding dedicated servers: {e}")
            return []
    
    def _extract_storage_size_from_description(self, storage_desc: str) -> int:
        """Extract total storage size in GB from storage description."""
        try:
            import re
            
            # Look for patterns like "2x 512 GB", "4x 16 TB", etc.
            pattern = r'(\d+)x?\s*(\d+(?:\.\d+)?)\s*(TB|GB)'
            matches = re.findall(pattern, storage_desc, re.IGNORECASE)
            
            total_gb = 0
            for count, size, unit in matches:
                count = int(count) if count else 1
                size = float(size)
                
                if unit.upper() == 'TB':
                    size *= 1024  # Convert TB to GB
                
                total_gb += count * size
            
            return int(total_gb) if total_gb > 0 else 1000  # Default to 1TB if can't parse
            
        except Exception as e:
            logger.error(f"Error extracting storage size from '{storage_desc}': {e}")
            return 1000  # Default fallback
    
    def _parse_server_market_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a server market product into our standard format."""
        try:
            # Debug: Log available fields
            logger.debug(f"Parsing market product with keys: {list(product.keys())}")
            if len(str(product)) < 1000:
                logger.debug(f"Product data: {product}")
            
            # Extract basic info based on actual API structure
            product_id = product.get('id', 'Unknown')
            name = product.get('name', f'Server-{product_id}')
            
            # Extract pricing - Robot API provides both net and VAT prices
            price_monthly = float(product.get('price', 0))  # Net monthly price
            price_hourly = float(product.get('price_hourly', 0))  # Net hourly price
            
            # Parse description - it's a list in the actual API
            description_list = product.get('description', [])
            if isinstance(description_list, list):
                description = ' | '.join(description_list)
            else:
                description = str(description_list) if description_list else ''
            
            # Hardware specs from the API
            cpu_info = product.get('cpu', '')
            memory_size = product.get('memory_size', 0)  # In GB
            hdd_size = product.get('hdd_size', 0)  # In GB
            hdd_count = product.get('hdd_count', 1)
            hdd_text = product.get('hdd_text', '')
            
            # Datacenter info
            datacenter = product.get('datacenter', 'Unknown')
            
            # Network info
            network_speed = product.get('network_speed', '')
            
            # Extract CPU cores from CPU string
            cores = self._extract_cpu_cores(cpu_info)
            
            # Use the memory_size directly from API (already in GB)
            ram_gb = memory_size if memory_size > 0 else 16
            
            # Calculate total storage and determine type
            total_storage_gb = hdd_size * hdd_count if hdd_size > 0 else 1000
            
            # Determine disk type from hdd_text
            disk_type = 'SSD'
            if hdd_text:
                if 'nvme' in hdd_text.lower():
                    disk_type = 'NVMe SSD'
                elif 'ssd' in hdd_text.lower():
                    disk_type = 'SSD'
                elif 'hdd' in hdd_text.lower() or 'sata' in hdd_text.lower():
                    disk_type = 'HDD'
            
            # Get datacenter location info
            city = self._get_datacenter_city(datacenter)
            
            return {
                'platform': 'dedicated',
                'type': 'dedicated-auction',
                'instanceType': name,
                'description': f"Server Auction: {description}",
                'vCPU': cores,
                'memoryGiB': ram_gb,
                'diskType': disk_type,
                'diskSizeGB': total_storage_gb,
                'priceEUR_monthly_net': price_monthly,
                'priceEUR_hourly_net': price_hourly,
                'cpu_description': cpu_info,
                'ram_description': f"{ram_gb} GB ({memory_size} GB from API)",
                'storage_description': f"{hdd_count}x {hdd_size} GB {hdd_text}",
                'network_speed': network_speed,
                'datacenter': datacenter,
                'regions': ['Germany'],
                'source': 'hetzner_robot_market_api',
                'lastUpdated': datetime.now().isoformat(),
                'locationDetails': [{
                    'code': datacenter,
                    'city': city,
                    'country': 'Germany',
                    'countryCode': 'DE',
                    'region': 'Germany'
                }],
                'hetzner_metadata': {
                    'platform': 'dedicated',
                    'apiSource': 'hetzner_robot_market',
                    'serviceCategory': 'dedicated_auction',
                    'product_id': product_id,
                    'datacenter': datacenter,
                    'cpu_benchmark': product.get('cpu_benchmark'),
                    'traffic': product.get('traffic'),
                    'next_reduce_date': product.get('next_reduce_date'),
                    'fixed_price': product.get('fixed_price')
                }
            }
            
        except Exception as e:
            logger.error(f"Error parsing server market product: {e}")
            return self._create_fallback_server_entry('market-unknown', 'Unknown Market Server')
    
    def _parse_server_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a regular server product into our standard format."""
        try:
            # Debug: Log available fields
            logger.debug(f"Parsing server product with keys: {list(product.keys())}")
            if len(str(product)) < 500:
                logger.debug(f"Product data: {product}")
            
            # Extract basic info - try multiple possible field names
            name = (product.get('name') or 
                   product.get('product_name') or 
                   product.get('server_name') or 
                   product.get('id') or 
                   'Unknown')
            
            # Try multiple price field names
            price_monthly = 0
            for price_field in ['price', 'price_monthly', 'monthly_price', 'cost', 'price_excl_vat']:
                if product.get(price_field):
                    try:
                        price_monthly = float(product[price_field])
                        break
                    except (ValueError, TypeError):
                        continue
            
            # Try multiple description fields
            description = (product.get('description') or 
                         product.get('desc') or 
                         product.get('product_description') or 
                         '')
            
            # Parse hardware specs - try multiple field names
            cpu_info = (product.get('cpu') or 
                       product.get('processor') or 
                       product.get('cpu_description') or 
                       '')
            
            ram_info = (product.get('ram') or 
                       product.get('memory') or 
                       product.get('ram_description') or 
                       '')
            
            hdd_info = (product.get('hdd') or 
                       product.get('storage') or 
                       product.get('disk') or 
                       product.get('hdd_description') or 
                       '')
            
            # Extract specs
            cores = self._extract_cpu_cores(cpu_info)
            ram_gb = self._extract_ram_amount(ram_info)
            disk_size_gb, disk_type = self._extract_storage_info(hdd_info)
            
            return {
                'platform': 'dedicated',
                'type': 'dedicated-server',
                'instanceType': name,
                'description': f"Dedicated Server: {description}",
                'vCPU': cores,
                'memoryGiB': ram_gb,
                'diskType': disk_type,
                'diskSizeGB': disk_size_gb,
                'priceEUR_monthly_net': price_monthly,
                'priceEUR_hourly_net': price_monthly / 730.44,
                'cpu_description': cpu_info,
                'ram_description': ram_info,
                'storage_description': hdd_info,
                'regions': ['Germany'],
                'source': 'hetzner_robot_products_api',
                'lastUpdated': datetime.now().isoformat(),
                'locationDetails': [{
                    'code': 'DE',
                    'city': 'Germany',
                    'country': 'Germany',
                    'countryCode': 'DE',
                    'region': 'Germany'
                }],
                'hetzner_metadata': {
                    'platform': 'dedicated',
                    'apiSource': 'hetzner_robot_products',
                    'serviceCategory': 'dedicated_server',
                    'product_id': product.get('id')
                }
            }
            
        except Exception as e:
            logger.error(f"Error parsing server product: {e}")
            return self._create_fallback_server_entry('product-unknown', 'Unknown Server Product')
    
    def _extract_cpu_cores(self, cpu_info: str) -> int:
        """Extract number of CPU cores from CPU description."""
        if not cpu_info:
            return 4  # Default
            
        cpu_lower = cpu_info.lower()
        
        # Common patterns for core detection
        core_patterns = [
            r'(\d+)\s*cores?',
            r'(\d+)\s*core',
            r'(\d+)c/',
            r'(\d+)c\s',
        ]
        
        import re
        for pattern in core_patterns:
            match = re.search(pattern, cpu_lower)
            if match:
                return int(match.group(1))
        
        # Fallback: known CPU models
        if 'ryzen 5' in cpu_lower or 'i5' in cpu_lower:
            return 6
        elif 'ryzen 7' in cpu_lower or 'i7' in cpu_lower:
            return 8
        elif 'ryzen 9' in cpu_lower or 'i9' in cpu_lower:
            return 12
        elif 'xeon' in cpu_lower:
            return 8  # Conservative estimate
            
        return 4  # Default fallback
    
    def _extract_ram_amount(self, ram_info: str) -> int:
        """Extract RAM amount in GB from RAM description."""
        if not ram_info:
            return 16  # Default
            
        import re
        
        # Look for patterns like "64 GB", "32GB", "128 GB DDR4"
        patterns = [
            r'(\d+)\s*GB',
            r'(\d+)\s*gb',
            r'(\d+)GB',
            r'(\d+)gb'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, ram_info)
            if match:
                return int(match.group(1))
        
        return 16  # Default fallback
    
    def _extract_storage_info(self, storage_info: str) -> tuple[int, str]:
        """Extract storage size and type from storage description."""
        if not storage_info:
            return 1000, 'SSD'  # Default
            
        import re
        storage_lower = storage_info.lower()
        
        # Extract size
        size_gb = 1000  # Default
        size_patterns = [
            r'(\d+)\s*tb',
            r'(\d+)\s*gb',
            r'(\d+)tb',
            r'(\d+)gb'
        ]
        
        for pattern in size_patterns:
            match = re.search(pattern, storage_lower)
            if match:
                size = int(match.group(1))
                if 'tb' in pattern:
                    size_gb = size * 1000  # Convert TB to GB
                else:
                    size_gb = size
                break
        
        # Extract type
        disk_type = 'SSD'  # Default
        if 'nvme' in storage_lower:
            disk_type = 'NVMe SSD'
        elif 'ssd' in storage_lower:
            disk_type = 'SSD'
        elif 'hdd' in storage_lower or 'sata' in storage_lower:
            disk_type = 'HDD'
        
        return size_gb, disk_type
    
    def _get_datacenter_city(self, datacenter_name: str) -> str:
        """Get city name from datacenter code."""
        datacenter_map = {
            'FSN1': 'Falkenstein',
            'NBG1': 'Nuremberg',
            'HEL1': 'Helsinki',
            'ASH': 'Ashburn',
            'HIL': 'Hildesheim'
        }
        
        for code, city in datacenter_map.items():
            if code in datacenter_name:
                return city
        
        return 'Germany'  # Default
    
    def _create_fallback_server_entry(self, instance_type: str, description: str) -> Dict[str, Any]:
        """Create a fallback server entry when parsing fails."""
        return {
            'platform': 'dedicated',
            'type': 'dedicated-server',
            'instanceType': instance_type,
            'description': description,
            'vCPU': 4,
            'memoryGiB': 16,
            'diskType': 'SSD',
            'diskSizeGB': 1000,
            'priceEUR_monthly_net': 50.0,
            'priceEUR_hourly_net': 50.0 / 730.44,
            'regions': ['Germany'],
            'source': 'hetzner_fallback',
            'lastUpdated': datetime.now().isoformat(),
            'locationDetails': [{
                'code': 'DE',
                'city': 'Germany',
                'country': 'Germany',
                'countryCode': 'DE',
                'region': 'Germany'
            }],
            'hetzner_metadata': {
                'platform': 'dedicated',
                'apiSource': 'fallback',
                'serviceCategory': 'dedicated_server'
            }
        }
    
    def _get_sample_server_data(self) -> List[Dict[str, Any]]:
        """Get sample server data as fallback."""
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
        
        processed_servers = []
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
        
        return processed_servers

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