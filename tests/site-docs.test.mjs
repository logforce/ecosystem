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

test('architecture examples distinguish process containment, implementation and OT authority', () => {
  const readme = fs.readFileSync(new URL('../README.md', import.meta.url), 'utf8');
  const examples = fs.readFileSync(new URL('../docs/deployment-examples.md', import.meta.url), 'utf8');
  assert(readme.includes('subgraph Worker["Supervised worker"]'));
  assert(readme.includes('Engine["Inference engine with loaded model"]'));
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
