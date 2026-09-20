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

const architectureTopics = {
  applications: {
    label: 'Request', title: 'Applications ask for capabilities.',
    copy: 'A writing tool, IDE, analytics workflow or creative application describes the AI task and its data contract without selecting a hard-coded model provider.',
    role: 'Declare intent and consume a typed result',
    boundary: 'Application permissions remain in force',
    status: 'Contract path implemented in the developer runtime',
  },
  cogposix: {
    label: 'Contract', title: 'CogPOSIX separates intent from implementation.',
    copy: 'The application uses typed buffers, model handles and asynchronous jobs through one capability-oriented interface. Providers and execution locations can evolve behind that contract.',
    role: 'Standardize requests, results and lifecycle semantics',
    boundary: 'An interface is not permission to access data or devices',
    status: 'Experimental Rust and C interfaces are implemented',
  },
  ecos: {
    label: 'Governance', title: 'ecOS makes the execution decision.',
    copy: 'The operating-system control plane intersects capability requirements with security policy, data rules, available hardware and resource limits before admitting work.',
    role: 'Resolve, admit, place, supervise and observe work',
    boundary: 'Policy remains independent of model output',
    status: 'Local runtime foundation implemented; full OS in development',
  },
  resolution: {
    label: 'Control plane', title: 'Resolve a capability, not a brand name.',
    copy: 'Eligible implementations must satisfy the requested contract, authorization, quality profile and hardware constraints before cost or speed can influence selection.',
    role: 'Match requests to approved implementations', boundary: 'Ineligible providers are excluded before optimization',
    status: 'Basic backend path exists; general discovery is planned',
  },
  models: {
    label: 'Control plane', title: 'Manage models as system resources.',
    copy: 'Packages need identity, versioning, integrity checks, resource declarations and revocation. Applications should not each invent a separate model supply chain.',
    role: 'Load, reuse, update and retire approved model packages', boundary: 'A loaded model receives no ambient authority',
    status: 'One pinned CPU model is validated; package lifecycle is planned',
  },
  security: {
    label: 'Control plane', title: 'Policy is enforced outside the model.',
    copy: 'Model output cannot grant itself file access, network access or device control. Those decisions belong to explicit system and application policy.',
    role: 'Authorize capabilities, data and execution boundaries', boundary: 'Learned predictions are never the security authority',
    status: 'Validation and process controls exist; production isolation is planned',
  },
  data: {
    label: 'Control plane', title: 'Data rules travel with the workload.',
    copy: 'The system tracks which application owns an input, which implementation may read it, where it may execute and how derived data must be handled.',
    role: 'Preserve ownership, locality and retention constraints', boundary: 'Data access does not imply permission to train on data',
    status: 'Typed and sealed inputs exist; broader governance is planned',
  },
  scheduling: {
    label: 'Control plane', title: 'AI competes for finite system resources.',
    copy: 'Inference, foreground applications and ordinary services need bounded memory, compute budgets, priorities and cancellation rather than unrestricted accelerator access.',
    role: 'Admit and prioritize bounded workloads', boundary: 'Resource availability never overrides authorization',
    status: 'Bounded jobs exist; multi-resource scheduling is planned',
  },
  locality: {
    label: 'Control plane', title: 'Locality is a policy decision.',
    copy: 'A workload can remain on the endpoint, move to a managed node or use an authorized external service. Each transition is visible and policy-controlled.',
    role: 'Select an eligible execution location', boundary: 'Off-device execution always creates a new trust boundary',
    status: 'Endpoint-local path exists; off-device placement is planned',
  },
  hardware: {
    label: 'Control plane', title: 'Use the right compute for the task.',
    copy: 'The system can match workload requirements to CPU, GPU or NPU capacity while applications continue to use the same capability contract.',
    role: 'Coordinate compute devices and backend availability', boundary: 'Hardware access stays mediated by the OS and drivers',
    status: 'CPU path validated; GPU and NPU backends are planned',
  },
  observability: {
    label: 'Control plane', title: 'Execution should be inspectable.',
    copy: 'Operators and users need model identity, actual execution location, resource use, failures and policy decisions without exposing private application data.',
    role: 'Report lifecycle, placement and resource evidence', boundary: 'Telemetry follows minimization and access policy',
    status: 'Job state and errors exist; full audit surfaces are planned',
  },
  nodes: {
    label: 'Control plane', title: 'Distribution starts with trusted membership.',
    copy: 'A participating node needs authenticated identity, declared capabilities, health state and explicit enrollment in an ecOS policy domain.',
    role: 'Coordinate approved compute across machines', boundary: 'Network discovery is never authorization',
    status: 'Planned architecture; no distributed fabric is implemented',
  },
  lifecycle: {
    label: 'Control plane', title: 'Resources have an owned lifecycle.',
    copy: 'Sessions, handles, buffers, jobs, workers and model packages need predictable creation, cancellation, cleanup, update and recovery behavior.',
    role: 'Keep resources attributable and recoverable', boundary: 'Failure does not silently replay or expand a request',
    status: 'Core job cleanup exists; complete package lifecycle is planned',
  },
  local: {
    label: 'Execution · Default', title: 'Run on this machine.',
    copy: 'Endpoint execution can minimize data movement, latency and external dependency. Provisioned capabilities may continue to operate without a network.',
    role: 'Use endpoint CPU, GPU or NPU resources', boundary: 'Application, runtime and worker isolation still matter',
    status: 'CPU execution is implemented in the developer prototype',
  },
  trusted: {
    label: 'Execution · Opt-in', title: 'Use an approved managed node.',
    copy: 'A workstation may delegate eligible work to an enrolled office GPU, private server or edge node while preserving the common capability contract.',
    role: 'Share controlled infrastructure across authorized applications', boundary: 'Another machine is always another execution boundary',
    status: 'Planned; authentication, transport and broker are not implemented',
  },
  external: {
    label: 'Execution · Explicit only', title: 'Cross the boundary by policy.',
    copy: 'An administrator may permit a named external service for a specific capability and data class. It is never an undisclosed fallback when local execution is unavailable.',
    role: 'Reach a deliberately authorized external capability', boundary: 'Disclosure, consent and service terms apply',
    status: 'Future profile; no external-service router is implemented',
  },
};

const architectureControls = [...document.querySelectorAll('[data-architecture-topic]')];
const architectureDetail = {
  panel: document.querySelector('.architecture-detail'),
  label: document.getElementById('architecture-detail-label'),
  title: document.getElementById('architecture-detail-title'),
  copy: document.getElementById('architecture-detail-copy'),
  role: document.getElementById('architecture-detail-role'),
  boundary: document.getElementById('architecture-detail-boundary'),
  status: document.getElementById('architecture-detail-status'),
};

function showArchitectureTopic(control) {
  const topic = architectureTopics[control.dataset.architectureTopic];
  if (!topic || !architectureDetail.title) return;
  if (architectureDetail.panel) architectureDetail.panel.hidden = false;
  for (const candidate of architectureControls) {
    const selected = candidate === control;
    candidate.classList.toggle('is-active', selected);
    candidate.setAttribute('aria-pressed', String(selected));
  }
  for (const key of ['label', 'title', 'copy', 'role', 'boundary', 'status']) {
    architectureDetail[key].textContent = topic[key];
  }
}

for (const control of architectureControls) {
  control.addEventListener('click', () => showArchitectureTopic(control));
  control.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft'].includes(event.key)) return;
    event.preventDefault();
    const direction = ['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1;
    const next = architectureControls[(architectureControls.indexOf(control) + direction + architectureControls.length) % architectureControls.length];
    next.focus();
    showArchitectureTopic(next);
  });
}

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
