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
                    data = response.json()
                    self._set_cache(cache_key, data)
                    return data
                elif response.status_code == 401:
                    logger.error(f"Authentication failed for {url}")
                    return None
                elif response.status_code == 429:
                    wait_time = 2 ** attempt
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"HTTP {response.status_code} for {url}")
                    
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
            logger.warning("Robot API credentials not provided")
            return None
        
        url = f"{config.robot_api_url}/{endpoint.lstrip('/')}"
        headers = {'Content-Type': 'application/json'}
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
                
                server_data = {
                    'platform': 'cloud',
                    'type': 'cloud-server',
                    'instanceType': name,
                    'vCPU': server_type.get('cores'),
                    'memoryGiB': server_type.get('memory'),
                    'diskType': server_type.get('storage_type'),
                    'diskSizeGB': server_type.get('disk'),
                    'priceEUR_hourly_net': hourly_price,
                    'priceEUR_monthly_net': monthly_price,
                    'cpuType': server_type.get('cpu_type'),
                    'architecture': server_type.get('architecture'),
                    'regions': locations,
                    'deprecated': server_type.get('deprecated', False),
                    'source': 'hetzner_cloud_api',
                    'description': server_type.get('description', ''),
                    'lastUpdated': datetime.now().isoformat(),
                    'hetzner_metadata': {
                        'platform': 'cloud',
                        'apiSource': 'cloud_api',
                        'serviceCategory': 'compute'
                    },
                    'raw': server_type
                }
                
                processed_servers.append(server_data)
                
            except Exception as e:
                logger.error(f"Error processing server type {server_type.get('name')}: {e}")
        
        return processed_servers
    
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
        """Process other pricing services (volumes, floating IPs, etc.)."""
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
                    hourly_price = float(price_entry.get('price_hourly', {}).get('net', 0))
                    monthly_price = float(price_entry.get('price_monthly', {}).get('net', 0))
                    
                    ip_data = {
                        'platform': 'cloud',
                        'type': 'cloud-floating-ip',
                        'instanceType': 'Floating IP',
                        'location': location,
                        'priceEUR_hourly_net': hourly_price,
                        'priceEUR_monthly_net': monthly_price,
                        'regions': [location] if location else [],
                        'source': 'hetzner_cloud_api',
                        'description': 'Floating IP pricing',
                        'lastUpdated': datetime.now().isoformat(),
                        'hetzner_metadata': {
                            'platform': 'cloud',
                            'apiSource': 'cloud_api',
                            'serviceCategory': 'networking'
                        }
                    }
                    
                    processed_services.append(ip_data)
                    
                except Exception as e:
                    logger.error(f"Error processing floating IP pricing: {e}")
        
        return processed_services

class HetznerDedicatedCollector:
    """Collector for Hetzner Dedicated Server services."""
    
    def __init__(self, api_client: HetznerAPIClient):
        self.api = api_client
    
    def collect_all_dedicated_services(self) -> List[Dict[str, Any]]:
        """Collect all dedicated server services data."""
        logger.info("🖥️  Collecting Hetzner Dedicated services...")
        
        all_services = []
        
        # Try Robot API first
        if config.robot_user and config.robot_password:
            logger.info("Using Robot API for dedicated servers...")
            robot_servers = self._collect_robot_api_servers()
            all_services.extend(robot_servers)
        else:
            logger.info("Robot API credentials not provided - skipping Robot API")
        
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