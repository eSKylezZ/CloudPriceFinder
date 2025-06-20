/**
 * Dynamic data loading utilities for client-side provider data loading.
 * Enables loading only selected provider data on demand.
 */

import type { CloudInstance } from '../types';

interface ProviderCache {
  [provider: string]: CloudInstance[];
}

class DynamicDataLoader {
  private cache: ProviderCache = {};
  private loadingPromises: { [provider: string]: Promise<CloudInstance[]> } = {};
  private availableProviders: string[] = [];
  
  constructor() {
    this.initializeAvailableProviders();
  }
  
  private async initializeAvailableProviders() {
    try {
      // Load summary to get available providers
      const response = await fetch('/data/summary.json');
      if (response.ok) {
        const summary = await response.json();
        if (summary.providerFiles) {
          this.availableProviders = Object.keys(summary.providerFiles);
        }
      }
    } catch (error) {
      console.error('Failed to initialize available providers:', error);
    }
  }
  
  /**
   * Load data for specific providers
   */
  async loadProviders(providers: string[]): Promise<CloudInstance[]> {
    const loadPromises = providers.map(provider => this.loadProvider(provider));
    const results = await Promise.all(loadPromises);
    
    // Flatten results
    return results.flat();
  }
  
  /**
   * Load data for a single provider
   */
  async loadProvider(provider: string): Promise<CloudInstance[]> {
    // Return cached data if available
    if (this.cache[provider]) {
      return this.cache[provider];
    }
    
    // Return existing promise if already loading
    const existingPromise = this.loadingPromises[provider];
    if (existingPromise) {
      return await existingPromise;
    }
    
    // Start loading
    this.loadingPromises[provider] = this.fetchProviderData(provider);
    
    try {
      const data = await this.loadingPromises[provider];
      this.cache[provider] = data;
      delete this.loadingPromises[provider];
      return data;
    } catch (error) {
      delete this.loadingPromises[provider];
      throw error;
    }
  }
  
  /**
   * Fetch provider data from server
   */
  private async fetchProviderData(provider: string): Promise<CloudInstance[]> {
    try {
      const response = await fetch(`/data/providers/${provider}.json`);
      if (!response.ok) {
        throw new Error(`Failed to load ${provider} data: ${response.status}`);
      }
      
      const data = await response.json();
      console.log(`✅ Dynamically loaded ${data.length} instances from ${provider}`);
      return data;
    } catch (error) {
      console.error(`Failed to load ${provider} data:`, error);
      return [];
    }
  }
  
  /**
   * Get cached providers
   */
  getCachedProviders(): string[] {
    return Object.keys(this.cache);
  }
  
  /**
   * Clear cache for specific providers
   */
  clearCache(providers?: string[]) {
    if (providers) {
      providers.forEach(provider => {
        delete this.cache[provider];
        delete this.loadingPromises[provider];
      });
    } else {
      this.cache = {};
      this.loadingPromises = {};
    }
  }
  
  /**
   * Get available providers
   */
  getAvailableProviders(): string[] {
    return this.availableProviders;
  }
  
  /**
   * Check if provider is currently loading
   */
  isLoading(provider: string): boolean {
    return !!this.loadingPromises[provider];
  }
  
  /**
   * Get cache statistics
   */
  getCacheStats() {
    return {
      cached: Object.keys(this.cache),
      loading: Object.keys(this.loadingPromises),
      available: this.availableProviders,
      cacheSize: Object.values(this.cache).reduce((sum, data) => sum + data.length, 0)
    };
  }
}

// Create global instance
export const dynamicLoader = new DynamicDataLoader();

// Export utility functions
export async function loadSelectedProviders(providers: string[]): Promise<CloudInstance[]> {
  return dynamicLoader.loadProviders(providers);
}

export function getCachedProviders(): string[] {
  return dynamicLoader.getCachedProviders();
}

export function clearProviderCache(providers?: string[]) {
  dynamicLoader.clearCache(providers);
}

export function getLoaderStats() {
  return dynamicLoader.getCacheStats();
}