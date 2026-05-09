"""
Azure service categorization and mapping utilities.
Maps Azure services to standardized categories for CloudPriceFinder.
"""

# Azure service name to category mapping
AZURE_SERVICE_CATEGORIES = {
    # SERVERS (Virtual Machines and Compute)
    'Virtual Machines': 'servers',
    'Azure Virtual Machines': 'servers',
    'Virtual Machine Scale Sets': 'servers',
    'Azure Dedicated Host': 'servers',
    'Azure Spot Virtual Machines': 'servers',
    'Container Instances': 'servers',
    'Azure Container Registry': 'servers',
    'Azure Kubernetes Service': 'servers',
    'App Service': 'servers',
    'Azure Functions': 'servers',
    'Logic Apps': 'servers',
    'Service Fabric': 'servers',
    'Batch': 'servers',
    'Azure VMware Solution': 'servers',
    'Azure Arc': 'servers',
    
    # NETWORKING (Load Balancers and Network Services)
    'Load Balancer': 'networking',
    'Azure Load Balancer': 'networking',
    'Application Gateway': 'networking',
    'Azure Application Gateway': 'networking',
    'Traffic Manager': 'networking',
    'Azure Traffic Manager': 'networking',
    'Front Door': 'networking',
    'Azure Front Door': 'networking',
    'Azure CDN': 'networking',
    'Content Delivery Network': 'networking',
    'Azure Firewall': 'networking',
    'VPN Gateway': 'networking',
    'Azure VPN Gateway': 'networking',
    'ExpressRoute': 'networking',
    'Azure ExpressRoute': 'networking',
    'Azure DDoS Protection': 'networking',
    'Azure Bastion': 'networking',
    'Virtual Network': 'networking',
    'Azure Virtual Network': 'networking',
    'Azure DNS': 'networking',
    'Private Link': 'networking',
    'Azure Private Link': 'networking',
    
    # DATABASES (SQL and NoSQL databases)
    'SQL Database': 'databases',
    'Azure SQL Database': 'databases',
    'SQL Managed Instance': 'databases',
    'Azure SQL Managed Instance': 'databases',
    'Azure Database for PostgreSQL': 'databases',
    'Azure Database for MySQL': 'databases',
    'Azure Database for MariaDB': 'databases',
    'Azure Cosmos DB': 'databases',
    'Cosmos DB': 'databases',
    'Azure Synapse Analytics': 'analytics',
    'SQL Data Warehouse': 'databases',
    'Azure Data Factory': 'analytics',
    'Azure Databricks': 'analytics',
    'Azure Analysis Services': 'databases',
    'Azure Data Lake Storage': 'storage',
    'Azure Data Lake Analytics': 'analytics',
    'Azure Search': 'databases',
    'Azure Cognitive Search': 'databases',
    'SQL Server Stretch Database': 'databases',
    
    # CACHE (Cache and In-Memory databases)
    'Azure Cache for Redis': 'cache',
    'Redis Cache': 'cache',
    
    # STORAGE (Object storage and file systems)
    'Azure Table Storage': 'storage',
    'Azure Blob Storage': 'storage', 
    'Azure Files': 'storage',
    'Azure Queue Storage': 'storage',
    'Storage': 'storage',
    'Azure Storage': 'storage',
    'Backup': 'storage',
    'Azure Backup': 'storage',
    'Site Recovery': 'storage',
    'Azure Site Recovery': 'storage',
    
    # STORAGE (Additional storage services that don't fit databases)
    'Azure NetApp Files': 'storage',
    'Azure HPC Cache': 'storage',
    'Azure FXT Edge Filer': 'storage',
    'StorSimple': 'storage',
    
    # AI/ML (Artificial Intelligence and Machine Learning)
    'Cognitive Services': 'ai-ml',
    'Azure Cognitive Services': 'ai-ml',
    'Azure Machine Learning': 'ai-ml',
    'Machine Learning': 'ai-ml',
    'Azure Bot Service': 'ai-ml',
    'Azure Form Recognizer': 'ai-ml',
    'Azure Computer Vision': 'ai-ml',
    'Azure Speech Services': 'ai-ml',
    'Azure Language Understanding': 'ai-ml',
    'Azure Translator': 'ai-ml',
    
    # ANALYTICS (Business Intelligence and Analytics)
    'Azure Stream Analytics': 'analytics',
    'Stream Analytics': 'analytics',
    'Azure Event Hubs': 'analytics',
    'Event Hubs': 'analytics',
    'Azure Event Grid': 'analytics',
    'Event Grid': 'analytics',
    'Azure Service Bus': 'analytics',
    'Service Bus': 'analytics',
    'Azure IoT Hub': 'analytics',
    'IoT Hub': 'analytics',
    'Azure IoT Central': 'analytics',
    'IoT Central': 'analytics',
    'Azure Time Series Insights': 'analytics',
    'Power BI': 'analytics',
    'Power BI Embedded': 'analytics',
    
    # SECURITY (Security and Identity)
    'Azure Active Directory': 'security',
    'Active Directory': 'security',
    'Azure Key Vault': 'security',
    'Key Vault': 'security',
    'Azure Security Center': 'security',
    'Security Center': 'security',
    'Azure Sentinel': 'security',
    'Azure Information Protection': 'security',
    'Azure Multi-Factor Authentication': 'security',
    
    # MONITORING (Monitoring and Management)
    'Azure Monitor': 'monitoring',
    'Monitor': 'monitoring',
    'Application Insights': 'monitoring',
    'Azure Application Insights': 'monitoring',
    'Log Analytics': 'monitoring',
    'Azure Log Analytics': 'monitoring',
    'Azure Automation': 'monitoring',
    'Automation': 'monitoring',
    'Azure Policy': 'monitoring',
    'Azure Resource Manager': 'monitoring',
    
    # INTEGRATION (Integration Services)
    'Azure API Management': 'integration',
    'API Management': 'integration',
    'Azure Logic Apps': 'integration',
    'Azure Service Fabric': 'integration',
    'Azure Relay': 'integration',
    'Azure Notification Hubs': 'integration',
    
    # MEDIA (Media and Content Delivery)
    'Media Services': 'media',
    'Azure Media Services': 'media',
    'Azure Content Moderator': 'media',
    'Azure Video Indexer': 'media',
    
    # DEVELOPMENT (Developer Tools)
    'Azure DevOps': 'development',
    'DevOps': 'development',
    'Azure DevTest Labs': 'development',
    'DevTest Labs': 'development',
    'Azure Repos': 'development',
    'Azure Pipelines': 'development',
    'Azure Artifacts': 'development',
    'Azure Test Plans': 'development',
    
    # MIGRATION (Migration Services)
    'Azure Migrate': 'migration',
    'Migrate': 'migration',
    'Azure Database Migration Service': 'migration',
    'Azure Import/Export': 'migration',
    'Azure Data Box': 'migration',
}

