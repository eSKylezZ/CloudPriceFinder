#!/usr/bin/env python3
"""
Hetzner Complete Data Fetcher - Refactored Edition
Comprehensive data collection for both Hetzner Cloud and Dedicated Server platforms.
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

# Configuration
class HetznerConfig:
    def __init__(self):
        # Cloud API Configuration
        self.cloud_api_url = "https://api.hetzner.cloud/v1"
        self.cloud_api_token = os.environ.get("HETZNER_API_TOKEN", "")
        
        # Robot API Configuration (for dedicated servers)
        self.robot_api_url = "https://robot.hetzner.com"
        self.robot_user = os.environ.get("HETZNER_ROBOT_USER", "")
        self.robot_password = os.environ.get("HETZNER_ROBOT_PASSWORD", "")
        
        # Feature flags
        self.enable_cloud = os.environ.get("HETZNER_ENABLE_CLOUD", "true").lower() == "true"
        self.enable_dedicated = os.environ.get("HETZNER_ENABLE_DEDICATED", "false").lower() == "true"
        self.enable_auction = os.environ.get("HETZNER_ENABLE_AUCTION", "false").lower() == "true"
        
        # Rate limiting and caching
        self.api_rate_limit_delay = 0.1
        self.api_retry_count = 3
        self.api_timeout = 15
        self.cache_duration = 300  # 5 minutes

config = HetznerConfig()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global cache
_api_cache: Dict[str, Tuple[Any, float]] = {}

class HetznerAPIClient:
    """Unified API client for both Cloud and Robot APIs."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CloudCosts/2.0 (https://github.com/cloudcosts/cloudcosts)'
        })
    
    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get data from cache if still valid."""
        if key in _api_cache:
            data, timestamp = _api_cache[key]
            if time.time() - timestamp < config.cache_duration:
                return data
        return None
    
    def _set_cache(self, key: str, data: Any) -> None:
        """Set data in cache with timestamp."""
        _api_cache[key] = (data, time.time())
    
    def _make_request(self, url: str, headers: Dict[str, str], 
                     auth: Optional[Tuple[str, str]] = None) -> Optional[Dict[str, Any]]:
        """Make HTTP request with retry logic and caching."""
        cache_key = f"http_{url}_{str(headers)}"
        
        # Check cache first
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return cached_data
        
        for attempt in range(config.api_retry_count + 1):
            try:
                time.sleep(config.api_rate_limit_delay)
                
                response = self.session.get(
                    url,
                    headers=headers,
                    auth=auth,
                    timeout=config.api_timeout
                )
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        self._set_cache(cache_key, data)
                        return data
                    except ValueError as e:
                        logger.error(f"Invalid JSON response from {url}: {e}")
                        logger.debug(f"Response content: {response.text[:500]}...")
                        return None
                elif response.status_code == 401:
                    logger.error(f"Authentication failed for {url}")
                    return None
                elif response.status_code == 403:
                    logger.error(f"Access forbidden for {url} - check API credentials")
                    return None
                elif response.status_code == 429:
                    wait_time = 2 ** attempt
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"HTTP {response.status_code} for {url}")
                    logger.debug(f"Response content: {response.text[:200]}...")
                    
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < config.api_retry_count:
                    time.sleep(2 ** attempt)
                    continue
        
        logger.error(f"Failed to fetch data from {url} after {config.api_retry_count + 1} attempts")
        return None
    
    def cloud_api_request(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Make request to Hetzner Cloud API."""
        if not config.cloud_api_token:
            logger.error("Cloud API token not provided")
            return None
        
        url = f"{config.cloud_api_url}/{endpoint.lstrip('/')}"
        headers = {
            'Authorization': f'Bearer {config.cloud_api_token}',
            'Content-Type': 'application/json'
        }
        
        return self._make_request(url, headers)
    
    def robot_api_request(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Make request to Hetzner Robot API."""
        if not config.robot_user or not config.robot_password:
            logger.info("Robot API credentials not provided - skipping dedicated servers")
            return None
        
        url = f"{config.robot_api_url}/{endpoint.lstrip('/')}"
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        auth = (config.robot_user, config.robot_password)
        
        return self._make_request(url, headers, auth)

class HetznerCloudCollector:
    """Collector for Hetzner Cloud services."""
    
    def __init__(self, api_client: HetznerAPIClient):
        self.api = api_client
    
    def collect_all_cloud_services(self) -> List[Dict[str, Any]]:
        """Collect all cloud services data."""
        logger.info("🌩️  Collecting Hetzner Cloud services...")
        
        all_services = []
        
        # Collect different service types
        services = [
            ('server_types', self._process_server_types),
            ('load_balancer_types', self._process_load_balancer_types),
            ('pricing', self._process_pricing_services),
        ]
        
        for service_name, processor in services:
            try:
                logger.info(f"Fetching {service_name}...")
                raw_data = self.api.cloud_api_request(service_name)
                
                if raw_data:
                    processed_data = processor(raw_data)
                    all_services.extend(processed_data)
                    logger.info(f"✅ {service_name}: {len(processed_data)} items")
                else:
                    logger.warning(f"❌ {service_name}: No data received")
                    
            except Exception as e:
                logger.error(f"Error processing {service_name}: {e}")
        
        return all_services
    
    def _process_server_types(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process cloud server types with pricing."""
        server_types = data.get('server_types', [])
        
        # Get pricing data for enrichment
        pricing_data = self.api.cloud_api_request('pricing')
        pricing_by_type = {}
        
        if pricing_data and 'pricing' in pricing_data:
            for pricing_entry in pricing_data['pricing'].get('server_types', []):
                pricing_by_type[pricing_entry.get('name')] = pricing_entry
        
        processed_servers = []
        
        for server_type in server_types:
            try:
                name = server_type.get('name')
                pricing_info = pricing_by_type.get(name, {})
                
                # Extract pricing for all regions
                regional_pricing = []
                locations = []
                
                if 'prices' in pricing_info:
                    for price_entry in pricing_info['prices']:
                        location = price_entry.get('location')
                        if location:
                            locations.append(location)
                            
                            hourly_net = float(price_entry.get('price_hourly', {}).get('net', 0))
                            monthly_net = float(price_entry.get('price_monthly', {}).get('net', 0))
                            
                            regional_pricing.append({
                                'location': location,
                                'hourly_net': hourly_net,
                                'monthly_net': monthly_net,
                                'included_traffic': price_entry.get('included_traffic', 0),
                                'traffic_price_per_tb': price_entry.get('price_per_tb_traffic', {}).get('net', 0)
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
                    hourly_price = None
                    monthly_price = None
                    min_hourly = max_hourly = None
                    min_monthly = max_monthly = None
                
                # Get IPv4 Primary IP pricing for accurate IPv6-only calculations
                ipv4_primary_ip_cost = self._get_ipv4_primary_ip_cost(locations)
                
                # Process location information with country mapping
                location_details = []
                for location in locations:
                    location_info = self._get_location_info(location)
                    location_details.append({
                        'code': location,
                        'city': location_info['city'],
                        'country': location_info['country'],
                        'countryCode': location_info['countryCode'],
                        'region': location_info['region']
                    })
                
                # Calculate IPv6-only pricing
                ipv6_only_monthly = max(0, monthly_price - ipv4_primary_ip_cost) if monthly_price and ipv4_primary_ip_cost > 0 else None
                ipv6_only_hourly = ipv6_only_monthly / (24 * 30) if ipv6_only_monthly and ipv6_only_monthly > 0 else None
                
                # Create single server entry with both pricing options
                server_data = {
                    'platform': 'cloud',
                    'type': 'cloud-server',
                    'instanceType': name,
                    'vCPU': server_type.get('cores'),
                    'memoryGiB': server_type.get('memory'),
                    'diskType': server_type.get('storage_type'),
                    'diskSizeGB': server_type.get('disk'),
                    'cpuType': server_type.get('cpu_type'),
                    'architecture': server_type.get('architecture'),
                    'regions': locations,
                    'locationDetails': location_details,
                    'deprecated': server_type.get('deprecated', False),
                    'source': 'hetzner_cloud_api',
                    'description': server_type.get('description', ''),
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
                            'hasVariation': min_hourly != max_hourly if min_hourly and max_hourly else False
                        },
                        'monthly': {
                            'min': min_monthly,
                            'max': max_monthly,
                            'hasVariation': min_monthly != max_monthly if min_monthly and max_monthly else False
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
                                    'min': max(0, min_hourly - ipv4_primary_ip_cost / (24 * 30)) if min_hourly and ipv4_primary_ip_cost else None,
                                    'max': max(0, max_hourly - ipv4_primary_ip_cost / (24 * 30)) if max_hourly and ipv4_primary_ip_cost else None
                                },
                                'monthly': {
                                    'min': max(0, min_monthly - ipv4_primary_ip_cost) if min_monthly and ipv4_primary_ip_cost else None,
                                    'max': max(0, max_monthly - ipv4_primary_ip_cost) if max_monthly and ipv4_primary_ip_cost else None
                                }
                            }
                        }
                    },
                    
                    # Default network type for filtering
                    'defaultNetworkType': 'ipv4_ipv6',
                    'supportsIPv6Only': ipv6_only_monthly is not None,
                    
                    'hetzner_metadata': {
                        'platform': 'cloud',
                        'apiSource': 'cloud_api',
                        'serviceCategory': 'compute',
                        'ipv4_primary_ip_cost': ipv4_primary_ip_cost
                    },
                    'raw': server_type
                }
                
                processed_servers.append(server_data)
                
            except Exception as e:
                logger.error(f"Error processing server type {server_type.get('name')}: {e}")
        
        return processed_servers
    
    def _get_ipv4_primary_ip_cost(self, locations: List[str]) -> float:
        """Get IPv4 Primary IP cost from pricing data."""
        try:
            pricing_data = self.api.cloud_api_request('pricing')
            if not pricing_data or 'pricing' not in pricing_data:
                return 0.50  # Fallback to known IPv4 Primary IP cost
            
            # Look for IPv4 Primary IP pricing
            primary_ip_pricing = pricing_data['pricing'].get('primary_ip', [])
            for price_entry in primary_ip_pricing:
                if price_entry.get('type') == 'ipv4':
                    # Use the first location's pricing or any available pricing
                    if not locations or price_entry.get('location') in locations:
                        return float(price_entry.get('price_monthly', {}).get('net', 0.50))
            
            # Fallback to known IPv4 Primary IP cost if not found in API
            return 0.50
            
        except Exception as e:
            logger.warning(f"Could not fetch IPv4 Primary IP cost: {e}")
            return 0.50  # Fallback to known cost
    
    def _get_location_info(self, location_code: str) -> Dict[str, str]:
        """Get location information including country and flag code."""
        # Hetzner Cloud location mapping
        location_map = {
            'ash': {'country': 'United States', 'countryCode': 'US', 'city': 'Ashburn', 'region': 'Virginia'},
            'fsn1': {'country': 'Germany', 'countryCode': 'DE', 'city': 'Falkenstein', 'region': 'Saxony'},
            'hel1': {'country': 'Finland', 'countryCode': 'FI', 'city': 'Helsinki', 'region': 'Uusimaa'},
            'hil': {'country': 'Germany', 'countryCode': 'DE', 'city': 'Hildesheim', 'region': 'Lower Saxony'},
            'nbg1': {'country': 'Germany', 'countryCode': 'DE', 'city': 'Nuremberg', 'region': 'Bavaria'},
            'sin': {'country': 'Singapore', 'countryCode': 'SG', 'city': 'Singapore', 'region': 'Singapore'},
        }
        
        return location_map.get(location_code, {
            'country': 'Unknown',
            'countryCode': 'XX',
            'city': location_code,
            'region': 'Unknown'
        })
    
    def _process_load_balancer_types(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process load balancer types with pricing."""
        lb_types = data.get('load_balancer_types', [])
        
        # Get pricing data
        pricing_data = self.api.cloud_api_request('pricing')
        pricing_by_type = {}
        
        if pricing_data and 'pricing' in pricing_data:
            for pricing_entry in pricing_data['pricing'].get('load_balancer_types', []):
                pricing_by_type[pricing_entry.get('name')] = pricing_entry
        
        processed_lbs = []
        
        for lb_type in lb_types:
            try:
                name = lb_type.get('name')
                pricing_info = pricing_by_type.get(name, {})
                
                # Extract pricing
                hourly_price = None
                monthly_price = None
                locations = []
                
                if 'prices' in pricing_info:
                    for price_entry in pricing_info['prices']:
                        location = price_entry.get('location')
                        if location:
                            locations.append(location)
                        
                        if hourly_price is None and 'price_hourly' in price_entry:
                            hourly_price = float(price_entry['price_hourly'].get('net', 0))
                        
                        if monthly_price is None and 'price_monthly' in price_entry:
                            monthly_price = float(price_entry['price_monthly'].get('net', 0))
                
                lb_data = {
                    'platform': 'cloud',
                    'type': 'cloud-loadbalancer',
                    'instanceType': name,
                    'max_connections': lb_type.get('max_connections'),
                    'max_services': lb_type.get('max_services'),
                    'max_targets': lb_type.get('max_targets'),
                    'max_assigned_certificates': lb_type.get('max_assigned_certificates'),
                    'priceEUR_hourly_net': hourly_price,
                    'priceEUR_monthly_net': monthly_price,
                    'regions': locations,
                    'deprecated': lb_type.get('deprecated', False),
                    'source': 'hetzner_cloud_api',
                    'description': lb_type.get('description', ''),
                    'lastUpdated': datetime.now().isoformat(),
                    'hetzner_metadata': {
                        'platform': 'cloud',
                        'apiSource': 'cloud_api',
                        'serviceCategory': 'networking'
                    },
                    'raw': lb_type
                }
                
                processed_lbs.append(lb_data)
                
            except Exception as e:
                logger.error(f"Error processing load balancer type {lb_type.get('name')}: {e}")
        
        return processed_lbs
    
    def _process_pricing_services(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process other pricing services (volumes, floating IPs, networks, primary IPs, etc.)."""
        pricing_data = data.get('pricing', {})
        processed_services = []
        
        # Process volume pricing
        if 'volume' in pricing_data and isinstance(pricing_data['volume'], list):
            for price_entry in pricing_data['volume']:
                try:
                    location = price_entry.get('location')
                    hourly_price = float(price_entry.get('price_hourly', {}).get('net', 0))
                    monthly_price = float(price_entry.get('price_monthly', {}).get('net', 0))
                    
                    volume_data = {
                        'platform': 'cloud',
                        'type': 'cloud-volume',
                        'instanceType': 'Block Storage',
                        'unit': 'per GB/month',
                        'location': location,
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        'regions': [location] if location else [],
                        'source': 'hetzner_cloud_api',
                        'description': 'Block storage volume pricing per GB',
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'cloud',
                            'apiSource': 'cloud_api',
                            'serviceCategory': 'storage'
                        }
                    }
                    
                    processed_services.append(volume_data)
                    
                except Exception as e:
                    logger.error(f"Error processing volume pricing: {e}")
        
        # Process floating IP pricing
        if 'floating_ip' in pricing_data and isinstance(pricing_data['floating_ip'], list):
            for price_entry in pricing_data['floating_ip']:
                try:
                    location = price_entry.get('location')
                    ip_type = price_entry.get('type', 'unknown')  # Should be 'ipv4' or 'ipv6'
                    hourly_price = float(price_entry.get('price_hourly', {}).get('net', 0))
                    monthly_price = float(price_entry.get('price_monthly', {}).get('net', 0))
                    
                    ip_data = {
                        'platform': 'cloud',
                        'type': 'cloud-floating-ip',
                        'instanceType': f'Floating IP ({ip_type.upper() if ip_type != "unknown" else "IPv4/IPv6"})',
                        'ipType': ip_type if ip_type != 'unknown' else 'ipv4_ipv6',  # Add IP type as filterable field
                        'location': location,
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        'regions': [location] if location else [],
                        'source': 'hetzner_cloud_api',
                        'description': f'Floating {ip_type.upper() if ip_type != "unknown" else "IP"} address pricing',
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'cloud',
                            'apiSource': 'cloud_api',
                            'serviceCategory': 'networking',
                            'ipVersion': ip_type if ip_type != 'unknown' else 'mixed'
                        }
                    }
                    
                    processed_services.append(ip_data)
                    
                except Exception as e:
                    logger.error(f"Error processing floating IP pricing: {e}")
        
        # Process private network pricing
        if 'network' in pricing_data and isinstance(pricing_data['network'], list):
            for price_entry in pricing_data['network']:
                try:
                    location = price_entry.get('location')
                    hourly_price = float(price_entry.get('price_hourly', {}).get('net', 0))
                    monthly_price = float(price_entry.get('price_monthly', {}).get('net', 0))
                    
                    network_data = {
                        'platform': 'cloud',
                        'type': 'cloud-private-network',
                        'instanceType': 'Private Network',
                        'location': location,
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        'regions': [location] if location else [],
                        'source': 'hetzner_cloud_api',
                        'description': 'Private network pricing',
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'cloud',
                            'apiSource': 'cloud_api',
                            'serviceCategory': 'networking'
                        }
                    }
                    
                    processed_services.append(network_data)
                    
                except Exception as e:
                    logger.error(f"Error processing network pricing: {e}")
        
        # Process primary IP pricing
        if 'primary_ip' in pricing_data and isinstance(pricing_data['primary_ip'], list):
            for price_entry in pricing_data['primary_ip']:
                try:
                    location = price_entry.get('location')
                    ip_type = price_entry.get('type', 'unknown')  # Should be 'ipv4' or 'ipv6'
                    hourly_price = float(price_entry.get('price_hourly', {}).get('net', 0))
                    monthly_price = float(price_entry.get('price_monthly', {}).get('net', 0))
                    
                    ip_data = {
                        'platform': 'cloud',
                        'type': 'cloud-primary-ip',
                        'instanceType': f'Primary IP ({ip_type.upper()})',
                        'ipType': ip_type,  # Add IP type as filterable field
                        'location': location,
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        'regions': [location] if location else [],
                        'source': 'hetzner_cloud_api',
                        'description': f'Primary {ip_type.upper()} address pricing',
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'cloud',
                            'apiSource': 'cloud_api',
                            'serviceCategory': 'networking',
                            'ipVersion': ip_type
                        }
                    }
                    
                    processed_services.append(ip_data)
                    
                except Exception as e:
                    logger.error(f"Error processing primary IP pricing: {e}")
        
        return processed_services

