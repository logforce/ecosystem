// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../contracts/capability/v1/', import.meta.url));
const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'));
const schemas = [
  'capability-request.schema.json',
  'capability-result.schema.json',
  'execution-receipt.schema.json',
  'capability-manifest.schema.json',
  'error.schema.json'
];

test('capability v1 schemas have stable public identities and closed roots', () => {
  for (const name of schemas) {
    const schema = read(name);
    assert.equal(schema.$schema, 'https://json-schema.org/draft/2020-12/schema');
    assert.equal(schema.$id, `https://logforce.github.io/contracts/capability/v1/${name}`);
    assert.equal(schema.type, 'object');
    assert.equal(schema.additionalProperties, false);
    assert(Array.isArray(schema.required));
  }
});

test('every relative schema reference resolves inside capability v1', () => {
  for (const name of schemas) {
    const text = fs.readFileSync(path.join(root, name), 'utf8');
    for (const match of text.matchAll(/"\$ref"\s*:\s*"([^"#][^"]*)"/g)) {
      assert(fs.existsSync(path.join(root, match[1])), `${name}: ${match[1]}`);
    }
  }
});

test('fixtures join one application request to one runtime capability and receipt', () => {
  const request = read('examples/numeric-summary-request.json');
  const result = read('examples/numeric-summary-result.json');
  const manifest = read('examples/experimental-manifest.json');
  assert.equal(request.contract, 'ecos-capability-exchange.v1');
  assert.equal(result.contract, request.contract);
  assert.equal(result.request_id, request.request_id);
  assert.equal(result.receipt.capability, request.capability);
  assert.equal(result.output.schema, request.output_schema);
  assert.equal(result.receipt.locality, 'endpoint');
  assert.equal(result.receipt.network_used, false);
  assert.equal(manifest.capabilities[0].capability, request.capability);
  assert.equal(manifest.capabilities[0].status, 'experimental');
  for (const forbidden of ['model', 'provider', 'device', 'worker', 'node']) {
    assert.equal(Object.hasOwn(request, forbidden), false, forbidden);
  }
});

test('portable errors remain bounded and product-neutral', () => {
  const error = read('error.schema.json');
  assert.deepEqual(error.properties.code.enum, [
    'unsupported', 'invalid_input', 'schema_mismatch', 'policy_denied',
    'unavailable', 'busy', 'timeout', 'cancelled', 'internal'
  ]);
  assert.equal(error.properties.message.maxLength, 500);
  assert.equal(error.properties.retryable.type, 'boolean');
});