def get_service_category(service_name: str) -> str:
    """
    Get the category for an Azure service name.
    
    Args:
        service_name: Azure service name from the API
        
    Returns:
        Category string (servers, loadbalancers, databases, etc.)
    """
    if not service_name:
        return 'other'
    
    # Direct mapping lookup
    if service_name in AZURE_SERVICE_CATEGORIES:
        return AZURE_SERVICE_CATEGORIES[service_name]
    
    # Fallback pattern matching for partial matches
    service_lower = service_name.lower()
    
    # Check for key terms in service names
    if any(term in service_lower for term in ['virtual machine', 'vm', 'compute', 'container', 'app service', 'function']):
        return 'servers'
    elif any(term in service_lower for term in ['load balancer', 'application gateway', 'traffic manager', 'cdn', 'firewall', 'vpn', 'network', 'dns']):
        return 'networking'
    elif any(term in service_lower for term in ['redis cache', 'cache for redis']):
        return 'cache'
    elif any(term in service_lower for term in ['sql', 'database', 'cosmos', 'synapse']):
        return 'databases'
    elif any(term in service_lower for term in ['storage', 'blob', 'file', 'table', 'queue', 'backup']):
        return 'storage'
    elif any(term in service_lower for term in ['cognitive', 'machine learning', 'ml', 'ai', 'bot', 'speech', 'vision', 'form recognizer']):
        return 'ai-ml'
    elif any(term in service_lower for term in ['stream analytics', 'event hub', 'event grid', 'service bus', 'iot', 'power bi']):
        return 'analytics'
    elif any(term in service_lower for term in ['active directory', 'key vault', 'security', 'sentinel', 'authentication']):
        return 'security'
    elif any(term in service_lower for term in ['monitor', 'insights', 'log analytics', 'automation', 'policy']):
        return 'monitoring'
    elif any(term in service_lower for term in ['api management', 'logic apps', 'service fabric', 'relay', 'notification']):
        return 'integration'
    elif any(term in service_lower for term in ['media services', 'content moderator', 'video indexer']):
        return 'media'
    elif any(term in service_lower for term in ['devops', 'devtest', 'repos', 'pipelines', 'artifacts']):
        return 'development'
    elif any(term in service_lower for term in ['migrate', 'migration', 'import', 'export', 'data box']):
        return 'migration'
    else:
        return 'other'

