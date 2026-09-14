// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { collectSnapshot, exportSnapshot } from '../scripts/publication.mjs';

function fixture(t, entries = []) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ecos-publication-test-')));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const root = path.join(base, 'source');
  fs.mkdirSync(root);
  const files = [
    { path: 'PUBLICATION.json', license: 'Apache-2.0' },
    { path: 'README.md', license: 'CC-BY-4.0' },
    ...entries,
  ];
  fs.writeFileSync(path.join(root, 'PUBLICATION.json'), JSON.stringify({ version: 1, default: 'exclude', files }));
  fs.writeFileSync(path.join(root, 'README.md'), '# Public\n');
  return { root, base };
}

test('export copies only allowlisted bytes and never source Git history or private files', t => {
  const { root, base } = fixture(t);
  fs.mkdirSync(path.join(root, '.git'));
  fs.writeFileSync(path.join(root, '.git', 'private-history'), 'withheld');
  fs.mkdirSync(path.join(root, 'internal'));
  fs.writeFileSync(path.join(root, 'internal', 'contract.md'), 'withheld');
  const snapshot = collectSnapshot(root);
  const target = exportSnapshot(snapshot, path.join(base, 'export'));
  assert.deepEqual(fs.readdirSync(target).sort(), ['PUBLICATION.json', 'README.md']);
  assert.equal(collectSnapshot(target).digest, snapshot.digest);
  fs.writeFileSync(path.join(root, 'README.md'), '# Changed after review\n');
  assert.equal(fs.readFileSync(path.join(target, 'README.md'), 'utf8'), '# Public\n');
});

for (const name of ['../outside.md', '/absolute.md', '.git/config', 'internal/terms.md',
  'docs/business-impact.md', 'docs/website.md', 'draft.jpeg', 'docs/*', 'docs//file.md', '.env', 'site/.nojekyll',
  '.github/workflows/unreviewed.yml', '.github/secrets']) {
  test(`rejects unsafe or private manifest path: ${name}`, t => {
    const { root } = fixture(t, [{ path: name, license: 'Apache-2.0' }]);
    assert.throws(() => collectSnapshot(root));
  });
}

test('rejects missing files and case-insensitive duplicate paths', t => {
  const missing = fixture(t, [{ path: 'missing.md', license: 'CC-BY-4.0' }]);
  assert.throws(() => collectSnapshot(missing.root), /ENOENT/);
  const duplicate = fixture(t, [{ path: 'readme.md', license: 'CC-BY-4.0' }]);
  assert.throws(() => collectSnapshot(duplicate.root), /Duplicate/);
});

test('rejects a symlink file and a symlink directory', t => {
  const first = fixture(t, [{ path: 'linked.md', license: 'CC-BY-4.0' }]);
  fs.symlinkSync('README.md', path.join(first.root, 'linked.md'));
  assert.throws(() => collectSnapshot(first.root), /Symlink/);
  const second = fixture(t, [{ path: 'docs/test.md', license: 'CC-BY-4.0' }]);
  fs.mkdirSync(path.join(second.base, 'outside'));
  fs.symlinkSync('../outside', path.join(second.root, 'docs'));
  assert.throws(() => collectSnapshot(second.root), /Symlink/);
});

test('rejects private classification and links to excluded documents', t => {
  const { root } = fixture(t);
  fs.writeFileSync(path.join(root, 'README.md'), 'Classification: INTERNAL - DO NOT PUBLISH.\n');
  assert.throws(() => collectSnapshot(root), /Internal classification/);
  fs.writeFileSync(path.join(root, 'README.md'), '[contract](internal/terms.md)\n');
  assert.throws(() => collectSnapshot(root), /Link outside/);
});

