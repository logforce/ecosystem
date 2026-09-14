// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { collectSnapshot, exportSnapshot } from './publication.mjs';
import { documentBundle } from './build-site-docs.mjs';

try {
  const [destination, ...extra] = process.argv.slice(2);
  if (!destination || extra.length) throw new Error('Usage: node scripts/prepare-pages.mjs <new-absolute-directory>');
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  if (fs.readFileSync(path.join(root, 'site/documents.js'), 'utf8') !== documentBundle(root)) {
    throw new Error('Reader bundle is stale. Run node scripts/build-site-docs.mjs first.');
  }
  const snapshot = collectSnapshot(root);
  for (const name of ['index.html', '.nojekyll', 'site/main.js', 'site/documents.js']) {
    if (!snapshot.files.some(file => file.path === name)) throw new Error(`Missing Pages entry: ${name}`);
  }
  const output = exportSnapshot(snapshot, destination);
  console.log(JSON.stringify({ directory: output, files: snapshot.files.length, sha256: snapshot.digest,
    note: 'Static Pages snapshot prepared for review. No Git history copied; no upload performed.' }, null, 2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
