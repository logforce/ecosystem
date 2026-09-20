// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const licenses = new Set(['Apache-2.0', 'CC-BY-4.0', 'MIT', 'License-Text', 'LicenseRef-Brand-Reserved']);
const reviewedArt = new Map([
  ['assets/ecOS-ystem_logo.png', '72e319fd8ddb42c9d2f304af0ad82e819996a0920b1fd1b2452ebf4e51c75a4f'],
  ['assets/ecOS-ystem_square_logo.png', '597e4bd6be111b53726f8f1dbd4babe1830daed8f67edf52586d8a2f7f6dee0c'],
  ['assets/local-compute-hero.png', '461de48cb22a84074f29333978b9ade88597020af7c4c6d8477f8f203817e3e6'],
  ['assets/ot-system-hero.png', 'c360083e2a04c5bd7ce303c9a9cfadcb2b63ac3ac70a4f40368ac02228007ca1'],
]);
const excludedParts = new Set(['internal', 'private', 'enterprise', 'logforce']);
const excludedNames = new Set(['docs/business-impact.md', 'docs/website.md']);
const attachment = /\.(?:docx?|pdf|jpe?g|png|zip|onnx|gguf|safetensors|pem|key)$/i;
const reviewedHiddenPaths = new Set(['.gitignore', '.nojekyll', '.github/workflows/ci.yml']);
const restrictedMarkerHashes = new Set([
  '54b01587ffef5a6128e05f9b5e36cfcbf1393728209449e802873be3264482e7',
  '813aa1640511f5d82ee24dfde5aa5a4a42cb18128d6b910e01378f5575017774',
  '89124ed7233c1322d2f9d69b72c7bad022ea6c74c980c96c4c707a149b3d7a9b',
  'a66b125a4c17a6a8446784b8c98238da30e70a6b98651fead1b44dbfbce1d113',
]);

// Fingerprints avoid naming internal terms in public tooling; they are not encryption.
export function hasRestrictedMarker(text) {
  return (text.toLowerCase().match(/[a-z0-9_-]+/g) ?? []).some(word =>
    restrictedMarkerHashes.has(crypto.createHash('sha256').update(word).digest('hex')));
}

function regularFile(root, relative) {
  let current = root;
  const parts = relative.split('/');
  for (const [index, part] of parts.entries()) {
    current = path.join(current, part);
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) throw new Error(`Symlink excluded: ${relative}`);
    if (index < parts.length - 1 && !stat.isDirectory()) {
      throw new Error(`Invalid parent: ${relative}`);
    }
    if (index === parts.length - 1 && !stat.isFile()) {
      throw new Error(`Not a regular file: ${relative}`);
    }
  }
  return current;
}

function validatePath(relative) {
  if (typeof relative !== 'string' || !/^[A-Za-z0-9_./-]+$/.test(relative)) {
    throw new Error('Manifest requires literal relative paths');
  }
  const parts = relative.split('/');
  if (parts.some(part => !part || part === '.' || part === '..')) {
    throw new Error(`Unsafe path: ${relative}`);
  }
  if (parts.some(part => part.startsWith('.') && !reviewedHiddenPaths.has(relative))) {
    throw new Error(`Hidden path excluded: ${relative}`);
  }
  if (parts.some(part => excludedParts.has(part.toLowerCase())) ||
      excludedNames.has(relative.toLowerCase()) || (attachment.test(relative) && !reviewedArt.has(relative))) {
    throw new Error(`Private or unreviewed path excluded: ${relative}`);
  }
}

