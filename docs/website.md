# Project Website

Classification: INTERNAL - DO NOT PUBLISH.
This operational guide is excluded from both the source export and reader bundle.

The root [index.html](../index.html) is a static project page with vendored reader libraries.
Open it directly for local preview; no development server or build is required.
CSS and JavaScript are local. There are no external fonts, analytics, tracking
pixels, contact forms or inference requests. Clipboard access occurs only when
the visitor presses the copy button; failure leaves the commands selectable.
The root `.nojekyll` marker preserves the site as static files. Marked and DOMPurify
render a prebuilt bundle of approved documents in an accessible native dialog.
Regenerate it with `node scripts/build-site-docs.mjs` after public document edits;
`node scripts/build-site-docs.mjs --check` detects stale content. The source view
remains available in another tab. Internal/deployment notes are never bundled.

## Content and License Boundaries

The page distinguishes working experimental R1/R2/R3 subsets from planned
scheduling, packaging, applications and an installable OS. LOGFORCE is an optional,
separately licensed offering, not a dependency or shipped integration. The license
table must remain consistent with [license scope](../LICENSE-SCOPE.md) and
[community terms](../TERMS.md). Do not advertise unverified security, accuracy,
cost-saving or performance claims.

Markup/CSS/JavaScript use Apache-2.0; website editorial prose uses CC BY 4.0.
Only two supplied PNG logos are reviewed for publication, under the reserved-rights
[artwork notice](../assets/BRAND-NOTICE.md). Their exact hashes are checked by the
exporter. The generated computing illustration is separately approved under CC
BY 4.0 where copyright applies. No LOGFORCE implementation is included.
Review rights-holder authorization and naming clearance before public deployment.

## GitHub Pages

### Website-Only ZIP

Run `node scripts/package-website.mjs` to create `dist/ecos-github-pages.zip`, or
pass a new `.zip` output path. Node.js 20+, `zip` and `unzip` are required. Run
`node scripts/package-website.mjs --check` to inspect the selection without writing
an archive. Existing archives are never overwritten. Build output is Git-ignored.

This archive contains only selected website assets/license files/documents, with `index.html`
and `.nojekyll` at its root. It does not include the runtime source, build tools,
tests, the publication manifest, release-operation notes or Git history. The
reader bundle is freshly generated from checked documents; its link inventory is
limited to ZIP members. References to runtime source outside the ZIP appear as
plain text in the reader rather than broken links. The complete source export
procedure below remains available when distributing the actual runtime repository.

Every selected input must be allowlisted in `PUBLICATION.json`. Its explicit
`do_not_publish` entries take precedence over the allowlist, with a trailing slash
denoting a directory prefix. Symlinks, changed artwork, restricted classification
markers, known private strategy markers, local workstation paths and common
credential formats stop packaging. Error messages identify the file and rule,
not the secret value. The ZIP is integrity-tested and its exact member list checked.

The current selected prose has been reviewed for sensitive business/operational
material; no such content was found. Automated detection cannot guarantee absence
of all secrets or confidential information. Human review is still necessary when
documents change. Do not weaken the deny rules to bypass a failed check.

Extract the ZIP into a clean public website repository and deploy its root through
GitHub Pages. Do not commit the ZIP alone and expect Pages to unpack it. Keep the
entire extracted tree, including `.nojekyll`, the document sources and notices.

### Complete Source Export

1. Run `node scripts/build-site-docs.mjs`, `node scripts/publication.mjs check` and
   `node --test tests/publication.test.mjs tests/site-docs.test.mjs tests/website-package.test.mjs`.
2. Use `node scripts/prepare-pages.mjs /absolute/path/to/new-review-directory`
   to export into a new directory outside the working tree. Review all exported bytes,
   asset rights and secret-scanning results before initializing fresh public history.
3. Create/push only that reviewed public repository. Do not push this workspace's
   Git history or turn this working repository public.
4. In the public repository's Settings > Pages, choose Deploy from a branch, select
   the public branch and `/(root)`, and save. Follow the
   [official GitHub Pages instructions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
5. Visit the generated project URL and check styles, logos, navigation, clipboard
   feedback and all document/license links. Relative paths support project subpaths.

The preparation command rejects stale document bundles and existing destinations.
The output includes the root `index.html`, `.nojekyll`, local libraries, images,
Markdown sources and license files. Deploy that entire reviewed snapshot, not just
the HTML file and not `/docs`. Keep `.nojekyll` so Markdown URLs remain accessible.

Document links use `index.html?doc=docs%2Fcogposix.md` with optional heading fragments.
They work under both a project prefix and a custom-domain root, including reloads
and new tabs. No SPA rewrite rule or backend is required. Source links remain
relative to the actual site root; no GitHub owner/repository name is hardcoded.

Do not configure Pages on the private mixed repository: branch publishing can
expose eligible content beyond the landing page. This change does not create a
remote, upload content, enable Pages or establish a commercial service.

No repository URL is guessed in the page. Add a source-repository link only after
the reviewed public repository has a confirmed address. Keep private cross-project
notes and internal commercial documents off the website.

## Verification

Desktop (1440 px), mobile (390 px) and narrow mobile (320 px) were checked in
headless Chrome with Playwright: local document links, section anchors, logo
decoding, copy-button feedback, all eleven license rows and horizontal overflow.
Screenshots were visually reviewed. Publication tests verify that changed or
additional artwork cannot bypass the explicit review boundary. Repeat browser
checks after layout changes and verify the deployed URL before announcing it.

The actual prepared export was served over temporary HTTP at both `/` and
`/ecos-cogposix/`. All local targets collected from the page and its 26 rendered
documents returned HTTP 200. Direct document links, reloads, heading anchors,
cross-document navigation and Escape were exercised. Responsive screenshots at
320, 390, 1440 and 1920 pixels verified the translucent hero and static branching
diagram. External websites were not availability-tested; their uptime is outside
the project's control. These are local deployment simulations, not a claim that
GitHub Pages has already been enabled or that a public domain is live.

The 39-file website ZIP was also extracted and tested independently at both URL
prefixes. All local page/reader links returned HTTP 200; 26 documents, reloads,
heading anchors and nested navigation passed. Its runtime-source references are
non-clickable text because that source is intentionally not in the archive.
