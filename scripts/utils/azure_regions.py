"""
Azure region to country mapping utilities.
Maps Azure region codes to proper country names for flag display.
"""

# Comprehensive Azure region to country mapping
AZURE_REGION_MAPPING = {
    # United States
    'eastus': 'United States',
    'eastus2': 'United States', 
    'southcentralus': 'United States',
    'westus2': 'United States',
    'westus3': 'United States',
    'australiaeast': 'Australia',
    'southeastasia': 'Singapore',
    'northeurope': 'Ireland',
    'swedencentral': 'Sweden',
    'uksouth': 'United Kingdom',
    'westeurope': 'Netherlands',
    'centralus': 'United States',
    'southafricanorth': 'South Africa',
    'centralindia': 'India',
    'eastasia': 'Hong Kong',
    'japaneast': 'Japan',
    'koreacentral': 'South Korea',
    'canadacentral': 'Canada',
    'francecentral': 'France',
    'germanywestcentral': 'Germany',
    'norwayeast': 'Norway',
    'switzerlandnorth': 'Switzerland',
    'uaenorth': 'United Arab Emirates',
    'brazilsouth': 'Brazil',
    'centraluseuap': 'United States',
    'eastus2euap': 'United States',
    'qatarcentral': 'Qatar',
    'centralusstage': 'United States',
    'eastusstage': 'United States',
    'eastus2stage': 'United States',
    'northcentralusstage': 'United States',
    'southcentralusstage': 'United States',
    'westusstage': 'United States',
    'westus2stage': 'United States',
    'asia': 'Singapore',  # Regional grouping
    'asiapacific': 'Singapore',  # Regional grouping
    'australia': 'Australia',  # Regional grouping
    'brazil': 'Brazil',  # Regional grouping
    'canada': 'Canada',  # Regional grouping
    'europe': 'Ireland',  # Regional grouping - default to Ireland
    'france': 'France',  # Regional grouping
    'germany': 'Germany',  # Regional grouping
    'global': 'Global',  # Global services
    'india': 'India',  # Regional grouping
    'japan': 'Japan',  # Regional grouping
    'korea': 'South Korea',  # Regional grouping
    'norway': 'Norway',  # Regional grouping
    'southafrica': 'South Africa',  # Regional grouping
    'switzerland': 'Switzerland',  # Regional grouping
    'uae': 'United Arab Emirates',  # Regional grouping
    'uk': 'United Kingdom',  # Regional grouping
    'unitedstates': 'United States',  # Regional grouping
    'unitedstateseuap': 'United States',  # Regional grouping
    
    # More specific regions
    'australiacentral': 'Australia',
    'australiacentral2': 'Australia',
    'australiasoutheast': 'Australia',
    'brazilsoutheast': 'Brazil',
    'canadaeast': 'Canada',
    'chinaeast': 'China',
    'chinaeast2': 'China',
    'chinanorth': 'China',
    'chinanorth2': 'China',
    'chinanorth3': 'China',
    'eastusg': 'United States',  # US Government
    'southcentralusg': 'United States',  # US Government
    'westcentralus': 'United States',
    'westus': 'United States',
    'francecentral': 'France',
    'francesouth': 'France',
    'germanynorth': 'Germany',
    'japanwest': 'Japan',
    'jioindiacentral': 'India',
    'jioindiawest': 'India',
    'koreasouth': 'South Korea',
    'northcentralus': 'United States',
    'norwaywest': 'Norway',
    'southafricawest': 'South Africa',
    'southindia': 'India',
    'switzerlandwest': 'Switzerland',
    'uaecentral': 'United Arab Emirates',
    'ukwest': 'United Kingdom',
    'westindia': 'India',
    'westus2': 'United States',
    
    # New regions and specific mappings from the data
    'indonesiacentral': 'Indonesia',
    'spaincentral': 'Spain',
    'italynorth': 'Italy',
    'israelnorth': 'Israel',
    'israelnorthwest': 'Israel',
    'mexicocentral': 'Mexico',
    'polandcentral': 'Poland',
    'taiwannorth': 'Taiwan',
    'swedencentral': 'Sweden',
    'denmarkeast': 'Denmark',
    'finlandcentral': 'Finland',
    'austriaeast': 'Austria',
    'belgiumcentral': 'Belgium',
    'chilecentral': 'Chile',
    'colombiacentral': 'Colombia',
    'malaysiasouth': 'Malaysia',
    'newzealandnorth': 'New Zealand',
    'saudi­arabiacentral': 'Saudi Arabia',
    'saudiarabiacentral': 'Saudi Arabia',
    'singaporecentral': 'Singapore',
    'southkoreacentral': 'South Korea',
    'southkorea­south': 'South Korea',
    'southkoreasouth': 'South Korea',
    'vietnamcentral': 'Vietnam',
    'moldovacentral': 'Moldova',
    'greececentral': 'Greece',
    
    # AT&T and other US regions that were missed
    'atlanta1': 'United States',
    'attatlanta1': 'United States',
    'attdallas1': 'United States',
    'attdetroit1': 'United States',
    'attnewyork1': 'United States',
    'attchicago1': 'United States',
    'attlosangeles1': 'United States',
    'attmiami1': 'United States',
    'attseattle1': 'United States',
    'attsilicon1': 'United States',
    'attwashington1': 'United States',
    
    # Global and special regions
    'global': 'Global',
    'worldwide': 'Global',
}

