// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { collectSnapshot, exportSnapshot } from '../scripts/publication.mjs';
import { packageWebsite, scanWebsiteText, websitePaths, websiteSnapshot } from '../scripts/package-website.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
function fixture(t) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ecos-zip-test-')));
  t.after(() => fs.rmSync(base, {recursive:true, force:true}));
  const source = path.join(base, 'source');
  exportSnapshot(collectSnapshot(root), source);
  return {base, source};
}

test('ZIP contains only site dependencies, documents and licenses, with index at its root', t => {
  const {base, source} = fixture(t);
  const target = path.join(base, 'website.zip');
  const report = packageWebsite(source, target);
  assert.equal(report.files, websitePaths.length);
  const entries = execFileSync('unzip', ['-Z1', target], {encoding:'utf8'}).trim().split('\n');
  assert.deepEqual(entries.sort(), [...websitePaths].sort());
  assert(entries.includes('index.html') && entries.includes('.nojekyll'));
  assert(!entries.some(name => /^(?:crates|tests|scripts|internal|\.git|PUBLICATION)/.test(name)));
  const bundle = execFileSync('unzip', ['-p', target, 'site/documents.js'], {encoding:'utf8'});
  const context = {window:{}};
  vm.runInNewContext(bundle, context);
  assert.deepEqual([...context.window.ECOS_DOCUMENTS.files].sort(), [...websitePaths].sort());
  assert.equal(context.window.ECOS_DOCUMENTS.documents['docs/website.md'], undefined);
  assert.throws(() => packageWebsite(source, target), /already exists/);
});

test('unlisted and explicitly denied documents stop packaging before output is written', t => {
  const {base, source} = fixture(t);
  const manifestFile = path.join(source, 'PUBLICATION.json');
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  manifest.do_not_publish.push('docs/vision.md');
  fs.writeFileSync(manifestFile, JSON.stringify(manifest));
  const target = path.join(base, 'denied.zip');
  assert.throws(() => packageWebsite(source, target), /Explicitly denied/);
  assert(!fs.existsSync(target));
  manifest.do_not_publish.pop();
  manifest.files = manifest.files.filter(file => file.path !== 'site/main.js');
  fs.writeFileSync(manifestFile, JSON.stringify(manifest));
  assert.throws(() => websiteSnapshot(source), /not approved/);
});

test('confidential document content and credentials fail closed without echoing secrets', t => {
  const {source} = fixture(t);
  const name = path.join(source, 'docs/vision.md');
  const original = fs.readFileSync(name, 'utf8');
  for (const injected of ['Classification: CONFIDENTIAL', 'api_key = "0123456789secretvalue"']) {
    fs.writeFileSync(name, original + '\n' + injected);
    assert.throws(() => websiteSnapshot(source), error => {
      assert.match(error.message, /Sensitive-content check failed/);
      assert(!error.message.includes('0123456789secretvalue'));
      return true;
    });
  }
});

test('sensitive markers are detected, but technical privacy descriptions remain allowed', () => {
  for (const text of ['-----BEGIN PRIVATE KEY-----', 'DO NOT PUBLISH',
    'ghp_' + 'x'.repeat(36), 'AKIA' + 'A'.repeat(16), 'https://user:secret@example.test']) {
    assert.throws(() => scanWebsiteText('test.md', text), /Sensitive-content/);
  }
  assert.doesNotThrow(() => scanWebsiteText('test.md', 'Private inputs remain isolated. No API key is required.'));
});