class HetznerDedicatedCollector:
    """Collector for Hetzner Dedicated Server services."""
    
    def __init__(self, api_client: HetznerAPIClient):
        self.api = api_client
    
    def collect_all_dedicated_services(self) -> List[Dict[str, Any]]:
        """Collect all dedicated server services data."""
        logger.info("🖥️  Collecting Hetzner Dedicated services...")
        
        all_services = []
        
        # Try Robot API first if credentials are available
        if config.robot_user and config.robot_password:
            logger.info("Using Robot API for dedicated servers...")
            try:
                robot_servers = self._collect_robot_api_servers()
                all_services.extend(robot_servers)
            except Exception as e:
                logger.error(f"Failed to collect Robot API data: {e}")
        else:
            logger.info("Robot API credentials not provided - skipping dedicated servers")
            logger.info("To enable dedicated servers, set HETZNER_ROBOT_USER and HETZNER_ROBOT_PASSWORD")
        
        logger.info(f"Collected {len(all_services)} dedicated services")
        return all_services
    
    def _collect_robot_api_servers(self) -> List[Dict[str, Any]]:
        """Collect dedicated servers via Robot API."""
        try:
            # Robot API endpoints for server information
            logger.info("Fetching products from Robot API...")
            products_data = self.api.robot_api_request('order/server/product')
            
            if not products_data:
                logger.warning("No data from Robot API")
                return []
            
            processed_servers = []
            products = products_data.get('product', [])
            
            logger.info(f"Processing {len(products)} products from Robot API...")
            
            for product in products:
                try:
                    price_data = product.get('price', {})
                    price_net = float(price_data.get('net', 0)) if price_data else 0
                    
                    server_data = {
                        'platform': 'dedicated',
                        'type': 'dedicated-server',
                        'instanceType': product.get('name', ''),
                        'description': product.get('description', ''),
                        'priceEUR_monthly_net': price_net,
                        'priceEUR_hourly_net': price_net / (24 * 30) if price_net > 0 else 0,  # Approximate
                        'regions': ['Germany'],  # Hetzner dedicated servers are in Germany
                        'source': 'hetzner_robot_api',
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'dedicated',
                            'apiSource': 'robot_api',
                            'serviceCategory': 'dedicated_compute'
                        },
                        'raw': product
                    }
                    
                    processed_servers.append(server_data)
                    
                except Exception as e:
                    logger.error(f"Error processing Robot API product: {e}")
            
            logger.info(f"Successfully processed {len(processed_servers)} dedicated servers")
            return processed_servers
            
        except Exception as e:
            logger.error(f"Error collecting Robot API servers: {e}")
            return []