def get_country_from_azure_region(region_code: str) -> str:
    """
    Map Azure region code to country name.
    
    Args:
        region_code: Azure region code (e.g., 'westeurope', 'eastus')
        
    Returns:
        Country name or 'Unknown' if not found
    """
    if not region_code:
        return 'Unknown'
    
    # Convert to lowercase for consistent matching
    region_lower = region_code.lower().strip()
    
    # Direct mapping lookup
    if region_lower in AZURE_REGION_MAPPING:
        return AZURE_REGION_MAPPING[region_lower]
    
    # Fallback patterns for unknown regions
    region_patterns = {
        'us': 'United States',
        'europe': 'Europe',
        'asia': 'Asia',
        'australia': 'Australia',
        'canada': 'Canada',
        'brazil': 'Brazil',
        'japan': 'Japan',
        'korea': 'South Korea',
        'india': 'India',
        'china': 'China',
        'uk': 'United Kingdom',
        'france': 'France',
        'germany': 'Germany',
        'norway': 'Norway',
        'sweden': 'Sweden',
        'switzerland': 'Switzerland',
        'africa': 'South Africa',
        'uae': 'United Arab Emirates',
        'israel': 'Israel',
        'spain': 'Spain',
        'italy': 'Italy',
        'mexico': 'Mexico',
        'poland': 'Poland',
        'taiwan': 'Taiwan',
        'denmark': 'Denmark',
        'finland': 'Finland',
        'austria': 'Austria',
        'belgium': 'Belgium',
        'chile': 'Chile',
        'colombia': 'Colombia',
        'malaysia': 'Malaysia',
        'newzealand': 'New Zealand',
        'singapore': 'Singapore',
        'vietnam': 'Vietnam',
        'moldova': 'Moldova',
        'greece': 'Greece',
        'indonesia': 'Indonesia',
        'saudi': 'Saudi Arabia',
    }
    
    # Try pattern matching
    for pattern, country in region_patterns.items():
        if pattern in region_lower:
            return country
    
    # Last resort: return the region code capitalized
    return region_code.title()

def get_all_azure_countries() -> list[str]:
    """Get all unique countries from Azure regions."""
    return sorted(list(set(AZURE_REGION_MAPPING.values())))

def get_regions_by_country(country: str) -> list[str]:
    """Get all Azure regions for a specific country."""
    return [region for region, mapped_country in AZURE_REGION_MAPPING.items() 
            if mapped_country == country]

# Country name to ISO code mapping for flags
COUNTRY_TO_ISO_CODE = {
    'Afghanistan': 'AF',
    'Albania': 'AL',
    'Algeria': 'DZ',
    'Argentina': 'AR',
    'Armenia': 'AM',
    'Australia': 'AU',
    'Austria': 'AT',
    'Azerbaijan': 'AZ',
    'Bangladesh': 'BD',
    'Belarus': 'BY',
    'Belgium': 'BE',
    'Bosnia and Herzegovina': 'BA',
    'Brazil': 'BR',
    'Bulgaria': 'BG',
    'Canada': 'CA',
    'Chile': 'CL',
    'China': 'CN',
    'Colombia': 'CO',
    'Croatia': 'HR',
    'Czech Republic': 'CZ',
    'Denmark': 'DK',
    'Egypt': 'EG',
    'Estonia': 'EE',
    'Finland': 'FI',
    'France': 'FR',
    'Germany': 'DE',
    'Greece': 'GR',
    'Hong Kong': 'HK',
    'Hungary': 'HU',
    'Iceland': 'IS',
    'India': 'IN',
    'Indonesia': 'ID',
    'Ireland': 'IE',
    'Israel': 'IL',
    'Italy': 'IT',
    'Japan': 'JP',
    'Kazakhstan': 'KZ',
    'Latvia': 'LV',
    'Lithuania': 'LT',
    'Luxembourg': 'LU',
    'Malaysia': 'MY',
    'Mexico': 'MX',
    'Moldova': 'MD',
    'Netherlands': 'NL',
    'New Zealand': 'NZ',
    'Norway': 'NO',
    'Poland': 'PL',
    'Portugal': 'PT',
    'Qatar': 'QA',
    'Romania': 'RO',
    'Russia': 'RU',
    'Saudi Arabia': 'SA',
    'Singapore': 'SG',
    'Slovakia': 'SK',
    'Slovenia': 'SI',
    'South Africa': 'ZA',
    'South Korea': 'KR',
    'Spain': 'ES',
    'Sweden': 'SE',
    'Switzerland': 'CH',
    'Taiwan': 'TW',
    'Thailand': 'TH',
    'Turkey': 'TR',
    'Ukraine': 'UA',
    'United Arab Emirates': 'AE',
    'United Kingdom': 'GB',
    'United States': 'US',
    'Vietnam': 'VN',
    # Add some fallbacks for regions/groupings
    'Europe': 'EU',
    'Asia': 'AS', 
    'Global': 'GLOBAL',  # Special code for global services
}

def get_country_code(country_name: str) -> str:
    """Get ISO country code for flag display."""
    return COUNTRY_TO_ISO_CODE.get(country_name, 'GLOBAL')  # Special code as fallback

def create_location_detail(azure_region: str, country: str) -> dict:
    """Create locationDetail object compatible with frontend."""
    return {
        'code': azure_region,
        'city': azure_region.replace('central', '').replace('north', '').replace('south', '').replace('east', '').replace('west', '').title(),
        'country': country,
        'countryCode': get_country_code(country),
        'region': azure_region
    }