// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { collectSnapshot, hasRestrictedMarker } from './publication.mjs';
import { documentBundleFromSnapshot, readerPaths } from './build-site-docs.mjs';

// A website is not a source release. Do not recursively copy the working tree.
export const websitePaths = [...new Set([
  'index.html', '.nojekyll', 'site/style.css', 'site/main.js', 'site/documents.js',
  'site/icons/activity.svg', 'site/icons/box.svg', 'site/icons/chart-no-axes-column.svg',
  'site/icons/cloud.svg', 'site/icons/cloud-off.svg', 'site/icons/code.svg', 'site/icons/cpu.svg', 'site/icons/database.svg',
  'site/icons/ellipsis.svg', 'site/icons/file-text.svg', 'site/icons/image.svg',
  'site/icons/list-check.svg', 'site/icons/locate-fixed.svg', 'site/icons/laptop.svg', 'site/icons/memory-stick.svg',
  'site/icons/network.svg', 'site/icons/route.svg', 'site/icons/search.svg', 'site/icons/server.svg',
  'site/icons/settings.svg', 'site/icons/share-2.svg', 'site/icons/shield-check.svg',
  'site/icons/shield.svg', 'site/icons/workflow.svg',
  'site/vendor/marked.js', 'site/vendor/purify.js',
  'assets/ecOS-ystem_logo.png', 'assets/ecOS-ystem_square_logo.png',
  'assets/local-compute-hero.png', 'NOTICE', ...readerPaths,
])].sort();

export function scanWebsiteText(name, text) {
  if (hasRestrictedMarker(text)) throw new Error(`Sensitive-content check failed (private strategy marker): ${name}`);
  const checks = [
    ['restricted classification', /Classification\s*:\s*(?:INTERNAL|PRIVATE|CONFIDENTIAL|RESTRICTED)\b/i],
    ['publication prohibition', /^\s*(?:#+\s*)?(?:DO NOT PUBLISH|DO NOT DISTRIBUTE|CONFIDENTIAL)\s*[.!]?\s*$/im],
    ['local machine path', /\/Users\/|\/home\/[^\s/]+\/|app:\/\//],
    ['private key', /-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/],
    ['GitHub token', /\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b/],
    ['cloud access key', /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/],
    ['API token', /\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|xox[baprs]-[A-Za-z0-9-]{20,})\b/],
    ['credential assignment', /\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*["'][^"'\s]{12,}["']/i],
    ['credential-bearing URL', /https?:\/\/[^\s/:]+:[^\s/@]+@/i],
  ];
  for (const [label, pattern] of checks) {
    if (pattern.test(text)) throw new Error(`Sensitive-content check failed (${label}): ${name}`);
  }
}

export function websiteSnapshot(root) {
  // Includes path, explicit-deny, symlink, artwork hash and document-link checks.
  const approved = collectSnapshot(root);
  const files = websitePaths.map(name => {
    const entry = approved.files.find(file => file.path === name);
    if (!entry) throw new Error(`Website dependency is not approved in PUBLICATION.json: ${name}`);
    return { ...entry };
  });
  const bundle = files.find(file => file.path === 'site/documents.js');
  // Build from the checked document bytes, not a potentially stale embedded bundle.
  // Its link inventory contains only archive members, so no reader link targets absent source files.
  bundle.data = Buffer.from(documentBundleFromSnapshot({ ...approved, files }));
  for (const file of files) {
    if (!file.path.endsWith('.png')) scanWebsiteText(file.path, file.data.toString('utf8'));
    file.sha256 = crypto.createHash('sha256').update(file.data).digest('hex');
  }
  return { ...approved, files };
}

export function packageWebsite(root, destination) {
  if (!path.isAbsolute(destination) || path.extname(destination).toLowerCase() !== '.zip') {
    throw new Error('Destination must be an absolute .zip path');
  }
  if (fs.existsSync(destination)) throw new Error('Destination already exists; choose a new archive name');
  const snapshot = websiteSnapshot(root);
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'ecos-website-zip-'));
  try {
    const stage = path.join(scratch, 'site');
    fs.mkdirSync(stage);
    for (const file of snapshot.files) {
      const output = path.join(stage, file.path);
      fs.mkdirSync(path.dirname(output), { recursive: true });
      fs.writeFileSync(output, file.data, { flag: 'wx', mode: 0o644 });
      fs.utimesSync(output, new Date('2000-01-01T00:00:00Z'), new Date('2000-01-01T00:00:00Z'));
    }
    const archive = path.join(scratch, 'website.zip');
    execFileSync('zip', ['-q', '-X', '-9', archive, '-@'], {
      cwd: stage, input: snapshot.files.map(file => file.path).join('\n') + '\n', timeout: 60000,
    });
    execFileSync('unzip', ['-tqq', archive], { timeout: 60000 });
    const entries = execFileSync('unzip', ['-Z1', archive], { encoding: 'utf8' }).trim().split('\n').sort();
    if (JSON.stringify(entries) !== JSON.stringify([...websitePaths].sort())) {
      throw new Error('ZIP inventory differs from approved website selection');
    }
    const bytes = fs.readFileSync(archive);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.writeFileSync(destination, bytes, { flag: 'wx', mode: 0o600 });
    return { archive: destination, files: entries.length, documents: readerPaths.length,
      bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
      note: 'Approved website files only; sensitive-content checks passed. Human content/rights review remains required. No upload performed.' };
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  try {
    const [argument, ...extra] = process.argv.slice(2);
    if (extra.length) throw new Error('Usage: node scripts/package-website.mjs [output.zip | --check]');
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
    if (argument === '--check') {
      const snapshot = websiteSnapshot(root);
      console.log(JSON.stringify({ files: snapshot.files.map(file => file.path),
        note: 'Allowlist, explicit-deny and sensitive-content checks passed; no archive written.' }, null, 2));
    } else {
      console.log(JSON.stringify(packageWebsite(root, path.resolve(argument ?? path.join(root, 'dist/ecos-github-pages.zip'))), null, 2));
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
