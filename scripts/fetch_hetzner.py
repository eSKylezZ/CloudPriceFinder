# scripts/fetch_hetzner.py
"""
Fetch Hetzner Cloud and Dedicated Server pricing/specs with IPv6 toggle and comprehensive pagination.

- Cloud API docs: https://docs.hetzner.cloud/
- Cloud pricing page (for scraping fallback): https://www.hetzner.com/cloud
- Dedicated server pricing page (for scraping): https://www.hetzner.com/dedicated-rootserver
  (Note: This page is paginated and has IPv6-only pricing toggle)

Cloud Data Fetching Strategy:
1. Try API if HETZNER_API_TOKEN is set.
2. If no token OR API fails, try scraping the cloud pricing page.

Dedicated Data Fetching Strategy:
- Uses Selenium WebDriver to scrape the dedicated server pricing page with JavaScript support.
- Handles IPv6 toggle to collect both IPv4 and IPv6 pricing data.
- Implements comprehensive pagination detection and navigation.
- Works with default 10 items per page, optimized for fast pagination through ~39 pages for 386 servers.
- Waits for JavaScript content to load before extracting data to handle React components.

Enhanced API Integration Features:
- Comprehensive error handling and retry logic
- Request rate limiting and throttling
- Response caching for development efficiency
- Additional cloud services (Load Balancers, Block Storage, Floating IPs, Networks)
- Data validation and consistent field naming
- Improved logging and monitoring

Requires: requests, beautifulsoup4, selenium, webdriver-manager
"""

import json
import os
import re
import requests
import time
import logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# --- Configuration ---
HETZNER_API_TOKEN = os.environ.get("HETZNER_API_TOKEN")
BASE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Enhanced API Configuration
API_BASE_URL = "https://api.hetzner.cloud/v1"
ROBOT_API_BASE_URL = "https://robot-ws.your-server.de"
API_RATE_LIMIT_DELAY = 0.1  # 100ms between API calls
API_RETRY_COUNT = 3
API_RETRY_DELAY = 1.0  # Initial retry delay
API_TIMEOUT = 15
CACHE_DURATION = 300  # 5 minutes cache for development

# Robot API Configuration
HETZNER_ROBOT_USER = os.environ.get("HETZNER_ROBOT_USER")
HETZNER_ROBOT_PASSWORD = os.environ.get("HETZNER_ROBOT_PASSWORD")

# Simple in-memory cache
_api_cache = {}

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# --- End Configuration ---

def rate_limit_delay():
    """Apply rate limiting between API calls."""
    time.sleep(API_RATE_LIMIT_DELAY)

def get_from_cache(cache_key):
    """Get data from cache if not expired."""
    if cache_key in _api_cache:
        cached_data, timestamp = _api_cache[cache_key]
        if datetime.now() - timestamp < timedelta(seconds=CACHE_DURATION):
            logger.debug(f"Cache hit for key: {cache_key}")
            return cached_data
        else:
            # Remove expired cache entry
            del _api_cache[cache_key]
            logger.debug(f"Cache expired for key: {cache_key}")
    return None

def set_cache(cache_key, data):
    """Store data in cache with timestamp."""
    _api_cache[cache_key] = (data, datetime.now())
    logger.debug(f"Cached data for key: {cache_key}")