def get_service_type_from_category(category: str) -> str:
    """
    Map category to CloudPriceFinder service type.
    
    Args:
        category: Service category from get_service_category()
        
    Returns:
        CloudPriceFinder service type
    """
    category_to_type = {
        'servers': 'cloud-server',
        'networking': 'cloud-loadbalancer',  # Keep as cloud-loadbalancer for compatibility
        'databases': 'cloud-database',
        'cache': 'cloud-cache',
        'storage': 'cloud-storage',
        'ai-ml': 'cloud-ai-ml',
        'analytics': 'cloud-analytics',
        'security': 'cloud-security',
        'monitoring': 'cloud-monitoring',
        'integration': 'cloud-integration',
        'media': 'cloud-media',
        'development': 'cloud-development',
        'migration': 'cloud-migration',
        'other': 'cloud-other'
    }
    return category_to_type.get(category, 'cloud-other')

def is_virtual_machine_service(service_name: str, meter_name: str = '') -> bool:
    """
    Check if a service is specifically a Virtual Machine service.
    
    Args:
        service_name: Azure service name
        meter_name: Azure meter name
        
    Returns:
        True if this is a VM service
    """
    vm_indicators = [
        'Virtual Machines',
        'Azure Virtual Machines', 
        'Virtual Machine Scale Sets',
        'Azure Dedicated Host',
        'Azure Spot Virtual Machines'
    ]
    
    if service_name in vm_indicators:
        return True
    
    # Check meter name for VM indicators
    if meter_name and any(term in meter_name.lower() for term in ['virtual machine', 'vm ', 'dedicated host']):
        return True
        
    return False

def is_dedicated_host_service(service_name: str, meter_name: str = '') -> bool:
    """
    Check if a service is specifically a Dedicated Host service.
    
    Args:
        service_name: Azure service name
        meter_name: Azure meter name
        
    Returns:
        True if this is a dedicated host service
    """
    dedicated_indicators = [
        'Azure Dedicated Host',
        'Dedicated Host'
    ]
    
    if service_name in dedicated_indicators:
        return True
    
    # Check meter name for dedicated host indicators
    if meter_name and any(term in meter_name.lower() for term in ['dedicated host', 'dedicated compute']):
        return True
        
    return False

def get_china_region_mapping() -> dict:
    """
    Get mapping for Azure China regions.
    
    Returns:
        Dictionary mapping Azure China regions to countries
    """
    return {
        'chinaeast': 'China',
        'chinaeast2': 'China', 
        'chinanorth': 'China',
        'chinanorth2': 'China',
        'chinanorth3': 'China',
        'chinawest': 'China',
        'chinawest2': 'China',
        'china': 'China'
    }