test('rejects exports into source tree or existing directories', t => {
  const { root, base } = fixture(t);
  const snapshot = collectSnapshot(root);
  assert.throws(() => exportSnapshot(snapshot, path.join(root, 'output')), /outside/);
  assert.throws(() => exportSnapshot(snapshot, root), /outside/);
  assert.throws(() => exportSnapshot(snapshot, base), /EEXIST/);
  assert.throws(() => exportSnapshot(snapshot, 'relative-output'), /absolute/);
});

test('rejects unknown licenses and permissive default publication', t => {
  const first = fixture(t, [{ path: 'file.md', license: 'UNKNOWN' }]);
  assert.throws(() => collectSnapshot(first.root), /Unknown license/);
  const second = fixture(t);
  const manifest = path.join(second.root, 'PUBLICATION.json');
  const parsed = JSON.parse(fs.readFileSync(manifest, 'utf8'));
  parsed.default = 'include';
  fs.writeFileSync(manifest, JSON.stringify(parsed));
  assert.throws(() => collectSnapshot(second.root), /Invalid publication manifest/);
});

test('only exact reviewed artwork with reserved rights can be exported', t => {
  const name = 'assets/ecOS-ystem_square_logo.png';
  const { root, base } = fixture(t, [{ path: name, license: 'LicenseRef-Brand-Reserved' }]);
  fs.mkdirSync(path.join(root, 'assets'));
  fs.copyFileSync(new URL(`../${name}`, import.meta.url), path.join(root, name));
  const snapshot = collectSnapshot(root);
  exportSnapshot(snapshot, path.join(base, 'with-art'));
  assert.equal(snapshot.files.find(file => file.path === name).license, 'LicenseRef-Brand-Reserved');
  fs.appendFileSync(path.join(root, name), 'changed');
  assert.throws(() => collectSnapshot(root), /Artwork changed/);
  const wrong = fixture(t, [{ path: name, license: 'Apache-2.0' }]);
  assert.throws(() => collectSnapshot(wrong.root), /Artwork license mismatch/);
  const extra = fixture(t, [{ path: 'assets/other.png', license: 'LicenseRef-Brand-Reserved' }]);
  assert.throws(() => collectSnapshot(extra.root), /unreviewed path/);
});

test('rejects private content in website HTML', t => {
  const { root } = fixture(t, [{ path: 'index.html', license: 'Apache-2.0' }]);
  fs.writeFileSync(path.join(root, 'index.html'), 'Classification: INTERNAL - DO NOT PUBLISH.');
  assert.throws(() => collectSnapshot(root), /Internal classification/);
});

test('allows only the root static-site marker without opening other hidden paths', t => {
  const { root } = fixture(t, [{ path: '.nojekyll', license: 'Apache-2.0' }]);
  fs.writeFileSync(path.join(root, '.nojekyll'), '');
  assert(collectSnapshot(root).files.some(file => file.path === '.nojekyll'));
});

test('explicit deny rules override allowlisted files and directory members case-insensitively', t => {
  for (const denied of [['README.md'], ['readme.MD'], ['docs/']]) {
    const {root} = fixture(t, [{path:'docs/page.md', license:'CC-BY-4.0'}]);
    fs.mkdirSync(path.join(root, 'docs'));
    fs.writeFileSync(path.join(root, 'docs/page.md'), '# Public');
    const manifestFile = path.join(root, 'PUBLICATION.json');
    const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
    manifest.do_not_publish = denied;
    fs.writeFileSync(manifestFile, JSON.stringify(manifest));
    assert.throws(() => collectSnapshot(root), /Explicitly denied/);
  }
});

test('malformed explicit deny lists fail closed', t => {
  for (const denied of [null, 'docs/', ['../private'], ['docs/*'], ['']]) {
    const {root} = fixture(t);
    const manifestFile = path.join(root, 'PUBLICATION.json');
    const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
    manifest.do_not_publish = denied;
    fs.writeFileSync(manifestFile, JSON.stringify(manifest));
    assert.throws(() => collectSnapshot(root), /Invalid do_not_publish/);
  }
});