def make_api_request(endpoint, params=None, retry_count=API_RETRY_COUNT):
    """
    Make API request with enhanced error handling, retries, and caching.
    
    Args:
        endpoint (str): API endpoint (without base URL)
        params (dict): Query parameters
        retry_count (int): Number of retry attempts
    
    Returns:
        dict: API response data or None if failed
    """
    url = f"{API_BASE_URL}/{endpoint.lstrip('/')}"
    cache_key = f"{url}_{str(params or {})}"
    
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
            
            logger.debug(f"API request attempt {attempt + 1}/{retry_count + 1}: {url}")
            
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=API_TIMEOUT
            )
            
            # Handle different HTTP status codes
            if response.status_code == 200:
                data = response.json()
                set_cache(cache_key, data)
                return data
            elif response.status_code == 401:
                logger.error("API authentication failed (401). Check your HETZNER_API_TOKEN.")
                return None
            elif response.status_code == 403:
                logger.error("API access forbidden (403). Check your API token permissions.")
                return None
            elif response.status_code == 404:
                logger.warning(f"API endpoint not found (404): {url}")
                return None
            elif response.status_code == 429:
                # Rate limited - wait longer
                wait_time = API_RETRY_DELAY * (2 ** attempt)
                logger.warning(f"API rate limited (429). Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                continue
            elif 500 <= response.status_code < 600:
                # Server error - retry
                logger.warning(f"API server error ({response.status_code}). Retrying...")
                if attempt < retry_count:
                    time.sleep(API_RETRY_DELAY * (2 ** attempt))
                    continue
            else:
                logger.error(f"Unexpected API response status: {response.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"API request timeout on attempt {attempt + 1}")
            if attempt < retry_count:
                time.sleep(API_RETRY_DELAY * (2 ** attempt))
                continue
        except requests.exceptions.ConnectionError:
            logger.warning(f"API connection error on attempt {attempt + 1}")
            if attempt < retry_count:
                time.sleep(API_RETRY_DELAY * (2 ** attempt))
                continue
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error during API request: {e}")
            break
    
    logger.error(f"API request failed after {retry_count + 1} attempts: {url}")
    return None

def validate_api_response(data, expected_fields):
    """
    Validate API response data structure.
    
    Args:
        data (dict): API response data
        expected_fields (list): List of expected field names
    
    Returns:
        bool: True if valid, False otherwise
    """
    if not isinstance(data, dict):
        logger.warning("API response is not a dictionary")
        return False
    
    for field in expected_fields:
        if field not in data:
            logger.warning(f"Missing expected field in API response: {field}")
            return False
    
    return True

def get_api_headers():
    headers = BASE_HEADERS.copy()
    if HETZNER_API_TOKEN:
        headers['Authorization'] = f'Bearer {HETZNER_API_TOKEN}'
    return headers

def get_robot_api_auth():
    """Get Robot API authentication tuple."""
    if HETZNER_ROBOT_USER and HETZNER_ROBOT_PASSWORD:
        return (HETZNER_ROBOT_USER, HETZNER_ROBOT_PASSWORD)
    return None

def make_robot_api_request(endpoint, params=None, retry_count=API_RETRY_COUNT):
    """
    Make Robot API request with enhanced error handling, retries, and caching.
    
    Args:
        endpoint (str): Robot API endpoint (without base URL)
        params (dict): Query parameters
        retry_count (int): Number of retry attempts
    
    Returns:
        dict: API response data or None if failed
    """
    url = f"{ROBOT_API_BASE_URL}/{endpoint.lstrip('/')}"
    cache_key = f"robot_{url}_{str(params or {})}"
    
    # Check cache first
    cached_data = get_from_cache(cache_key)
    if cached_data is not None:
        return cached_data
    
    auth = get_robot_api_auth()
    if not auth:
        logger.warning("Robot API credentials not provided (HETZNER_ROBOT_USER/HETZNER_ROBOT_PASSWORD)")
        return None
    
    headers = BASE_HEADERS.copy()
    
    for attempt in range(retry_count + 1):
        try:
            # Apply rate limiting
            if attempt > 0:
                rate_limit_delay()
            
            logger.debug(f"Robot API request attempt {attempt + 1}/{retry_count + 1}: {url}")
            
            response = requests.get(
                url,
                auth=auth,
                headers=headers,
                params=params,
                timeout=API_TIMEOUT
            )
            
            # Handle different HTTP status codes
            if response.status_code == 200:
                data = response.json()
                set_cache(cache_key, data)
                return data
            elif response.status_code == 401:
                logger.error("Robot API authentication failed (401). Check HETZNER_ROBOT_USER/HETZNER_ROBOT_PASSWORD.")
                return None
            elif response.status_code == 403:
                logger.error("Robot API access forbidden (403). Check your credentials permissions.")
                return None
            elif response.status_code == 404:
                logger.warning(f"Robot API endpoint not found (404): {url}")
                return None
            elif response.status_code == 429:
                # Rate limited - wait longer
                wait_time = API_RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Robot API rate limited (429). Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                continue
            elif 500 <= response.status_code < 600:
                # Server error - retry
                logger.warning(f"Robot API server error ({response.status_code}). Retrying...")
                if attempt < retry_count:
                    time.sleep(API_RETRY_DELAY * (2 ** attempt))
                    continue
            else:
                logger.error(f"Unexpected Robot API response status: {response.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"Robot API request timeout on attempt {attempt + 1}")
            if attempt < retry_count:
                time.sleep(API_RETRY_DELAY * (2 ** attempt))
                continue
        except requests.exceptions.ConnectionError:
            logger.warning(f"Robot API connection error on attempt {attempt + 1}")
            if attempt < retry_count:
                time.sleep(API_RETRY_DELAY * (2 ** attempt))
                continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Robot API request failed: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error during Robot API request: {e}")
            break
    
    logger.error(f"Robot API request failed after {retry_count + 1} attempts: {url}")
    return None

def fetch_dedicated_server_products():
    """Fetch dedicated server products from Hetzner Robot API."""
    logger.info("Fetching dedicated server products from Robot API...")
    
    data = make_robot_api_request("order/server/product")
    if data and isinstance(data, list):
        logger.info(f"Successfully fetched {len(data)} dedicated server products")
        return data
    elif data and isinstance(data, dict) and "product" in data:
        products = data["product"]
        logger.info(f"Successfully fetched {len(products)} dedicated server products")
        return products
    
    logger.warning("Failed to fetch dedicated server products from Robot API")
    return []

def process_dedicated_server_products(products):
    """
    Process dedicated server products from Robot API.
    
    Args:
        products (list): List of server product data from Robot API
    
    Returns:
        list: Processed dedicated server data
    """
    logger.info("Processing dedicated server products from Robot API...")
    
    processed_servers = []
    
    for product in products:
        try:
            # Extract server specifications
            name = product.get("name", "Unknown Server")
            description = product.get("description", "")
            
            # Extract pricing information
            prices = product.get("price", [])
            monthly_price = None
            setup_fee = None
            
            # Find EUR pricing
            for price_entry in prices:
                if price_entry.get("currency") == "EUR":
                    monthly_price = float(price_entry.get("price_monthly", {}).get("net", 0))
                    setup_fee = float(price_entry.get("price_setup", {}).get("net", 0))
                    break
            
            # Extract specifications from description or other fields
            cpu_info = "N/A"
            ram_info = "N/A"
            storage_info = "N/A"
            
            # Try to extract specs from description
            if description:
                # CPU extraction
                cpu_match = re.search(r'(\d+)\s*x\s*([\w\s\-\.]+(?:GHz)?)', description, re.IGNORECASE)
                if cpu_match:
                    cpu_info = f"{cpu_match.group(1)}x {cpu_match.group(2).strip()}"
                
                # RAM extraction
                ram_match = re.search(r'(\d+)\s*GB\s*(?:DDR\d*\s*)?RAM', description, re.IGNORECASE)
                if ram_match:
                    ram_info = f"{ram_match.group(1)} GB RAM"
                
                # Storage extraction
                storage_match = re.search(r'(\d+(?:\.\d+)?)\s*(TB|GB)\s*(SSD|HDD|NVMe)', description, re.IGNORECASE)
                if storage_match:
                    storage_info = f"{storage_match.group(1)} {storage_match.group(2)} {storage_match.group(3)}"
            
            # Location information
            locations = []
            if "location" in product:
                locations = [product["location"]]
            elif "datacenter" in product:
                locations = [product["datacenter"]]
            
            server_data = {
                "type": "dedicated-robot-api",
                "instanceType": name,
                "description": description,
                "cpu": cpu_info,
                "ram": ram_info,
                "storage": storage_info,
                "priceEUR_monthly_net": monthly_price,
                "setup_feeEUR": setup_fee,
                "locations": locations,
                "source": "robot_api_enhanced",
                "robot_product_id": product.get("id"),
                "availability": product.get("available", "unknown"),
                # Note about IPv4 pricing changes
                "pricing_note": "Prices exclude Primary IPv4 addon (as of March 28, 2022). IPv6-only by default.",
                "ipv4_addon_required": True,
                "raw_product_data": product
            }
            
            processed_servers.append(server_data)
            
        except Exception as e:
            logger.error(f"Error processing dedicated server product {product.get('name', 'unknown')}: {e}")
            continue
    
    logger.info(f"Processed {len(processed_servers)} dedicated server products")
    return processed_servers

def fetch_dedicated_server_addons():
    """Fetch addon pricing including Primary IPv4 from Robot API."""
    logger.info("Fetching dedicated server addons from Robot API...")
    
    data = make_robot_api_request("order/server/addon")
    if data and isinstance(data, list):
        logger.info(f"Successfully fetched {len(data)} server addons")
        return data
    elif data and isinstance(data, dict) and "addon" in data:
        addons = data["addon"]
        logger.info(f"Successfully fetched {len(addons)} server addons")
        return addons
    
    logger.warning("Failed to fetch dedicated server addons from Robot API")
    return []

def fetch_server_types():
    """Fetch server types from Hetzner Cloud API."""
    logger.info("Fetching server types from API...")
    
    data = make_api_request("server_types")
    if data and validate_api_response(data, ["server_types"]):
        server_types = data["server_types"]
        logger.info(f"Successfully fetched {len(server_types)} server types")
        return server_types
    
    logger.warning("Failed to fetch server types from API")
    return []

def fetch_pricing_data():
    """Fetch pricing data from Hetzner Cloud API."""
    logger.info("Fetching pricing data from API...")
    
    data = make_api_request("pricing")
    if data and validate_api_response(data, ["pricing"]):
        pricing = data["pricing"]
        logger.info("Successfully fetched pricing data")
        return pricing
    
    logger.warning("Failed to fetch pricing data from API")
    return {}

def fetch_load_balancer_types():
    """Fetch load balancer types from Hetzner Cloud API."""
    logger.info("Fetching load balancer types from API...")
    
    data = make_api_request("load_balancer_types")
    if data and validate_api_response(data, ["load_balancer_types"]):
        lb_types = data["load_balancer_types"]
        logger.info(f"Successfully fetched {len(lb_types)} load balancer types")
        return lb_types
    
    logger.warning("Failed to fetch load balancer types from API")
    return []

def fetch_volume_types():
    """Fetch volume (block storage) types from Hetzner Cloud API."""
    logger.info("Fetching volume types from API...")
    
    # Note: Hetzner API doesn't have a dedicated volume_types endpoint
    # Volume pricing is included in the main pricing endpoint
    pricing_data = fetch_pricing_data()
    if pricing_data and "volume" in pricing_data:
        logger.info("Successfully fetched volume pricing data")
        return pricing_data["volume"]
    
    logger.warning("No volume pricing data found in API response")
    return {}

def fetch_floating_ip_pricing():
    """Fetch floating IP pricing from Hetzner Cloud API."""
    logger.info("Fetching floating IP pricing from API...")
    
    pricing_data = fetch_pricing_data()
    if pricing_data and "floating_ip" in pricing_data:
        logger.info("Successfully fetched floating IP pricing")
        return pricing_data["floating_ip"]
    
    logger.warning("No floating IP pricing data found in API response")
    return {}

def fetch_network_pricing():
    """Fetch network pricing from Hetzner Cloud API."""
    logger.info("Fetching network pricing from API...")
    
    pricing_data = fetch_pricing_data()
    if pricing_data and "network" in pricing_data:
        logger.info("Successfully fetched network pricing")
        return pricing_data["network"]
    
    logger.warning("No network pricing data found in API response")
    return {}

def process_server_types_with_pricing(server_types, pricing_data):
    """
    Process server types with enhanced pricing data.
    
    Args:
        server_types (list): List of server type data
        pricing_data (dict): Pricing data from API
    
    Returns:
        list: Processed server data
    """
    logger.info("Processing server types with pricing data...")
    
    price_map = {}
    if "server_types" in pricing_data:
        price_map = {p["name"]: p for p in pricing_data["server_types"]}
    
    processed_servers = []
    
    for s_type in server_types:
        try:
            price_info = price_map.get(s_type["name"], {})
            
            # Extract primary pricing information
            primary_price_eur_hourly_net = None
            primary_price_eur_monthly_net = None
            locations = []
            
            if price_info.get("prices"):
                for price_detail in price_info["prices"]:
                    location = price_detail.get("location")
                    if location:
                        locations.append(location)
                    
                    # Get first available pricing as primary
                    if primary_price_eur_hourly_net is None:
                        hourly_price = price_detail.get("price_hourly", {}).get("net")
                        monthly_price = price_detail.get("price_monthly", {}).get("net")
                        
                        if hourly_price:
                            primary_price_eur_hourly_net = float(hourly_price)
                        if monthly_price:
                            primary_price_eur_monthly_net = float(monthly_price)
            
            # Create enhanced server data structure
            server_data = {
                "type": "cloud-api",
                "instanceType": s_type["name"],
                "vCPU": s_type["cores"],
                "memoryGiB": s_type["memory"],
                "diskType": s_type["storage_type"],
                "diskSizeGB": s_type["disk"],
                "priceEUR_hourly_net": primary_price_eur_hourly_net,
                "priceEUR_monthly_net": primary_price_eur_monthly_net,
                "trafficOutTB": s_type.get("included_traffic", 0) / 1024 / 1024 / 1024 if s_type.get("included_traffic") else None,
                "cpuType": s_type["cpu_type"],
                "architecture": s_type["architecture"],
                "locations": locations,
                "deprecated": s_type["deprecated"],
                "source": "api_enhanced",
                # Enhanced metadata
                "description": s_type.get("description", ""),
                "created": s_type.get("created"),
                "api_id": s_type.get("id"),
                "pricing_details": price_info.get("prices", [])
            }
            
            processed_servers.append(server_data)
            
        except Exception as e:
            logger.error(f"Error processing server type {s_type.get('name', 'unknown')}: {e}")
            continue
    
    logger.info(f"Processed {len(processed_servers)} server types with pricing")
    return processed_servers

def process_load_balancer_types_with_pricing(lb_types, pricing_data):
    """Process load balancer types with pricing."""
    logger.info("Processing load balancer types with pricing...")
    
    price_map = {}
    if "load_balancer_types" in pricing_data:
        price_map = {p["name"]: p for p in pricing_data["load_balancer_types"]}
    
    processed_lbs = []
    
    for lb_type in lb_types:
        try:
            price_info = price_map.get(lb_type["name"], {})
            
            # Extract pricing information
            primary_price_eur_hourly_net = None
            primary_price_eur_monthly_net = None
            locations = []
            
            if price_info.get("prices"):
                for price_detail in price_info["prices"]:
                    location = price_detail.get("location")
                    if location:
                        locations.append(location)
                    
                    if primary_price_eur_hourly_net is None:
                        hourly_price = price_detail.get("price_hourly", {}).get("net")
                        monthly_price = price_detail.get("price_monthly", {}).get("net")
                        
                        if hourly_price:
                            primary_price_eur_hourly_net = float(hourly_price)
                        if monthly_price:
                            primary_price_eur_monthly_net = float(monthly_price)
            
            lb_data = {
                "type": "load-balancer",
                "instanceType": lb_type["name"],
                "maxConnections": lb_type.get("max_connections"),
                "maxTargets": lb_type.get("max_targets"),
                "maxServices": lb_type.get("max_services"),
                "maxAssignedCertificates": lb_type.get("max_assigned_certificates"),
                "priceEUR_hourly_net": primary_price_eur_hourly_net,
                "priceEUR_monthly_net": primary_price_eur_monthly_net,
                "locations": locations,
                "deprecated": lb_type.get("deprecated", False),
                "source": "api_enhanced",
                "description": lb_type.get("description", ""),
                "api_id": lb_type.get("id")
            }
            
            processed_lbs.append(lb_data)
            
        except Exception as e:
            logger.error(f"Error processing load balancer type {lb_type.get('name', 'unknown')}: {e}")
            continue
    
    logger.info(f"Processed {len(processed_lbs)} load balancer types")
    return processed_lbs

def process_volume_pricing(volume_pricing):
    """Process volume (block storage) pricing data."""
    logger.info("Processing volume pricing...")
    
    if not volume_pricing:
        return []
    
    processed_volumes = []
    
    try:
        # Process different volume pricing tiers
        for price_detail in volume_pricing:
            if isinstance(price_detail, dict):
                volume_data = {
                    "type": "volume",
                    "instanceType": "Block Storage Volume",
                    "priceEUR_per_GB_monthly": float(price_detail.get("price_monthly", {}).get("net", 0)),
                    "location": price_detail.get("location"),
                    "source": "api_enhanced",
                    "description": "Block storage volume pricing per GB"
                }
                processed_volumes.append(volume_data)
                
    except Exception as e:
        logger.error(f"Error processing volume pricing: {e}")
    
    logger.info(f"Processed {len(processed_volumes)} volume pricing entries")
    return processed_volumes

def process_floating_ip_pricing(floating_ip_pricing):
    """Process floating IP pricing data."""
    logger.info("Processing floating IP pricing...")
    
    if not floating_ip_pricing:
        return []
    
    processed_ips = []
    
    try:
        for price_detail in floating_ip_pricing:
            if isinstance(price_detail, dict):
                ip_data = {
                    "type": "floating-ip",
                    "instanceType": "Floating IP",
                    "priceEUR_monthly_net": float(price_detail.get("price_monthly", {}).get("net", 0)),
                    "location": price_detail.get("location"),
                    "source": "api_enhanced",
                    "description": "Floating IP pricing per month"
                }
                processed_ips.append(ip_data)
                
    except Exception as e:
        logger.error(f"Error processing floating IP pricing: {e}")
    
    logger.info(f"Processed {len(processed_ips)} floating IP pricing entries")
    return processed_ips

def process_network_pricing(network_pricing):
    """Process network pricing data."""
    logger.info("Processing network pricing...")
    
    if not network_pricing:
        return []
    
    processed_networks = []
    
    try:
        for price_detail in network_pricing:
            if isinstance(price_detail, dict):
                network_data = {
                    "type": "network",
                    "instanceType": "Private Network",
                    "priceEUR_monthly_net": float(price_detail.get("price_monthly", {}).get("net", 0)),
                    "location": price_detail.get("location"),
                    "source": "api_enhanced",
                    "description": "Private network pricing per month"
                }
                processed_networks.append(network_data)
                
    except Exception as e:
        logger.error(f"Error processing network pricing: {e}")
    
    logger.info(f"Processed {len(processed_networks)} network pricing entries")
    return processed_networks

def create_webdriver():
    """Create a headless Chrome WebDriver instance with optimized settings for CI/local environments."""
    chrome_options = Options()
    
    # Essential headless options for CI environments
    chrome_options.add_argument("--headless=new")  # Use new headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-features=TranslateUI,VizDisplayCompositor")
    chrome_options.add_argument("--run-all-compositor-stages-before-draw")
    chrome_options.add_argument("--disable-ipc-flooding-protection")
    
    # Window and display settings
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-images")
    
    # Network and security settings
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    chrome_options.add_argument("--ignore-certificate-errors-spki-list")
    chrome_options.add_argument("--ignore-certificate-errors-spki-list")
    
    # CI-specific arguments
    if os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS'):
        print("Detected CI environment, adding CI-specific Chrome options...")
        chrome_options.add_argument("--disable-background-networking")
        chrome_options.add_argument("--disable-default-apps")
        chrome_options.add_argument("--disable-sync")
        chrome_options.add_argument("--metrics-recording-only")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--disable-crash-reporter")
        chrome_options.add_argument("--disable-logging")
        chrome_options.add_argument("--disable-notifications")
        
    chrome_options.add_argument(f"--user-agent={BASE_HEADERS['User-Agent']}")
    
    # Set Chrome binary path if specified in environment
    chrome_bin = os.environ.get('CHROME_BIN')
    if chrome_bin:
        chrome_options.binary_location = chrome_bin
        print(f"Using Chrome binary from environment: {chrome_bin}")
    
    try:
        print("Installing ChromeDriver via webdriver-manager...")
        service = Service(ChromeDriverManager().install())
        print(f"ChromeDriver installed at: {service.path}")
        
        print("Creating Chrome WebDriver instance...")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Verify WebDriver is working
        print(f"Chrome version: {driver.capabilities['browserVersion']}")
        print(f"ChromeDriver version: {driver.capabilities['chrome']['chromedriverVersion']}")
        
        return driver
        
    except Exception as e:
        print(f"Failed to create Chrome WebDriver with webdriver-manager: {e}")
        print("Trying with system Chrome...")
        try:
            # Fallback to system Chrome without explicit service
            driver = webdriver.Chrome(options=chrome_options)
            print("Successfully created WebDriver with system Chrome")
            return driver
        except Exception as e2:
            print(f"Failed to create system Chrome WebDriver: {e2}")
            print("Chrome installation tips:")
            print("1. Ensure Chrome/Chromium is installed")
            print("2. Check Chrome version compatibility with ChromeDriver")
            print("3. In CI: Verify Chrome dependencies are installed")
            raise

def wait_for_content_load(driver, url, timeout=None):
    """Load page and wait for JavaScript content to render."""
    # Use environment variable for timeout in CI, otherwise default
    if timeout is None:
        timeout = int(os.environ.get('SELENIUM_TIMEOUT', '30'))
    
    print(f"Loading page: {url}")
    print(f"Using timeout: {timeout} seconds")
    
    try:
        driver.get(url)
    except Exception as e:
        print(f"Error loading page: {e}")
        return False
    
    print("Waiting for JavaScript content to load...")
    try:
        # Wait for the main server listing container to be present
        wait = WebDriverWait(driver, timeout)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.border-card")))
        
        # Additional wait to ensure all content is loaded (shorter in CI)
        additional_wait = 2 if os.environ.get('CI') else 3
        time.sleep(additional_wait)
        
        # Check if we actually have server elements
        server_elements = driver.find_elements(By.CSS_SELECTOR, "li.border-card")
        print(f"Found {len(server_elements)} server elements after waiting")
        
        if len(server_elements) == 0:
            print("Warning: No server elements found even after waiting. Content might still be loading.")
            # Wait a bit more and try again (shorter wait in CI)
            additional_wait = 3 if os.environ.get('CI') else 5
            time.sleep(additional_wait)
            server_elements = driver.find_elements(By.CSS_SELECTOR, "li.border-card")
            print(f"Found {len(server_elements)} server elements after additional wait")
        
        return True
        
    except TimeoutException:
        print(f"Timeout waiting for content to load after {timeout} seconds")
        # Check if any elements are present at all
        server_elements = driver.find_elements(By.CSS_SELECTOR, "li.border-card")
        if len(server_elements) > 0:
            print(f"Found {len(server_elements)} elements despite timeout - proceeding")
            return True
        else:
            print("No server elements found - this might be a loading issue")
            if os.environ.get('CI'):
                print("CI environment detected - this could be due to network restrictions or slower CI runners")
            return False

def scrape_hetzner_cloud_page():
    """Scrapes Hetzner cloud server details from their public webpage as a fallback."""
    url = "https://www.hetzner.com/cloud"
    print(f"Attempting to scrape Hetzner Cloud data from {url} as fallback...")
    
    try:
        response = requests.get(url, headers=BASE_HEADERS, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch cloud pricing page: {e}")
        return []
    
    soup = BeautifulSoup(response.text, "html.parser")
    result = []
    server_elements = soup.select('div[class*="plans-card"], div[class*="pricing-card"]')

    print(f"Found {len(server_elements)} potential cloud server elements on page for scraping.")
    for el_idx, el in enumerate(server_elements):
        try:
            name_tag = el.select_one('h3, .package-name, .plan-title')
            name = name_tag.text.strip() if name_tag else f"Cloud Scrape {el_idx+1} (Name N/A)"
            specs_text = el.get_text(separator=" ").lower()
            vcpu_match = re.search(r'(\d+)\s*vCPU', specs_text, re.IGNORECASE)
            vcpu = int(vcpu_match.group(1)) if vcpu_match else None
            memory_match = re.search(r'(\d+)\s*GB\s*RAM', specs_text, re.IGNORECASE)
            memory_gb = int(memory_match.group(1)) if memory_match else None
            disk_match = re.search(r'(\d+)\s*GB\s*(SSD|NVMe|Storage)', specs_text, re.IGNORECASE)
            disk_gb = int(disk_match.group(1)) if disk_match else None
            disk_type = disk_match.group(2) if disk_match and disk_match.group(2) else None
            price_tag = el.select_one('.price, .package-price, .plan-price')
            price_text = price_tag.text.strip() if price_tag else "N/A"
            price_eur_hourly = None
            price_eur_monthly_match = re.search(r'€\s*([\d,.]+)\s*/\s*mo', price_text, re.IGNORECASE)
            if not price_eur_monthly_match:
                 price_eur_monthly_match = re.search(r'€\s*([\d,.]+)', price_text, re.IGNORECASE)

            price_eur_monthly = None
            if price_eur_monthly_match:
                try:
                    price_str = price_eur_monthly_match.group(1).replace(',', '.')
                    price_eur_monthly = float(price_str)
                    price_eur_hourly = round(price_eur_monthly / 720, 5)
                except ValueError: pass
            if name and (vcpu or memory_gb or disk_gb):
                result.append({
                    "type": "cloud-scraped", "instanceType": name, "vCPU": vcpu, "memoryGiB": memory_gb,
                    "diskType": disk_type, "diskSizeGB": disk_gb,
                    "priceEUR_hourly_net_estimated": price_eur_hourly, "priceEUR_monthly_net": price_eur_monthly,
                    "source": "scraped_webpage"
                })
        except Exception as e:
            logger.error(f"Error scraping a cloud element: {e}")
    return result

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

def parse_price(price_text, default=None):
    if not price_text or price_text == "N/A": return default
    try:
        cleaned = re.sub(r'[^\d,.]', '', price_text)
        if ',' in cleaned and '.' in cleaned: cleaned = cleaned.replace('.', '').replace(',', '.')
        else: cleaned = cleaned.replace(',', '.')
        return float(cleaned)
    except ValueError: return default

def find_ipv6_toggle(driver):
    """Find and return the IPv6 toggle element."""
    toggle_selectors = [
        '.serverboerse_toggle input[type="checkbox"]',
        '.serverboerse_toggle',
        'input[role="switch"]',
        'div.react-switch-bg',
        '[aria-checked]'
    ]
    
    for selector in toggle_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                print(f"Found IPv6 toggle using selector: {selector}")
                return elements[0]
        except Exception as e:
            print(f"Error finding toggle with selector {selector}: {e}")
            continue
    
    print("Warning: IPv6 toggle not found with any selector")
    return None

def toggle_ipv6_mode(driver, enable_ipv6=True):
    """Toggle IPv6-only mode on/off and wait for page to update."""
    print(f"Attempting to {'enable' if enable_ipv6 else 'disable'} IPv6-only mode...")
    
    toggle_element = find_ipv6_toggle(driver)
    if not toggle_element:
        print("IPv6 toggle not found - continuing with current mode")
        return False
    
    try:
        # Check current state
        current_state = False
        if toggle_element.get_attribute('aria-checked'):
            current_state = toggle_element.get_attribute('aria-checked').lower() == 'true'
        elif toggle_element.get_attribute('checked'):
            current_state = toggle_element.is_selected()
        
        print(f"Current IPv6 toggle state: {current_state}")
        
        # Only click if we need to change state
        if current_state != enable_ipv6:
            print(f"Clicking toggle to {'enable' if enable_ipv6 else 'disable'} IPv6-only mode...")
            
            # Try clicking the toggle element or its parent
            try:
                driver.execute_script("arguments[0].click();", toggle_element)
            except:
                # Fallback: try clicking parent container
                parent = toggle_element.find_element(By.XPATH, "..")
                driver.execute_script("arguments[0].click();", parent)
            
            # Wait for page to update after toggle
            print("Waiting for page to update after IPv6 toggle...")
            time.sleep(3 if os.environ.get('CI') else 5)
            
            # Verify the change took effect
            wait = WebDriverWait(driver, 10)
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.border-card")))
            except TimeoutException:
                print("Warning: Timeout waiting for content after IPv6 toggle")
            
            print(f"IPv6-only mode {'enabled' if enable_ipv6 else 'disabled'} successfully")
            return True
        else:
            print(f"IPv6-only mode already in desired state ({'enabled' if enable_ipv6 else 'disabled'})")
            return True
            
    except Exception as e:
        print(f"Error toggling IPv6 mode: {e}")
        return False

def set_items_per_page(driver, items_count=50):
    """Set the number of items per page using the React Select dropdown. Extra robust for headless/CI environments."""
    print(f"Attempting to set items per page to {items_count}...")

    try:
        wait = WebDriverWait(driver, 20)
        dropdown_selector = 'div.css-v8bn4a-control'
        dropdown_element = None

        # Try to find the dropdown
        try:
            dropdown_element = driver.find_element(By.CSS_SELECTOR, dropdown_selector)
            print(f"Found dropdown using selector: {dropdown_selector}")
        except Exception:
            fallback_selectors = [
                'div[class*="css-v8bn4a-control"]',
                'div[class*="control"]',
                '[role="combobox"]'
            ]
            for selector in fallback_selectors:
                try:
                    dropdown_element = driver.find_element(By.CSS_SELECTOR, selector)
                    print(f"Found dropdown using fallback selector: {selector}")
                    break
                except Exception:
                    continue

        if not dropdown_element:
            print("Warning: Could not find items per page dropdown - continuing with default")
            return False

        # Click the dropdown to open it (use JS for reliability in headless)
        driver.execute_script("arguments[0].scrollIntoView(true);", dropdown_element)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", dropdown_element)
        time.sleep(1)

        # Try clicking the input inside the dropdown for React Select
        try:
            input_element = dropdown_element.find_element(By.CSS_SELECTOR, 'input[id*="react-select"]')
            driver.execute_script("arguments[0].focus();", input_element)
            input_element.click()
            time.sleep(1)
            # Send DOWN keys to open options
            from selenium.webdriver.common.keys import Keys
            input_element.send_keys(Keys.ARROW_DOWN)
            input_element.send_keys(Keys.ARROW_DOWN)
            input_element.send_keys(Keys.ENTER)
            print("Sent ARROW_DOWN and ENTER keys to input")
            time.sleep(2)
        except Exception as e:
            print(f"Input element interaction failed: {e}")

        # Wait for options to appear and try to click the correct one
        option_found = False
        for _ in range(10):
            options = driver.find_elements(By.CSS_SELECTOR, '[id*="react-select"][id*="option"], div[role="option"], div[class*="option"]')
            for option in options:
                try:
                    if option.is_displayed() and option.text.strip() == str(items_count):
                        print(f"Found option '{items_count}', clicking...")
                        driver.execute_script("arguments[0].click();", option)
                        option_found = True
                        break
                except Exception:
                    continue
            if option_found:
                break
            time.sleep(1)

        # Fallback: try JS to click the option if not found
        if not option_found:
            print("Trying JS fallback for React Select option...")
            driver.execute_script(f"""
                var opts = Array.from(document.querySelectorAll('[id*="react-select"][id*="option"], div[role="option"], div[class*="option"]'));
                for (var i=0; i<opts.length; i++) {{
                    var el = opts[i];
                    if (el.textContent.trim() === '{items_count}' && el.offsetParent !== null) {{
                        el.click();
                        return;
                    }}
                }}
            """)
            time.sleep(2)
            # Check if it worked
            server_elements = driver.find_elements(By.CSS_SELECTOR, "li.border-card")
            if len(server_elements) > 10:
                print(f"Successfully set items per page to {items_count} via JS fallback")
                return True

        # Wait for the page to update after changing the dropdown
        print("Waiting for page to update after changing items per page...")
        time.sleep(5)

        # Wait for server elements to be present with new count
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.border-card")))
        except TimeoutException:
            print("Warning: Timeout waiting for content after changing items per page")

        # Verify we now have more items
        server_elements = driver.find_elements(By.CSS_SELECTOR, "li.border-card")
        print(f"After setting items per page: found {len(server_elements)} server elements")

        if len(server_elements) > 10:
            print(f"Successfully set items per page to {items_count}")
            return True
        else:
            print("Warning: Items per page setting may not have taken effect")
            return False

    except Exception as e:
        print(f"Error setting items per page: {e}")
        return False

def detect_pagination_info(driver):
    """Detect pagination information and available navigation options."""
    pagination_info = {
        'current_page': 1,
        'total_pages': 1,
        'has_next': False,
        'next_element': None,
        'pagination_type': 'none'
    }
    
    try:
        # Look for pagination container
        pagination_selectors = [
            'ul.pagination-sf',
            'ul.pagination',
            '.pagination',
            '[class*="pagination"]'
        ]
        
        pagination_container = None
        for selector in pagination_selectors:
            try:
                containers = driver.find_elements(By.CSS_SELECTOR, selector)
                if containers:
                    pagination_container = containers[0]
                    pagination_info['pagination_type'] = 'numbered'
                    print(f"Found pagination container with selector: {selector}")
                    break
            except Exception:
                continue
        
        if pagination_container:
            # Extract current page using multiple strategies
            try:
                # Try multiple selectors for active page
                active_selectors = [
                    'li.active span',
                    'span.active',
                    'li.active',
                    'a.active',
                    '[aria-current="page"]'
                ]
                
                current_page_found = False
                for selector in active_selectors:
                    try:
                        active_elements = pagination_container.find_elements(By.CSS_SELECTOR, selector)
                        for active_element in active_elements:
                            page_text = active_element.text.strip()
                            if page_text.isdigit():
                                pagination_info['current_page'] = int(page_text)
                                print(f"DEBUG: Found current page {pagination_info['current_page']} using selector '{selector}'")
                                current_page_found = True
                                break
                        if current_page_found:
                            break
                    except Exception:
                        continue
                
                # If still not found, try to extract from URL parameters
                if not current_page_found:
                    try:
                        current_url = driver.current_url
                        if 'page=' in current_url:
                            import re
                            page_match = re.search(r'page=(\d+)', current_url)
                            if page_match:
                                pagination_info['current_page'] = int(page_match.group(1))
                                print(f"DEBUG: Found current page {pagination_info['current_page']} from URL")
                    except Exception:
                        pass
                        
            except Exception as e:
                print(f"DEBUG: Error detecting current page: {e}")
            
            # Look for next page link with enhanced detection
            next_selectors = [
                'li:last-child span i.fa-angle-right',
                'a[rel="next"]',
                '.pagination li:last-child',
                'li span i.fa-angle-right',
                'li:last-child a',
                'li:last-child'
            ]
            
            for selector in next_selectors:
                try:
                    next_elements = pagination_container.find_elements(By.CSS_SELECTOR, selector)
                    if next_elements:
                        next_parent = next_elements[0]
                        
                        # Navigate up to find the clickable parent
                        while next_parent and next_parent.tag_name in ['i', 'span']:
                            next_parent = next_parent.find_element(By.XPATH, "./..")
                        
                        if next_parent:
                            # Enhanced checks for disabled state
                            parent_class = next_parent.get_attribute('class') or ''
                            parent_aria_disabled = next_parent.get_attribute('aria-disabled') or 'false'
                            
                            # Check if element is truly clickable
                            is_disabled = (
                                'disabled' in parent_class.lower() or
                                'inactive' in parent_class.lower() or
                                parent_aria_disabled.lower() == 'true' or
                                not next_parent.is_enabled()
                            )
                            
                            if not is_disabled:
                                pagination_info['has_next'] = True
                                pagination_info['next_element'] = next_parent
                                print(f"DEBUG: Found next page element with selector: {selector}, class: {parent_class}")
                                break
                            else:
                                print(f"DEBUG: Next page element found but disabled with selector: {selector}, class: {parent_class}")
                                
                except Exception as e:
                    print(f"DEBUG: Error with next page selector {selector}: {e}")
                    continue
            
            # Try to find total pages
            try:
                page_links = pagination_container.find_elements(By.CSS_SELECTOR, 'li span')
                page_numbers = []
                for link in page_links:
                    try:
                        if link.text.strip().isdigit():
                            page_numbers.append(int(link.text.strip()))
                    except:
                        continue
                if page_numbers:
                    pagination_info['total_pages'] = max(page_numbers)
            except Exception:
                pass
        
        # Look for "Load More" or infinite scroll
        load_more_selectors = [
            'button[class*="load-more"]',
            'button[class*="show-more"]',
            '.load-more',
            '.show-more'
        ]
        
        for selector in load_more_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    pagination_info['pagination_type'] = 'load_more'
                    pagination_info['has_next'] = True
                    pagination_info['next_element'] = elements[0]
                    print(f"Found load-more button with selector: {selector}")
                    break
            except Exception:
                continue
        
        print(f"Pagination info: {pagination_info}")
        return pagination_info
        
    except Exception as e:
        print(f"Error detecting pagination: {e}")
        return pagination_info

def navigate_to_next_page(driver, pagination_info):
    """Navigate to the next page and wait for content to load."""
    if not pagination_info['has_next'] or not pagination_info['next_element']:
        return False
    
    try:
        print(f"Navigating to next page (current: {pagination_info['current_page']})...")
        
        # Scroll to pagination element
        driver.execute_script("arguments[0].scrollIntoView(true);", pagination_info['next_element'])
        time.sleep(0.5)  # Reduced wait time
        
        # Click the next page element
        driver.execute_script("arguments[0].click();", pagination_info['next_element'])
        
        # Wait for page to load - optimized for faster pagination
        wait_time = 3 if os.environ.get('CI') else 4  # Reduced wait times
        print(f"Waiting {wait_time} seconds for next page to load...")
        time.sleep(wait_time)
        
        # Wait for content to be present
        wait = WebDriverWait(driver, 10)  # Reduced timeout
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.border-card")))
        
        # Minimal additional wait for content to load
        time.sleep(1)  # Reduced from 2 seconds
        
        print("Successfully navigated to next page")
        return True
        
    except Exception as e:
        print(f"Error navigating to next page: {e}")
        return False

def fetch_hetzner_dedicated_page(page_url, driver, ipv6_mode=False):
    """Fetch dedicated servers using Selenium WebDriver to handle JavaScript content."""
    print(f"Fetching dedicated servers from {page_url} (IPv6 mode: {ipv6_mode})")
    
    # Load page and wait for content
    if not wait_for_content_load(driver, page_url):
        print("Failed to load content properly, attempting to continue anyway...")
    
    # Get page source after JavaScript execution
    page_source = driver.page_source
    soup = BeautifulSoup(page_source, "html.parser")
    page_results = []

    # --- PRIMARY SELECTOR based on user's HTML snippet ---
    server_elements = soup.select('li.border-card')
    
    if not server_elements:
        print("Warning: Primary selector 'li.border-card' found 0 elements. Trying fallbacks...")
        # Fallback selectors if the primary one fails
        server_elements = soup.select('div.row > div[class*="col-"]')
        if not server_elements:
             server_elements = soup.select('div[class*="server-"], article[class*="server"]')

    print(f"Found {len(server_elements)} potential dedicated server elements on this page with current selectors.")

    for card_idx, card in enumerate(server_elements):
        # Name: From user's HTML: div.product-name-sf
        name_tag = card.select_one('div.product-name-sf')
        name = name_tag.text.strip() if name_tag else f"Server {card_idx+1} (Name N/A)"

        # Specs: From user's HTML: div.sf-serverdata-grid with spans.
        specs_container = card.select_one('div.sf-serverdata-grid')
        specs_dict = {}
        cpu_info, gen_info, ram_info, drives_info, location_info, information_info = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"

        if specs_container:
            # Parse specs using the grid structure - look for all direct children (spans and divs)
            all_children = specs_container.find_all(['span', 'div'], recursive=False)
            current_label_text = None
            
            for child in all_children:
                child_text = child.text.strip()
                
                # Check if this is a label (bold span)
                if (child.name == 'span' and
                    child.get('style') and
                    'font-weight: bold' in child.get('style', '').lower()):
                    current_label_text = child_text.lower()
                    continue
                
                # Process value for current label
                if current_label_text:
                    if "cpu" in current_label_text: cpu_info = child_text
                    elif "generation" in current_label_text: gen_info = child_text
                    elif "ram" in current_label_text: ram_info = child_text
                    elif "drives" in current_label_text: # Drives can be complex, might need further parsing if nested
                        drive_detail_tag = child.select_one('div.list-style-none')
                        drives_info = drive_detail_tag.text.strip() if drive_detail_tag else child_text
                    elif "location" in current_label_text:
                        # Fix location parsing - child can be div or span
                        # Structure: <div><span class="special-tag fi fi-fi" data-toggle="tooltip" data-original-title="Finland, HEL1">á</span></div>
                        location_info = "N/A"  # Default
                        
                        # Look for location flag elements within this child element
                        flag_elements = child.find_all('span', class_='special-tag')
                        if flag_elements:
                            locations = []
                            for flag in flag_elements:
                                if flag.get('data-original-title'):
                                    locations.append(flag['data-original-title'])
                            
                            if locations:
                                location_info = ', '.join(locations)
                            else:
                                # Fallback: look for any element with data-original-title
                                title_elements = child.find_all(attrs={'data-original-title': True})
                                if title_elements:
                                    locations = [elem['data-original-title'] for elem in title_elements]
                                    location_info = ', '.join(locations)
                        
                        # Final fallback: use child text if we still have nothing
                        if location_info == "N/A":
                            location_info = child_text if child_text and child_text.strip() else "N/A"
                    elif "information" in current_label_text:
                        information_info = child_text # Captures IPv4 etc.
                    
                    specs_dict[current_label_text.replace(":", "").replace(" ", "_")] = child_text
                    current_label_text = None # Reset for next label
        else:
            print(f"Warning: No specs container 'div.sf-serverdata-grid' found for {name}.")

        # Pricing: From user's HTML
        monthly_price_tag = card.select_one('div.product-price-sf > div:first-child')
        monthly_price_text = monthly_price_tag.text.strip() if monthly_price_tag else "N/A"
        price_eur_monthly = parse_price(monthly_price_text)

        hourly_price_tag = card.select_one('div.sf-hourprice')
        hourly_price_text = hourly_price_tag.text.strip() if hourly_price_tag else "N/A"
        price_eur_hourly = parse_price(hourly_price_text)
        
        setup_fee_tag = card.select_one('div.product-setup-price-pill-sf') # From user HTML
        setup_fee_text = setup_fee_tag.text.strip() if setup_fee_tag else "N/A"
        setup_fee = parse_price(setup_fee_text)

        if name != f"Server {card_idx+1} (Name N/A)" and (price_eur_monthly is not None or price_eur_hourly is not None or cpu_info != "N/A"):
            # Create server data with IPv4/IPv6 pricing distinction
            server_data = {
                "type": "dedicated",
                "instanceType": name,
                "cpu": cpu_info,
                "ram": ram_info,
                "storage": drives_info,
                "generation": gen_info,
                "location": location_info,
                "information": information_info,
                "raw_specs_from_grid": specs_dict,
                "source_url": page_url
            }
            
            # Add pricing with IPv4/IPv6 distinction
            if ipv6_mode:
                server_data.update({
                    "priceEUR_monthly_net_ipv6": price_eur_monthly,
                    "priceEUR_hourly_net_ipv6": price_eur_hourly,
                    "setup_feeEUR_ipv6": setup_fee,
                    "ipv6_pricing_available": True
                })
            else:
                server_data.update({
                    "priceEUR_monthly_net_ipv4": price_eur_monthly,
                    "priceEUR_hourly_net_ipv4": price_eur_hourly,
                    "setup_feeEUR_ipv4": setup_fee,
                    "ipv4_pricing_available": True
                })
            
            page_results.append(server_data)
        else:
            print(f"Skipping card '{name}' due to insufficient data (no valid name, price or key specs).")
    
    return page_results

def merge_ipv4_ipv6_data(ipv4_servers, ipv6_servers):
    """Merge IPv4 and IPv6 pricing data for the same servers."""
    merged_servers = {}
    
    # Enhanced merge logic with multiple fallback identification methods
    # Primary key: instanceType + location, fallback: instanceType + cpu + ram
    for server in ipv4_servers:
        primary_key = f"{server['instanceType']}_{server.get('location', 'unknown')}"
        fallback_key = f"{server['instanceType']}_{server.get('cpu', 'unknown')}_{server.get('ram', 'unknown')}"
        
        merged_servers[primary_key] = server.copy()
        # Store fallback mapping for robust matching
        if fallback_key not in merged_servers:
            merged_servers[f"fallback_{fallback_key}"] = primary_key
    
    # Merge IPv6 data with existing IPv4 data using enhanced matching
    for server in ipv6_servers:
        primary_key = f"{server['instanceType']}_{server.get('location', 'unknown')}"
        fallback_key = f"{server['instanceType']}_{server.get('cpu', 'unknown')}_{server.get('ram', 'unknown')}"
        
        matched_key = None
        if primary_key in merged_servers:
            matched_key = primary_key
        elif f"fallback_{fallback_key}" in merged_servers:
            matched_key = merged_servers[f"fallback_{fallback_key}"]
        
        if matched_key and matched_key in merged_servers:
            # Merge IPv6 pricing into existing server
            merged_servers[matched_key].update({
                "priceEUR_monthly_net_ipv6": server.get("priceEUR_monthly_net_ipv6"),
                "priceEUR_hourly_net_ipv6": server.get("priceEUR_hourly_net_ipv6"),
                "setup_feeEUR_ipv6": server.get("setup_feeEUR_ipv6"),
                "ipv6_pricing_available": True
            })
        else:
            # IPv6-only server (no IPv4 match)
            merged_servers[primary_key] = server.copy()
    
    # Filter out fallback keys and return only actual server data
    final_results = []
    for key, value in merged_servers.items():
        if not key.startswith('fallback_'):
            final_results.append(value)
    
    return final_results

def fetch_hetzner_dedicated():
    """
    Fetch Hetzner dedicated servers via Robot API only.
    
    Strategy: Use Robot API exclusively for reliable, fast data collection
    """
    logger.info("Starting Hetzner dedicated server data fetch via Robot API...")
    all_results = []
    
    # Robot API is required for this implementation
    if not HETZNER_ROBOT_USER or not HETZNER_ROBOT_PASSWORD:
        logger.error("Robot API credentials required for dedicated server data")
        logger.error("Please set HETZNER_ROBOT_USER and HETZNER_ROBOT_PASSWORD environment variables")
        return []
    
    logger.info("Robot API credentials provided - fetching data...")
    
    try:
        # Fetch server products
        products = fetch_dedicated_server_products()
        if not products:
            logger.warning("Robot API returned no server products")
            return []
            
        processed_servers = process_dedicated_server_products(products)
        all_results.extend(processed_servers)
        logger.info(f"Successfully fetched {len(processed_servers)} dedicated servers via Robot API")
        
        # Fetch addon information for IPv4 pricing context
        addons = fetch_dedicated_server_addons()
        if addons:
            # Find Primary IPv4 addon pricing for reference
            ipv4_addon_price = None
            for addon in addons:
                if "ipv4" in addon.get("name", "").lower() or "primary" in addon.get("name", "").lower():
                    prices = addon.get("price", [])
                    for price_entry in prices:
                        if price_entry.get("currency") == "EUR":
                            ipv4_addon_price = float(price_entry.get("price_monthly", {}).get("net", 0))
                            break
                    if ipv4_addon_price:
                        break
            
            # Add IPv4 addon pricing context to all servers
            if ipv4_addon_price:
                for server in all_results:
                    if server.get("source") == "robot_api_enhanced":
                        server["ipv4_addon_price_eur_monthly"] = ipv4_addon_price
                
                logger.info(f"Added Primary IPv4 addon pricing context: €{ipv4_addon_price}/month")
        
        logger.info(f"Robot API fetch successful: {len(all_results)} servers with comprehensive pricing data")
        return all_results
        
    except Exception as e:
        logger.error(f"Robot API fetch failed: {e}")
        return []
    
    driver = None
    try:
        driver = create_webdriver()
        print("WebDriver created successfully")
        
        # Set page load timeout for CI environments - optimize for performance
        if os.environ.get('CI'):
            driver.set_page_load_timeout(45)  # Reduced timeout for CI efficiency
            print("CI TIMEOUT: Set 45-second page load timeout for faster execution")
        else:
            driver.set_page_load_timeout(60)  # Standard timeout for local development
        
        # Collect both IPv4 and IPv6 pricing data
        ipv4_servers = []
        ipv6_servers = []
        
        # --- PHASE 1: Collect IPv4 pricing data ---
        print("\n=== PHASE 1: Collecting IPv4 pricing data ===")
        
        # Load initial page
        if not wait_for_content_load(driver, start_url):
            print("Failed to load initial page properly")
            return []
        
        # Set items per page to 50 for better performance
        print("Setting items per page to 50...")
        if set_items_per_page(driver, 50):
            print("Successfully set to 50 items per page")
        else:
            print("Warning: Could not set items per page to 50, continuing with default")
        
        # Ensure IPv6 toggle is OFF for IPv4 prices
        toggle_ipv6_mode(driver, enable_ipv6=False)
        
        # Scrape all pages with IPv4 pricing
        page_count = 0
        while page_count < max_pages:  # Handle up to 10 pages for 386 servers with 50 per page
            page_count += 1
            print(f"Processing IPv4 pricing - page {page_count} (50 items per page)")
            
            try:
                page_data = fetch_hetzner_dedicated_page(start_url, driver, ipv6_mode=False)
                ipv4_servers.extend(page_data)
                print(f"PAGE PROGRESS: IPv4 page {page_count} - {len(page_data)} servers | Total so far: {len(ipv4_servers)}")
                
                # Add location parsing validation
                servers_with_location = sum(1 for s in page_data if s.get('location', 'N/A') != 'N/A')
                print(f"LOCATION PARSING: {servers_with_location}/{len(page_data)} servers have valid location data")
                
                # Check for next page using improved pagination detection
                pagination_info = detect_pagination_info(driver)
                if not pagination_info['has_next']:
                    print("No more pages available for IPv4 pricing")
                    break
                
                # Navigate to next page
                if not navigate_to_next_page(driver, pagination_info):
                    print("Failed to navigate to next page")
                    break
                    
            except Exception as e:
                print(f"Error processing IPv4 pricing page {page_count}: {e}")
                break
        
        print(f"Phase 1 complete: {len(ipv4_servers)} servers with IPv4 pricing")
        
        # --- PHASE 2: Collect IPv6 pricing data ---
        print("\n=== PHASE 2: Collecting IPv6 pricing data ===")
        
        # Return to first page for IPv6 pricing
        if not wait_for_content_load(driver, start_url):
            print("Failed to reload page for IPv6 pricing")
        else:
            # Set items per page to 50 again after page reload
            print("Re-setting items per page to 50 for IPv6 mode...")
            if set_items_per_page(driver, 50):
                print("Successfully re-set to 50 items per page for IPv6")
            else:
                print("Warning: Could not re-set items per page to 50 for IPv6")
            
            # Enable IPv6 toggle for IPv6-only prices
            if toggle_ipv6_mode(driver, enable_ipv6=True):
                # Scrape all pages with IPv6 pricing
                page_count = 0
                while page_count < max_pages:  # Handle up to 10 pages for 386 servers with 50 per page
                    page_count += 1
                    print(f"Processing IPv6 pricing - page {page_count} (50 items per page)")
                    
                    try:
                        page_data = fetch_hetzner_dedicated_page(start_url, driver, ipv6_mode=True)
                        ipv6_servers.extend(page_data)
                        print(f"PAGE PROGRESS: IPv6 page {page_count} - {len(page_data)} servers | Total so far: {len(ipv6_servers)}")
                        
                        # Add location parsing validation
                        servers_with_location = sum(1 for s in page_data if s.get('location', 'N/A') != 'N/A')
                        print(f"LOCATION PARSING: {servers_with_location}/{len(page_data)} servers have valid location data")
                        
                        # Check for next page
                        pagination_info = detect_pagination_info(driver)
                        if not pagination_info['has_next']:
                            print("No more pages available for IPv6 pricing")
                            break
                        
                        # Navigate to next page
                        if not navigate_to_next_page(driver, pagination_info):
                            print("Failed to navigate to next page")
                            break
                            
                    except Exception as e:
                        print(f"Error processing IPv6 pricing page {page_count}: {e}")
                        break
                
                print(f"Phase 2 complete: {len(ipv6_servers)} servers with IPv6 pricing")
            else:
                print("Failed to enable IPv6 toggle - skipping IPv6 pricing collection")
        
        # --- PHASE 3: Merge data ---
        print("\n=== PHASE 3: Merging IPv4 and IPv6 data ===")
        print(f"MERGE INPUT: {len(ipv4_servers)} IPv4 servers, {len(ipv6_servers)} IPv6 servers")
        
        if ipv4_servers and ipv6_servers:
            all_results = merge_ipv4_ipv6_data(ipv4_servers, ipv6_servers)
            print(f"MERGE RESULT: {len(all_results)} unique servers with combined pricing")
            
            # Validate merge effectiveness
            servers_with_both_pricing = sum(1 for s in all_results
                                          if s.get('ipv4_pricing_available') and s.get('ipv6_pricing_available'))
            print(f"MERGE VALIDATION: {servers_with_both_pricing} servers have both IPv4 and IPv6 pricing")
            
        elif ipv4_servers:
            all_results = ipv4_servers
            print(f"Using IPv4 data only: {len(all_results)} servers")
        elif ipv6_servers:
            all_results = ipv6_servers
            print(f"Using IPv6 data only: {len(all_results)} servers")
        else:
            print("No server data collected from either IPv4 or IPv6 modes")
            
        print(f"\nFINAL RESULT: {len(all_results)} total servers collected")
        
        # Expected vs actual validation
        expected_servers = 386  # Updated count as mentioned by user
        if len(all_results) < expected_servers * 0.8:  # If less than 80% of expected
            print(f"WARNING: Only collected {len(all_results)} servers, expected ~{expected_servers}")
            print("This suggests the pagination or location parsing fixes may need further adjustment")
        else:
            print(f"SUCCESS: Collected {len(all_results)} servers (expected ~{expected_servers})")
            
    except Exception as e:
        print(f"Failed to create or use WebDriver: {e}")
        print("This could be due to missing Chrome browser or ChromeDriver.")
        if os.environ.get('CI'):
            print("CI-specific troubleshooting:")
            print("1. Verify Chrome dependencies are installed")
            print("2. Check if Chrome binary is accessible")
            print("3. Ensure webdriver-manager can download ChromeDriver")
        else:
            print("Please ensure Chrome is installed and try installing chromedriver manually.")
        
    finally:
        if driver:
            print("Closing WebDriver...")
            try:
                driver.quit()
            except Exception as e:
                print(f"Error closing WebDriver: {e}")
    
    return all_results

if __name__ == "__main__":
    all_hetzner_data = []
    print("--- Fetching Hetzner Cloud Data ---")
    try:
        cloud_data_results = fetch_hetzner_cloud()
        all_hetzner_data.extend(cloud_data_results)
        print(f"Total {len(cloud_data_results)} cloud server types/entries processed.")
    except Exception as e: print(f"Error during Hetzner Cloud data processing: {e.__class__.__name__} - {e}")

    print("\n--- Fetching Hetzner Dedicated Server Data ---")
    try:
        dedicated_data = fetch_hetzner_dedicated()
        all_hetzner_data.extend(dedicated_data)
        print(f"Fetched {len(dedicated_data)} dedicated server types from scraping.")
    except Exception as e: print(f"Error during Hetzner Dedicated data fetching: {e.__class__.__name__} - {e}")

    output_path = "data/hetzner.json"
    if all_hetzner_data:
        with open(output_path, "w", encoding="utf-8") as f: json.dump(all_hetzner_data, f, indent=2, ensure_ascii=False)
        print(f"\nSuccessfully wrote {len(all_hetzner_data)} total Hetzner entries to {output_path}")
    else: print(f"\nNo data fetched for Hetzner. {output_path} not updated.")

    print("\n--- API-Only Configuration ---")
    print("This script now uses APIs exclusively for reliable, fast data collection.")
    print("")
    print("Required Environment Variables:")
    print("1. HETZNER_API_TOKEN - Hetzner Cloud API token (from console.hetzner.cloud)")
    print("   - Get from: Security → API Tokens")
    print("   - Permissions: 'Read' is sufficient")
    print("2. HETZNER_ROBOT_USER - Robot API username (from robot.your-server.de)")
    print("3. HETZNER_ROBOT_PASSWORD - Robot API password")
    print("")
    print("Benefits of API-Only Approach:")
    print("- ✅ No Chrome/Selenium dependencies (faster CI)")
    print("- ✅ More reliable data (direct from Hetzner systems)")
    print("- ✅ Comprehensive coverage (all cloud services + dedicated servers)")
    print("- ✅ IPv4 addon pricing context (shows true total costs)")
    print("- ✅ Faster execution and better error handling")