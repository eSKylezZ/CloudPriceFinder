"""
Data validation utilities for CloudCosts.
"""

from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = [
    'provider',
    'type', 
    'instanceType',
    'priceUSD_hourly',
    'lastUpdated'
]

VALID_PROVIDERS = ['aws', 'azure', 'gcp', 'hetzner', 'oci', 'ovh']
VALID_TYPES = [
    'cloud-server',
    'cloud-loadbalancer', 
    'cloud-volume',
    'cloud-network',
    'cloud-floating-ip',
    'cloud-snapshot',
    'cloud-certificate',
    'dedicated-server',
    'dedicated-auction',
    'dedicated-storage',
    'dedicated-colocation'
]

def validate_instance_data(instance: Dict[str, Any]) -> bool:
    """
    Validate a single cloud instance data structure.
    
    Args:
        instance: Dictionary containing instance data
        
    Returns:
        bool: True if valid, False otherwise
    """
    try:
        # Check required fields
        for field in REQUIRED_FIELDS:
            if field not in instance:
                logger.error(f"Missing required field: {field}")
                return False
        
        # Validate provider
        if instance['provider'] not in VALID_PROVIDERS:
            logger.error(f"Invalid provider: {instance['provider']}")
            return False
        
        # Validate type
        if instance['type'] not in VALID_TYPES:
            logger.error(f"Invalid type: {instance['type']}")
            return False
        
        # Validate numeric fields (optional for some service types)
        if 'vCPU' in instance and instance['vCPU'] is not None:
            if not isinstance(instance['vCPU'], (int, float)) or instance['vCPU'] <= 0:
                logger.error(f"Invalid vCPU: {instance['vCPU']}")
                return False
        
        if 'memoryGiB' in instance and instance['memoryGiB'] is not None:
            if not isinstance(instance['memoryGiB'], (int, float)) or instance['memoryGiB'] <= 0:
                logger.error(f"Invalid memoryGiB: {instance['memoryGiB']}")
                return False
        
        if not isinstance(instance['priceUSD_hourly'], (int, float)) or instance['priceUSD_hourly'] < 0:
            logger.error(f"Invalid priceUSD_hourly: {instance['priceUSD_hourly']}")
            return False
        
        # Validate string fields are not empty
        if not instance['instanceType'].strip():
            logger.error("instanceType cannot be empty")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return False

def validate_dataset(instances: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
    """
    Validate a complete dataset and return valid instances and errors.
    
    Args:
        instances: List of instance dictionaries
        
    Returns:
        tuple: (valid_instances, error_messages)
    """
    valid_instances = []
    errors = []
    
    for i, instance in enumerate(instances):
        if validate_instance_data(instance):
            valid_instances.append(instance)
        else:
            errors.append(f"Instance {i}: Invalid data for {instance.get('instanceType', 'unknown')}")
    
    return valid_instances, errors