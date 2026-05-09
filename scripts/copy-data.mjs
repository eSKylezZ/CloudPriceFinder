/**
 * copy-data.mjs
 * Postbuild step that runs after `astro build`. It:
 *   1. Copies data/ into dist/data/ (excluding raw provider files)
 *   2. Copies _headers and _redirects from repo root into dist/
 *
 * Why not public/data/?  Keeping data/ at repo root means CI tooling
 * (orchestrator, aggregator, validator) can all use relative paths without
 * knowing about Astro's public/ directory. We copy at postbuild instead.
 *
 * Usage: node scripts/copy-data.mjs [--src data] [--dest dist/data]
 */

import { cpSync, existsSync, mkdirSync, readdirSync, statSync } from 'fs';
import { join, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const repoRoot = resolve(__dirname, '..');

// Parse optional CLI overrides
const args = process.argv.slice(2);
const getArg = (flag) => {
  const idx = args.indexOf(flag);
  return idx !== -1 ? args[idx + 1] : null;
};

const srcDir = resolve(repoRoot, getArg('--src') ?? 'data');
const destDir = resolve(repoRoot, getArg('--dest') ?? join('dist', 'data'));
const distDir = resolve(repoRoot, 'dist');

// Skip entries that should not be published:
//   - providers/  (raw .json files — too large, not consumed by frontend)
//   - providers/_archive/  (v2 secondary-provider data)
const SKIP_NAMES = new Set(['providers']);

function copyFiltered(src, dest) {
  if (!existsSync(src)) {
    console.warn(`[copy-data] Source not found, skipping: ${src}`);
    return;
  }

  mkdirSync(dest, { recursive: true });

  for (const entry of readdirSync(src)) {
    if (SKIP_NAMES.has(entry)) continue;

    const srcPath = join(src, entry);
    const destPath = join(dest, entry);
    const stat = statSync(srcPath);

    if (stat.isDirectory()) {
      copyFiltered(srcPath, destPath);
    } else {
      cpSync(srcPath, destPath);
    }
  }
}

// 1. Copy data/ → dist/data/
console.log(`[copy-data] Copying data files: ${srcDir} → ${destDir}`);
copyFiltered(srcDir, destDir);

// 2. Copy Cloudflare Pages config files from repo root → dist/
//    Astro does not copy files from repo root automatically, but Cloudflare
//    Pages requires _headers and _redirects to be in the build output directory.
const cfFiles = ['_headers', '_redirects'];
for (const file of cfFiles) {
  const src = resolve(repoRoot, file);
  const dest = resolve(distDir, file);
  if (existsSync(src)) {
    cpSync(src, dest);
    console.log(`[copy-data] Copied ${file} → dist/${file}`);
  } else {
    console.warn(`[copy-data] ${file} not found at repo root, skipping`);
  }
}

console.log('[copy-data] Done.');
