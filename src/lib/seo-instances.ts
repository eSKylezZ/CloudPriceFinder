import { existsSync, readFileSync, readdirSync } from 'fs';
import { join } from 'path';
import type { V3Instance } from '../types/index';

const FAMILIES_DIR = join(process.cwd(), 'data', 'families');

export function loadProviderInstances(dataId: string): V3Instance[] {
  const dir = join(FAMILIES_DIR, dataId);
  if (!existsSync(dir)) return [];

  const instances: V3Instance[] = [];
  for (const file of readdirSync(dir)) {
    if (!file.endsWith('.json')) continue;
    const arr = JSON.parse(readFileSync(join(dir, file), 'utf-8')) as V3Instance[];
    for (const inst of arr) {
      if (inst.type === 'cloud-server' && inst.priceUSD_hourly > 0) {
        instances.push(inst);
      }
    }
  }
  return instances;
}

export function loadAllInstances(): V3Instance[] {
  return ['aws', 'azure', 'gcp', 'oci'].flatMap(loadProviderInstances);
}

export function fmtPrice(price: number): string {
  return `$${price.toFixed(4)}/hr`;
}