function validateDocument(relative, text, allowed) {
  if (/^Classification:\s*INTERNAL\b/im.test(text)) {
    throw new Error(`Internal classification in public file: ${relative}`);
  }
  if (hasRestrictedMarker(text)) {
    throw new Error(`Private strategy marker in public file: ${relative}`);
  }
  if (/\/Users\/|\/home\/[^\s/]+\/|app:\/\//.test(text)) {
    throw new Error(`Local environment reference in public file: ${relative}`);
  }
  if ((text.match(/^```/gm) ?? []).length % 2) {
    throw new Error(`Unbalanced code fence: ${relative}`);
  }
  // This checks the inline Markdown links used in this repository, not arbitrary HTML.
  for (const match of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = match[1];
    if (/^(?:https?:|mailto:|#)/.test(target)) continue;
    const decoded = decodeURIComponent(target.split('#')[0]);
    const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(relative), decoded));
    if (!allowed.has(resolved) || decoded.startsWith('/')) {
      throw new Error(`Link outside public snapshot: ${relative} -> ${target}`);
    }
  }
}

export function collectSnapshot(source) {
  const root = fs.realpathSync(source);
  const manifestPath = regularFile(root, 'PUBLICATION.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (manifest.version !== 1 || manifest.default !== 'exclude' ||
      !Array.isArray(manifest.files) || manifest.files.length === 0) {
    throw new Error('Invalid publication manifest; default must be exclude');
  }
  const denied = manifest.do_not_publish === undefined ? [] : manifest.do_not_publish;
  if (!Array.isArray(denied) || denied.some(name => typeof name !== 'string' ||
      !/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*\/?$/.test(name) ||
      name.split('/').some(part => part === '.' || part === '..'))) {
    throw new Error('Invalid do_not_publish list; use literal files or directories ending in /');
  }
  const allowed = new Set();
  const folded = new Set();
  for (const entry of manifest.files) {
    if (typeof entry.path === 'string' && denied.some(name => name.endsWith('/')
      ? entry.path.toLowerCase().startsWith(name.toLowerCase())
      : entry.path.toLowerCase() === name.toLowerCase())) {
      throw new Error(`Explicitly denied by do_not_publish: ${entry.path}`);
    }
    validatePath(entry.path);
    if (!licenses.has(entry.license)) throw new Error(`Unknown license: ${entry.path}`);
    const artworkLicense = ['assets/local-compute-hero.png', 'assets/ot-system-hero.png'].includes(entry.path)
      ? 'CC-BY-4.0' : 'LicenseRef-Brand-Reserved';
    if ((reviewedArt.has(entry.path) && entry.license !== artworkLicense) ||
        (!reviewedArt.has(entry.path) && entry.license === 'LicenseRef-Brand-Reserved')) {
      throw new Error(`Artwork license mismatch: ${entry.path}`);
    }
    if (folded.has(entry.path.toLowerCase())) throw new Error(`Duplicate path: ${entry.path}`);
    allowed.add(entry.path);
    folded.add(entry.path.toLowerCase());
  }
  if (!allowed.has('PUBLICATION.json')) throw new Error('Manifest must include itself');
  const files = manifest.files.map(entry => {
    const data = fs.readFileSync(regularFile(root, entry.path));
    if (/\.(md|html)$/.test(entry.path)) validateDocument(entry.path, data.toString('utf8'), allowed);
    const sha256 = crypto.createHash('sha256').update(data).digest('hex');
    if (reviewedArt.has(entry.path) && reviewedArt.get(entry.path) !== sha256) {
      throw new Error(`Artwork changed; explicit review required: ${entry.path}`);
    }
    return { ...entry, data, sha256 };
  });
  files.sort((a, b) => a.path.localeCompare(b.path, 'en'));
  const inventory = files.map(({ path: name, license, sha256 }) => ({ path: name, license, sha256 }));
  const digest = crypto.createHash('sha256').update(JSON.stringify(inventory)).digest('hex');
  return { root, files, digest };
}

export function exportSnapshot(snapshot, destination) {
  if (!path.isAbsolute(destination)) throw new Error('Export destination must be absolute');
  const target = path.resolve(destination);
  const parent = fs.realpathSync(path.dirname(target));
  const canonicalTarget = path.join(parent, path.basename(target));
  if (canonicalTarget !== target) throw new Error('Export parent must not be a symlink path');
  const relative = path.relative(snapshot.root, target);
  if (!relative || (!relative.startsWith(`..${path.sep}`) && relative !== '..')) {
    throw new Error('Export must be outside the source tree');
  }
  // mkdir without recursive mode rejects existing destinations, including symlinks.
  fs.mkdirSync(target, { mode: 0o700 });
  for (const file of snapshot.files) {
    const output = path.join(target, file.path);
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, file.data, { flag: 'wx', mode: 0o644 });
  }
  return target;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  try {
    const [command, destination, ...extra] = process.argv.slice(2);
    if (extra.length || !['check', 'export'].includes(command) ||
        (command === 'check' && destination) || (command === 'export' && !destination)) {
      throw new Error('Usage: node scripts/publication.mjs check | export <new-absolute-directory>');
    }
    const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
    const snapshot = collectSnapshot(source);
    const exportedTo = command === 'export' ? exportSnapshot(snapshot, destination) : undefined;
    console.log(JSON.stringify({ files: snapshot.files.length, sha256: snapshot.digest, exportedTo,
      note: 'Allowlist validation only; content, rights and secret review still required. No upload performed.' }, null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
