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
  assert(page.includes('A new operating system.'));
  assert(page.includes('CogPOSIX is its own POSIX-inspired interface'));
  assert(!/ecos\.sourceware|eCos RTOS|existing eCos project/.test(documentBundle(root)));
});

test('community destinations link to the approved workspace and repository', () => {
  const page = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const section = page.match(/<section[^>]+id="community"[\s\S]*?<\/section>/)?.[0];
  assert(section);
  assert.equal((section.match(/<article>/g) || []).length, 3);
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