class HetznerDataCollector:
    """Main collector orchestrating both cloud and dedicated services."""
    
    def __init__(self):
        self.api_client = HetznerAPIClient()
        self.cloud_collector = HetznerCloudCollector(self.api_client)
        self.dedicated_collector = HetznerDedicatedCollector(self.api_client)
    
    def collect_all_hetzner_data(self) -> List[Dict[str, Any]]:
        """Collect all Hetzner data from both platforms."""
        logger.info("🚀 Starting complete Hetzner data collection...")
        
        all_data = []
        
        # Collect cloud services
        if config.enable_cloud:
            try:
                cloud_data = self.cloud_collector.collect_all_cloud_services()
                all_data.extend(cloud_data)
                logger.info(f"✅ Cloud services: {len(cloud_data)} items")
            except Exception as e:
                logger.error(f"Cloud collection failed: {e}")
        else:
            logger.info("🔇 Cloud services disabled")
        
        # Collect dedicated services
        if config.enable_dedicated:
            try:
                dedicated_data = self.dedicated_collector.collect_all_dedicated_services()
                all_data.extend(dedicated_data)
                logger.info(f"✅ Dedicated services: {len(dedicated_data)} items")
            except Exception as e:
                logger.error(f"Dedicated collection failed: {e}")
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
    print("=== Hetzner Complete Data Fetcher - Refactored ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    # Show configuration
    print(f"\n🔧 Configuration:")
    print(f"  Cloud API: {'✅ Enabled' if config.enable_cloud else '❌ Disabled'}")
    print(f"  Dedicated: {'✅ Enabled' if config.enable_dedicated else '❌ Disabled'}")
    print(f"  Auction: {'✅ Enabled' if config.enable_auction else '❌ Disabled'}")
    
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