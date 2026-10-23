// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { collectSnapshot } from '../scripts/publication.mjs';

const root = new URL('../', import.meta.url);
const read = name => fs.readFileSync(new URL(name, root), 'utf8');
const digest = name => crypto.createHash('sha256').update(fs.readFileSync(new URL(name, root))).digest('hex');
const component = 'components/ete-x/';

test('ETE-X publication matches the independently inventoried component bytes', () => {
  const inventory = JSON.parse(read(component + 'PROVENANCE.json'));
  const published = collectSnapshot(fileURLToPath(root)).files;
  const indexed = new Set();
  for (const entry of inventory.files) {
    assert.equal(digest(entry.path), entry.sha256, entry.path);
    assert.equal(published.find(file => file.path === entry.path)?.license, entry.license, entry.path);
    indexed.add(entry.path);
  }
  for (const entry of published.filter(file => file.path.startsWith(component))) {
    if (entry.path !== component + 'PROVENANCE.json') assert(indexed.has(entry.path), entry.path);
  }
  assert.equal(inventory.handoff.archive_sha256, 'c1f300d2cd547b580c6f6d032e85a2d038c7efc9d5cad024ef247bfd07f0da89');
});

test('native ETE-X is opt-in and leaves the existing C ABI unchanged', () => {
  for (const name of ['crates/ecos-daemon/Cargo.toml', 'crates/cog-backend-process/Cargo.toml']) {
    assert.match(read(name), /\[features\]\s+default\s*=\s*\[\]/);
    assert.match(read(name), /\netex\s*=/);
  }
  assert.match(read('crates/cog-backend-process/src/lib.rs'), /#\[cfg\(all\(target_os = "linux", feature = "etex"\)\)\]\s+mod etex;/);
  assert.equal(digest('include/cogposix/cog.h'), 'c58403fbf9c25d8245b68b4f7612816aebddd21a4e9b89900c3846f43da5c5f8');
});

test('retained qualification covers real native inference with the published worker', () => {
  const folder = component + 'validation/local-2026-10-23/';
  const qualification = JSON.parse(read(folder + 'qualification.json'));
  assert.equal(qualification.status, 'passed');
  assert.equal(qualification.uid, 10001);
  assert.equal(qualification.network, 'disabled by Docker');
  assert(qualification.steps.every(step => step.exit_code === 0));
  const native = JSON.parse(read(folder + 'native-c-abi.json'));
  assert.equal(native.status, 'pass');
  assert.equal(native.worker_sha256, digest(component + 'integration/worker.py'));
  assert.equal(native.client_sha256, digest(component + 'integration/examples/cogposix_client.py'));
  assert(native.operator_rejections.length === 8 && native.operator_rejections.every(check => check.passed));
  const real = native.engines.find(engine => engine.engine === 'etex');
  assert(real.ai_executed && real.passed && real.successive_result_records_equal);
  assert.equal(real.socket_mode, '0o600');
  assert(real.socket_removed_after_shutdown);
  assert(real.calls.length === 2 && real.calls.every(call => call.passed && call.result.execution.rerank_performed));
});

test('public ETE-X diagram is local, accessible and separates live-application scope', () => {
  const page = read('index.html');
  const section = page.match(/<section[^>]+id="ete-x"[\s\S]*?<\/section>/)?.[0];
  assert(section);
  assert(section.includes('aria-labelledby="etex-title"'));
  assert.equal((section.match(/class="etex-step"/g) ?? []).length, 5);
  assert(section.includes('connecting it to the live sheet is the next application step'));
  assert(section.includes('href="docs/ete-x.md"'));
  assert(!section.includes('http://') && !section.includes('<iframe'));
});
