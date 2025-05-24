#!/usr/bin/env python3
"""
Hetzner Cloud API data fetcher - Cloud services only.
Fast, reliable, comprehensive cloud data collection via API.
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime

# Hetzner Cloud API Configuration
API_BASE_URL = "https://api.hetzner.cloud/v1"
API_RATE_LIMIT_DELAY = 0.1  # 100ms between API calls
API_RETRY_COUNT = 3
API_RETRY_DELAY = 1.0  # Initial retry delay
API_TIMEOUT = 15
CACHE_DURATION = 300  # 5 minutes cache for development

# Cloud API Configuration
HETZNER_API_TOKEN = os.environ.get("HETZNER_API_TOKEN")

# Simple in-memory cache
_api_cache = {}

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Base headers for all requests
BASE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# --- Utility Functions ---

def get_from_cache(key):
    """Get data from cache if still valid."""
    if key in _api_cache:
        data, timestamp = _api_cache[key]
        if time.time() - timestamp < CACHE_DURATION:
            return data
    return None

def set_cache(key, data):
    """Set data in cache with timestamp."""
    _api_cache[key] = (data, time.time())

def rate_limit_delay():
    """Apply rate limiting delay."""
    time.sleep(API_RATE_LIMIT_DELAY)

def validate_api_response(data, expected_keys=None):
    """Validate API response data."""
    if not isinstance(data, (dict, list)):
        return False
    
    if expected_keys and isinstance(data, dict):
        return all(key in data for key in expected_keys)
    
    return True

# --- API Client Functions ---

def get_api_headers():
    headers = BASE_HEADERS.copy()
    if HETZNER_API_TOKEN:
        headers['Authorization'] = f'Bearer {HETZNER_API_TOKEN}'
    return headers

def make_api_request(endpoint, params=None, retry_count=API_RETRY_COUNT):
    """
    Make Cloud API request with enhanced error handling, retries, and caching.
    """
    url = f"{API_BASE_URL}/{endpoint.lstrip('/')}"
    cache_key = f"cloud_{url}_{str(params or {})}"
    
    # Check cache first
    cached_data = get_from_cache(cache_key)
    if cached_data is not None:
        return cached_data
    
    headers = get_api_headers()
    
    for attempt in range(retry_count + 1):
        try:
            # Apply rate limiting
            if attempt > 0:
                rate_limit_delay()
            
            logger.debug(f"Cloud API request attempt {attempt + 1}/{retry_count + 1}: {url}")
            
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=API_TIMEOUT
            )
            
            # Handle different HTTP status codes
            if response.status_code == 200:
                data = response.json()
                if validate_api_response(data):
                    set_cache(cache_key, data)
                    return data
            elif response.status_code == 401:
                logger.error("Cloud API authentication failed (401). Check HETZNER_API_TOKEN.")
                return None
            elif response.status_code == 403:
                logger.error("Cloud API access forbidden (403). Check your token permissions.")
                return None
            elif response.status_code == 404:
                logger.warning(f"Cloud API endpoint not found (404): {url}")
                return None
            elif response.status_code == 429:
                # Rate limited - wait longer
                wait_time = API_RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Cloud API rate limited (429). Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                continue
            elif 500 <= response.status_code < 600:
                # Server error - retry
                logger.warning(f"Cloud API server error ({response.status_code}). Retrying...")
                if attempt < retry_count:
                    time.sleep(API_RETRY_DELAY * (2 ** attempt))
                    continue
            else:
                logger.error(f"Unexpected Cloud API response status: {response.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"Cloud API request timeout on attempt {attempt + 1}")
            if attempt < retry_count:
                time.sleep(API_RETRY_DELAY * (2 ** attempt))
                continue
        except requests.exceptions.ConnectionError:
            logger.warning(f"Cloud API connection error on attempt {attempt + 1}")
            if attempt < retry_count:
                time.sleep(API_RETRY_DELAY * (2 ** attempt))
                continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Cloud API request failed: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error during Cloud API request: {e}")
            break
    
    logger.error(f"Cloud API request failed after {retry_count + 1} attempts: {url}")
    return None


# --- Cloud API Functions ---

def fetch_server_types():
    """Fetch server types from Hetzner Cloud API."""
    logger.info("Fetching server types from Cloud API...")
    
    data = make_api_request("server_types")
    if data and "server_types" in data:
        server_types = data["server_types"]
        logger.info(f"Successfully fetched {len(server_types)} server types")
        return server_types
    
    logger.warning("Failed to fetch server types from Cloud API")
    return []

def fetch_pricing_data():
    """Fetch pricing data from Hetzner Cloud API."""
    logger.info("Fetching pricing data from Cloud API...")
    
    data = make_api_request("pricing")
    if data and "pricing" in data:
        logger.info("Successfully fetched pricing data")
        return data["pricing"]
    
    logger.warning("Failed to fetch pricing data from Cloud API")
    return {}

def fetch_load_balancer_types():
    """Fetch load balancer types from Hetzner Cloud API."""
    logger.info("Fetching load balancer types from Cloud API...")
    
    data = make_api_request("load_balancer_types")
    if data and "load_balancer_types" in data:
        lb_types = data["load_balancer_types"]
        logger.info(f"Successfully fetched {len(lb_types)} load balancer types")
        return lb_types
    
    logger.warning("Failed to fetch load balancer types from Cloud API")
    return []

def fetch_volume_types():
    """Extract volume pricing from pricing data."""
    pricing_data = fetch_pricing_data()
    if pricing_data and "volume" in pricing_data:
        logger.info("Successfully extracted volume pricing")
        return pricing_data["volume"]
    
    logger.warning("Failed to extract volume pricing")
    return []

def fetch_floating_ip_pricing():
    """Extract floating IP pricing from pricing data."""
    pricing_data = fetch_pricing_data()
    if pricing_data and "floating_ip" in pricing_data:
        logger.info("Successfully extracted floating IP pricing")
        return pricing_data["floating_ip"]
    
    logger.warning("Failed to extract floating IP pricing")
    return []

def fetch_network_pricing():
    """Extract network pricing from pricing data."""
    pricing_data = fetch_pricing_data()
    if pricing_data and "network" in pricing_data:
        logger.info("Successfully extracted network pricing")
        return pricing_data["network"]
    
    logger.warning("Failed to extract network pricing")
    return []

# --- Data Processing Functions ---

def process_server_types_with_pricing(server_types, pricing_data):
    """Process server types with enhanced pricing information."""
    logger.info("Processing server types with pricing data...")
    
    processed_servers = []
    pricing_by_type = {}
    
    # Build pricing lookup
    if pricing_data and "server_types" in pricing_data:
        for pricing_entry in pricing_data["server_types"]:
            pricing_by_type[pricing_entry.get("name")] = pricing_entry
    
    for server_type in server_types:
        try:
            name = server_type.get("name")
            pricing_info = pricing_by_type.get(name, {})
            
            # Extract pricing by location
            locations = []
            hourly_price = None
            monthly_price = None
            
            if "prices" in pricing_info:
                for price_entry in pricing_info["prices"]:
                    location = price_entry.get("location")
                    if location:
                        locations.append(location)
                    
                    # Get primary pricing (first location)
                    if hourly_price is None and "price_hourly" in price_entry:
                        hourly_price = float(price_entry["price_hourly"].get("net", 0))
                    
                    if monthly_price is None and "price_monthly" in price_entry:
                        monthly_price = float(price_entry["price_monthly"].get("net", 0))
            
            server_data = {
                "type": "cloud-server",
                "instanceType": name,
                "vCPU": server_type.get("cores"),
                "memoryGiB": server_type.get("memory"),
                "diskType": server_type.get("storage_type"),
                "diskSizeGB": server_type.get("disk"),
                "priceEUR_hourly_net": hourly_price,
                "priceEUR_monthly_net": monthly_price,
                "trafficOutTB": server_type.get("included_traffic", 0) / (1024**4) if server_type.get("included_traffic") else None,
                "cpuType": server_type.get("cpu_type"),
                "architecture": server_type.get("architecture"),
                "locations": locations,
                "deprecated": server_type.get("deprecated", False),
                "source": "cloud_api_enhanced",
                "description": server_type.get("description", ""),
                "api_id": server_type.get("id"),
                "created": server_type.get("created"),
                "raw_server_type": server_type
            }
            
            processed_servers.append(server_data)
            
        except Exception as e:
            logger.error(f"Error processing server type {server_type.get('name', 'unknown')}: {e}")
            continue
    
    logger.info(f"Processed {len(processed_servers)} server types")
    return processed_servers

def process_load_balancer_types_with_pricing(lb_types, pricing_data):
    """Process load balancer types with pricing information."""
    logger.info("Processing load balancer types with pricing data...")
    
    processed_lbs = []
    pricing_by_type = {}
    
    # Build pricing lookup
    if pricing_data and "load_balancer_types" in pricing_data:
        for pricing_entry in pricing_data["load_balancer_types"]:
            pricing_by_type[pricing_entry.get("name")] = pricing_entry
    
    for lb_type in lb_types:
        try:
            name = lb_type.get("name")
            pricing_info = pricing_by_type.get(name, {})
            
            # Extract pricing
            hourly_price = None
            monthly_price = None
            locations = []
            
            if "prices" in pricing_info:
                for price_entry in pricing_info["prices"]:
                    location = price_entry.get("location")
                    if location:
                        locations.append(location)
                    
                    if hourly_price is None and "price_hourly" in price_entry:
                        hourly_price = float(price_entry["price_hourly"].get("net", 0))
                    
                    if monthly_price is None and "price_monthly" in price_entry:
                        monthly_price = float(price_entry["price_monthly"].get("net", 0))
            
            lb_data = {
                "type": "cloud-loadbalancer",
                "instanceType": name,
                "max_connections": lb_type.get("max_connections"),
                "max_services": lb_type.get("max_services"),
                "max_targets": lb_type.get("max_targets"),
                "max_assigned_certificates": lb_type.get("max_assigned_certificates"),
                "priceEUR_hourly_net": hourly_price,
                "priceEUR_monthly_net": monthly_price,
                "locations": locations,
                "deprecated": lb_type.get("deprecated", False),
                "source": "cloud_api_enhanced",
                "description": lb_type.get("description", ""),
                "api_id": lb_type.get("id"),
                "raw_lb_type": lb_type
            }
            
            processed_lbs.append(lb_data)
            
        except Exception as e:
            logger.error(f"Error processing load balancer type {lb_type.get('name', 'unknown')}: {e}")
            continue
    
    logger.info(f"Processed {len(processed_lbs)} load balancer types")
    return processed_lbs

def process_volume_pricing(volume_pricing):
    """Process volume pricing information."""
    logger.info("Processing volume pricing data...")
    
    processed_volumes = []
    
    for price_entry in volume_pricing:
        try:
            location = price_entry.get("location")
            hourly_price = float(price_entry.get("price_hourly", {}).get("net", 0))
            monthly_price = float(price_entry.get("price_monthly", {}).get("net", 0))
            
            volume_data = {
                "type": "cloud-volume",
                "instanceType": "Block Storage",
                "unit": "per GB/month",
                "location": location,
                "priceEUR_hourly_net": hourly_price,
                "priceEUR_monthly_net": monthly_price,
                "source": "cloud_api_enhanced",
                "description": "Block storage volume pricing per GB"
            }
            
            processed_volumes.append(volume_data)
            
        except Exception as e:
            logger.error(f"Error processing volume pricing: {e}")
            continue
    
    logger.info(f"Processed {len(processed_volumes)} volume pricing entries")
    return processed_volumes

def process_floating_ip_pricing(floating_ip_pricing):
    """Process floating IP pricing information."""
    logger.info("Processing floating IP pricing data...")
    
    processed_ips = []
    
    for price_entry in floating_ip_pricing:
        try:
            location = price_entry.get("location")
            hourly_price = float(price_entry.get("price_hourly", {}).get("net", 0))
            monthly_price = float(price_entry.get("price_monthly", {}).get("net", 0))
            
            ip_data = {
                "type": "cloud-floating-ip",
                "instanceType": "Floating IP",
                "location": location,
                "priceEUR_hourly_net": hourly_price,
                "priceEUR_monthly_net": monthly_price,
                "source": "cloud_api_enhanced",
                "description": "Floating IP pricing"
            }
            
            processed_ips.append(ip_data)
            
        except Exception as e:
            logger.error(f"Error processing floating IP pricing: {e}")
            continue
    
    logger.info(f"Processed {len(processed_ips)} floating IP pricing entries")
    return processed_ips

def process_network_pricing(network_pricing):
    """Process network pricing information."""
    logger.info("Processing network pricing data...")
    
    processed_networks = []
    
    for price_entry in network_pricing:
        try:
            location = price_entry.get("location")
            hourly_price = float(price_entry.get("price_hourly", {}).get("net", 0))
            monthly_price = float(price_entry.get("price_monthly", {}).get("net", 0))
            
            network_data = {
                "type": "cloud-network",
                "instanceType": "Private Network",
                "location": location,
                "priceEUR_hourly_net": hourly_price,
                "priceEUR_monthly_net": monthly_price,
                "source": "cloud_api_enhanced",
                "description": "Private network pricing"
            }
            
            processed_networks.append(network_data)
            
        except Exception as e:
            logger.error(f"Error processing network pricing: {e}")
            continue
    
    logger.info(f"Processed {len(processed_networks)} network pricing entries")
    return processed_networks


# --- Main Fetch Functions ---

def fetch_hetzner_cloud():
    """Enhanced cloud data fetching via API only."""
    logger.info("Starting Hetzner Cloud data fetch via API...")
    cloud_data = []
    
    if not HETZNER_API_TOKEN:
        logger.error("Hetzner Cloud API token required for cloud data")
        logger.error("Please set HETZNER_API_TOKEN environment variable")
        return []
    
    logger.info("API token provided - fetching comprehensive cloud data...")
    
    try:
        # Fetch all cloud service data
        server_types = fetch_server_types()
        pricing_data = fetch_pricing_data()
        lb_types = fetch_load_balancer_types()
        volume_pricing = fetch_volume_types()
        floating_ip_pricing = fetch_floating_ip_pricing()
        network_pricing = fetch_network_pricing()
        
        # Process server types with pricing
        if server_types:
            servers = process_server_types_with_pricing(server_types, pricing_data)
            cloud_data.extend(servers)
            logger.info(f"Added {len(servers)} server types to cloud data")
        
        # Process load balancers
        if lb_types:
            load_balancers = process_load_balancer_types_with_pricing(lb_types, pricing_data)
            cloud_data.extend(load_balancers)
            logger.info(f"Added {len(load_balancers)} load balancer types to cloud data")
        
        # Process volume pricing
        if volume_pricing:
            volumes = process_volume_pricing(volume_pricing)
            cloud_data.extend(volumes)
            logger.info(f"Added {len(volumes)} volume pricing entries to cloud data")
        
        # Process floating IP pricing
        if floating_ip_pricing:
            floating_ips = process_floating_ip_pricing(floating_ip_pricing)
            cloud_data.extend(floating_ips)
            logger.info(f"Added {len(floating_ips)} floating IP pricing entries to cloud data")
        
        # Process network pricing
        if network_pricing:
            networks = process_network_pricing(network_pricing)
            cloud_data.extend(networks)
            logger.info(f"Added {len(networks)} network pricing entries to cloud data")
        
        if cloud_data:
            logger.info(f"Successfully fetched {len(cloud_data)} total cloud service entries via API")
            return cloud_data
        else:
            logger.warning("API fetch completed but no data was collected")
            return []
            
    except Exception as e:
        logger.error(f"API fetch failed: {e}")
        return []

def fetch_hetzner_dedicated():
    """Fetch Hetzner dedicated servers - DISABLED for now (Cloud API only)."""
    logger.info("Dedicated server data collection is currently disabled")
    logger.info("Only collecting Hetzner Cloud data for now")
    return []

def main():
    """Main function to fetch all Hetzner data."""
    print("=== Enhanced Hetzner Data Fetcher (API-Only) ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    all_data = []
    
    try:
        # Fetch cloud data
        print("\n--- Fetching Hetzner Cloud Data ---")
        cloud_data = fetch_hetzner_cloud()
        if cloud_data:
            all_data.extend(cloud_data)
            print(f"✅ Cloud: {len(cloud_data)} entries")
        else:
            print("❌ Cloud: No data collected")
        
        # Fetch dedicated server data
        print("\n--- Fetching Hetzner Dedicated Server Data ---")
        dedicated_data = fetch_hetzner_dedicated()
        if dedicated_data:
            all_data.extend(dedicated_data)
            print(f"✅ Dedicated: {len(dedicated_data)} entries")
        else:
            print("❌ Dedicated: No data collected")
        
        # Save results
        if all_data:
            output_file = "data/hetzner.json"
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(all_data, f, indent=2, ensure_ascii=False)
            
            print(f"\n✅ SUCCESS: Saved {len(all_data)} total entries to {output_file}")
            
            # Summary by type
            type_counts = {}
            for item in all_data:
                item_type = item.get("type", "unknown")
                type_counts[item_type] = type_counts.get(item_type, 0) + 1
            
            print("\n--- Data Summary ---")
            for item_type, count in type_counts.items():
                print(f"{item_type}: {count} entries")
        else:
            print("\n❌ No data was collected from either API")
            
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        return False
    
    print("\n--- Hetzner Cloud API Configuration ---")
    print("This script uses Hetzner Cloud API exclusively for reliable, fast data collection.")
    print("Dedicated server data collection is currently DISABLED.")
    print("")
    print("Required Environment Variable:")
    print("1. HETZNER_API_TOKEN - Hetzner Cloud API token (from console.hetzner.cloud)")
    print("   - Get from: Security → API Tokens")
    print("   - Permissions: 'Read' is sufficient")
    print("")
    print("Currently Collecting:")
    print("- ✅ Cloud Servers (all instance types)")
    print("- ✅ Load Balancers (all types)")
    print("- ✅ Block Storage (volume pricing)")
    print("- ✅ Floating IPs (all locations)")
    print("- ✅ Private Networks (all locations)")
    print("")
    print("Benefits:")
    print("- ✅ No Chrome/Selenium dependencies (faster CI)")
    print("- ✅ More reliable data (direct from Hetzner Cloud API)")
    print("- ✅ Comprehensive cloud service coverage")
    print("- ✅ Fast execution (~1-2 minutes)")
    print("- ✅ Location-specific pricing")
    
    return len(all_data) > 0

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)