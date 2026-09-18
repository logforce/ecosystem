// SPDX-License-Identifier: Apache-2.0
const copy = document.getElementById('copy');
copy.addEventListener('click', async () => {
  const status = document.getElementById('copy-status');
  try {
    await navigator.clipboard.writeText(document.getElementById('commands').textContent);
    status.textContent = 'Commands copied.';
  } catch {
    status.textContent = 'Clipboard unavailable. Select the commands to copy them.';
  }
});

const reader = document.getElementById('reader');
const content = document.getElementById('reader-content');
const select = document.getElementById('reader-select');
const back = document.getElementById('reader-back');
const toc = document.getElementById('reader-toc');
const siteRoot = new URL('./', document.baseURI);
const catalog = window.ECOS_DOCUMENTS?.documents ?? {};
const files = new Set(window.ECOS_DOCUMENTS?.files ?? []);
const blocked = new Set(['PUBLICATION.md', 'docs/website.md', 'docs/decisions.md', 'README.md']);
let current = null;
let trail = [];
let returnFocus = null;

for (const [name, document] of Object.entries(catalog)) {
  const option = new Option(document.title, name);
  select.add(option);
}

function localPath(url) {
  if (url.origin !== siteRoot.origin || !url.pathname.startsWith(siteRoot.pathname)) return null;
  try { return decodeURIComponent(url.pathname.slice(siteRoot.pathname.length)); }
  catch { return null; }
}

function documentURL(name, hash = '') {
  const url = new URL('index.html', siteRoot);
  url.searchParams.set('doc', name);
  url.hash = hash;
  return url;
}

// Query links reload on GitHub Pages without requiring server-side SPA rewrites.
for (const link of document.querySelectorAll('a[href]')) {
  const url = new URL(link.href);
  const name = localPath(url);
  if (name && Object.hasOwn(catalog, name)) link.href = documentURL(name, url.hash).href;
}

function scrollToHeading(hash) {
  if (!hash) { content.scrollTop = 0; return; }
  let id;
  try { id = decodeURIComponent(hash.replace(/^#/, '')); } catch { return; }
  const heading = content.querySelector(`#${CSS.escape(id)}`);
  if (heading) content.scrollTop += heading.getBoundingClientRect().top - content.getBoundingClientRect().top - 24;
}

function showDocument(name, hash = '', remember = true) {
  if (!Object.hasOwn(catalog, name) || blocked.has(name) || !window.marked || !window.DOMPurify) return false;
  if (!reader.open) {
    returnFocus = document.activeElement;
    trail = [];
    current = null;
  }
  if (remember && current) trail.push({ name: current, scroll: content.scrollTop });
  current = name;
  const doc = catalog[name];
  document.getElementById('reader-title').textContent = doc.title;
  select.value = name;
  document.getElementById('reader-source').href = new URL(name, siteRoot).href;
  toc.replaceChildren();
  if (name.endsWith('.md')) {
    content.innerHTML = DOMPurify.sanitize(marked.parse(doc.content, { gfm: true }), {
      ALLOWED_TAGS: ['h1','h2','h3','h4','h5','h6','p','a','ul','ol','li','strong','em','del',
        'blockquote','pre','code','hr','br','table','thead','tbody','tr','th','td'],
      ALLOWED_ATTR: ['href','title','scope','colspan','rowspan','start'],
    });
  } else {
    const pre = document.createElement('pre');
    pre.className = 'plain-license';
    pre.textContent = doc.content;
    content.replaceChildren(pre);
  }
  const ids = new Map();
  for (const heading of content.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
    const slug = heading.textContent.toLowerCase().trim().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-');
    const count = ids.get(slug) ?? 0;
    ids.set(slug, count + 1);
    heading.id = slug + (count ? `-${count}` : '');
    if (heading.tagName === 'H2') {
      const link = document.createElement('a');
      link.href = documentURL(name, heading.id).href;
      link.textContent = heading.textContent;
      link.addEventListener('click', event => { event.preventDefault(); scrollToHeading(link.hash); });
      toc.append(link);
    }
  }
  // Resolve links against the source document, never against arbitrary user input.
  for (const link of content.querySelectorAll('a')) {
    let url;
    try { url = new URL(link.getAttribute('href'), new URL(name, siteRoot)); }
    catch { link.replaceWith(document.createTextNode(link.textContent)); continue; }
    const target = localPath(url);
    const allowedLocal = target && files.has(target) && !blocked.has(target) &&
      (!target.endsWith('.md') || Object.hasOwn(catalog, target));
    if (allowedLocal) {
      link.href = Object.hasOwn(catalog, target) ? documentURL(target, url.hash).href : url.href;
    } else if (target === null && ['https:', 'http:'].includes(url.protocol)) {
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    } else {
      link.replaceWith(document.createTextNode(link.textContent));
    }
  }
  for (const table of content.querySelectorAll('table')) {
    const wrapper = document.createElement('div');
    wrapper.className = 'table-scroll';
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  back.disabled = trail.length === 0;
  if (!reader.open) {
    document.body.classList.add('reader-open');
    reader.showModal();
  }
  scrollToHeading(hash);
  return true;
}

document.addEventListener('click', event => {
  const link = event.target.closest('a[href]');
  if (!link || link.id === 'reader-source' || link.closest('#reader-toc') ||
      event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const url = new URL(link.href);
  const target = localPath(url);
  const name = ['index.html', ''].includes(target) ? url.searchParams.get('doc') : target;
  if (name && Object.hasOwn(catalog, name)) {
    if (reader.open && name === current && url.hash) {
      event.preventDefault();
      scrollToHeading(url.hash);
    } else if (showDocument(name, url.hash)) event.preventDefault();
  }
});
select.addEventListener('change', () => showDocument(select.value));
back.addEventListener('click', () => {
  const previous = trail.pop();
  if (previous) {
    showDocument(previous.name, '', false);
    content.scrollTop = previous.scroll;
  }
});
document.getElementById('reader-close').addEventListener('click', () => reader.close());
reader.addEventListener('close', () => {
  document.body.classList.remove('reader-open');
  returnFocus?.focus({ preventScroll: true });
});
reader.addEventListener('click', event => {
  if (event.target !== reader) return;
  const rect = reader.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) reader.close();
});

const requestedDocument = new URL(location.href).searchParams.get('doc');
if (requestedDocument) showDocument(requestedDocument, location.hash);
