# Public Release Boundary

This file is safe to publish. Internal release instructions remain outside the
public export. [PUBLICATION.json](PUBLICATION.json) is the exact file allowlist.
An unlisted file is excluded even if it is tracked in Git or is not ignored.

## Allowed Content

- Public product definition, architecture and proposed interface contracts.
- General threat model and bounded optimization design without proprietary algorithms.
- OS delivery design, public roadmap, contribution and license documents.
- Explicitly listed publication tooling and its tests.
- Explicitly listed mock runtime, Rust/C clients, CLI, headers and tests.
- Additional project-owned community code only after file-by-file manifest review.

## Excluded Content

- Internal commercial strategy, pricing assumptions, financial models and customer information.
- Commercial contract drafts, legal negotiation, confidential cross-repository notes.
- Proprietary analytics, weights, enterprise sensor logic and premium intelligence.
- Original planning documents, draft artwork and unreviewed attachments.
- Secrets, telemetry, private datasets, model caches, binaries and build output.
- The working repository's Git history, branches, tags, notes, remotes and configuration.

## Safe Export Procedure

Run with Node.js 20 or later:

```sh
node scripts/publication.mjs check
node --test tests/publication.test.mjs
node scripts/publication.mjs export /absolute/path/to/new-public-review-folder
```

The export destination must not exist and must be outside this source tree. The
exporter copies only reviewed manifest entries; it never copies `.git`, stages,
commits, creates remotes or uploads. It validates local document links against the
same allowlist and rejects symlinks and known private-content markers. It returns
a content digest to identify the snapshot for review.

Review the exported content before initializing a fresh public repository there.
Do not make this mixed working repository public, push its existing branch to a
public remote, mirror it, or attach its full archive to a release. Ignoring or
deleting files does not remove earlier versions from Git history.

Only the reviewed fresh export may seed public history. Keep internal development
and public release remotes in different working directories. For later releases,
review a new allowlisted export and apply its contents to the existing public
repository; never merge the private working repository's history.

## Limits of the Check

An allowlist controls which paths are copied. It cannot prove that an approved
file contains no newly added confidential sentence or credential. The checks are
fail-closed for malformed paths, missing files and recognized private content, but
they are not a comprehensive secret scanner, legal review or Git-host access control.
Review file contents and scan the release snapshot/history with the organization's
selected secret-scanning process before upload. Re-review any changed manifest.

These are project publication rules, not restrictions on recipients' licensed use
of an already public release.
