// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { documentBundle, readerPaths } from '../scripts/build-site-docs.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
test('reader bundle matches approved source and excludes deployment/private documents', () => {
  const bundle = documentBundle(root);
  assert.equal(fs.readFileSync(new URL('../site/documents.js', import.meta.url), 'utf8'), bundle);
  const context = { window: {} };
  vm.runInNewContext(bundle, context);
  assert.deepEqual(Object.keys(context.window.ECOS_DOCUMENTS.documents).sort(), [...readerPaths].sort());
  for (const name of ['PUBLICATION.md', 'docs/website.md', 'docs/business-impact.md', 'docs/decisions.md']) {
    assert.equal(context.window.ECOS_DOCUMENTS.documents[name], undefined);
  }
  assert(!bundle.includes('Classification: INTERNAL'));
  assert(!bundle.includes('</script>'));
});

test('public page has exact title and no deployment or old status copy', () => {
  const page = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert(page.includes('<title>ecOS &gt;_ CogPOSIX</title>'));
  assert(!/PUBLICATION|docs\/website|Provisional project names|Open development|live-dot/.test(page));
  assert(page.includes('site/capabilities.svg'));
  assert(page.includes('site/capabilities-mobile.svg'));
  assert(!page.includes('class="capability-strip"'));
  assert(page.includes('id="distributed"'));
  assert(page.includes('Distributed execution and database integration are planned'));
  assert(page.includes('The open OS bringing a new POSIX to machine intelligence.'));
  assert(page.includes('ecOS is built around CogPOSIX, a system-level execution standard for running AI models as <strong>native</strong> computing resources, designed for performance and security.'));
  assert(!page.includes('reader-permalink'));
  assert(!/ecOS (?:&gt;|>)_[,.;:!?]/.test(page));
  assert(!/ecos\.sourceware|eCos RTOS|existing eCos project/.test(documentBundle(root)));
});

test('architecture explorer reproduces the system map as accessible interactive HTML', () => {
  const page = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const script = fs.readFileSync(new URL('../site/main.js', import.meta.url), 'utf8');
  const section = page.match(/<section class="architecture-explorer"[\s\S]*?<\/section>/)?.[0];
  assert(section);
  assert.equal((section.match(/data-architecture-topic=/g) || []).length, 19);
  for (const text of ['Applications', 'CogPOSIX', 'ecOS', 'Local compute', 'Trusted nodes',
    'External compute', 'Capability resolution']) {
    assert(page.includes(text), text);
  }
  assert(section.includes('aria-live="polite"'));
  assert(section.includes('aria-pressed="true"'));
  assert(section.includes('Traditional AI application'));
  assert(section.includes('ecOS application'));
  assert(section.includes('Centralized model management'));
  assert(section.includes('Without a shared system layer'));
  assert(section.includes('Execution boundaries are harder to inspect'));
  assert(page.includes('<a href="#architecture-map">Architecture</a>'));
  assert(page.includes('href="#architecture-map">Explore the architecture'));
  assert(!page.includes('ecos-cogposix_simplified-schema.png'));
  assert(script.includes("const architectureTopics = {"));
  assert(script.includes("'ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft'"));
});

test('community destinations link to the approved workspace and repository', () => {
  const page = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const section = page.match(/<section[^>]+id="community"[\s\S]*?<\/section>/)?.[0];
  assert(section);
  assert.equal((section.match(/<article>/g) || []).length, 4);
  assert(section.includes('href="https://github.com/logforce/ecosystem"'));
  assert(section.indexOf('<h3>Slack</h3>') < section.indexOf('<h3>GitHub Repository</h3>'));
  assert(section.indexOf('<h3>GitHub Repository</h3>') < section.indexOf('<h3>GitHub Discussions</h3>'));
  assert(section.includes('href="https://logforceai.slack.com/" target="_blank" rel="noopener noreferrer"'));
  assert(section.includes('Workspace membership may be required'));
  assert(!section.includes('href="index.html"'));
  assert(!section.includes('Project website'));
  for (const destination of ['discussions', 'issues']) {
    assert(section.includes(`href="https://github.com/logforce/ecosystem/${destination}" target="_blank" rel="noopener noreferrer"`));
  }
  assert(!section.includes('community-pending'));
  assert(section.includes('require maintainer approval'));
  assert(!/discord/i.test(section));
});

test('project introduction uses unified identity and states delivery status', () => {
  const readme = fs.readFileSync(new URL('../README.md', import.meta.url), 'utf8');
  assert(readme.startsWith('# ecOS >_CogPOSIX\n'));
  assert(readme.includes('local-first, not local-only'));
  assert(readme.includes('Authorized service'));
  assert(readme.includes('consumer laptop is a primary deployment target'));
  assert(!readme.includes('[Website](https://logforce.github.io/) ·'));
  assert(readme.includes('A bootable OS image is not available yet'));
  for (const name of ['README.md', 'docs/vision.md', 'docs/operating-system.md',
    'docs/deployment-examples.md', 'TRADEMARKS.md']) {
    const text = fs.readFileSync(path.join(root, name), 'utf8');
    assert(text.includes('ecOS >_CogPOSIX'), name);
    assert(!/our (?:new operating system|OS project)|third-party (?:OS that|operating.system (?:product|or product))/.test(text), name);
  }
});

test('architecture examples distinguish process containment, implementation and OT authority', () => {
  const readme = fs.readFileSync(new URL('../README.md', import.meta.url), 'utf8');
  const examples = fs.readFileSync(new URL('../docs/deployment-examples.md', import.meta.url), 'utf8');
  assert(readme.includes('Policy --> Local["This device<br/>works offline"]'));
  assert(readme.includes('Policy --> Domain["Approved node<br/>private or edge infrastructure"]'));
  assert(readme.includes('Runtime --> Worker["Supervised model worker"]'));
  assert(readme.includes('Worker --> Engine["Model execution on CPU"]'));
  assert(!readme.includes('Existing inference engines'));
  assert(!readme.includes('Isolated inference workers'));
  assert(readerPaths.includes('docs/deployment-examples.md'));
  for (const text of ['not delivered OT/IoT products', 'no inference-to-actuator arrow',
    'IoT: A Gateway', 'Vision: Inspecting', 'Desktop: Local', 'Optional Distribution:']) {
    assert(examples.includes(text), text);
  }
});

test('Pages preparation exports complete static files without deployment notes or Git history', t => {
  const parent = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ecos-pages-test-')));
  t.after(() => fs.rmSync(parent, {recursive:true, force:true}));
  const destination = path.join(parent, 'public');
  execFileSync(process.execPath, [path.join(root, 'scripts/prepare-pages.mjs'), destination]);
  for (const name of ['index.html', '.nojekyll', 'site/capabilities.svg', 'site/capabilities-mobile.svg',
    'site/documents.js', 'site/vendor/marked.js', 'docs/cogposix.md', 'LICENSE']) {
    assert(fs.existsSync(path.join(destination, name)), name);
  }
  for (const name of ['.git', 'docs/website.md', 'internal', 'docs/business-impact.md']) {
    assert(!fs.existsSync(path.join(destination, name)), name);
  }
});
