# Contribution and Governance

## Current Stage

This repository contains an experimental mock runtime alongside the wider design.
Start with the [development guide](docs/development.md) and
[implementation limits](docs/implementation-r1.md). Changes should improve an
explicit contract, resolve an open decision or provide reproducible evidence.
Do not mark a capability implemented because its documentation exists.

## Documentation Changes

Update the canonical public topic document and record
material architecture changes in [decisions](docs/decisions.md). Use relative links
within the repository and primary-source links for external technical claims.
Label experimental APIs, financial hypotheses and unverified hardware support.

## Implementation Expectations

Start with the [roadmap](docs/roadmap.md) and [specification guide](SPEC.md). Keep
ownership, failure behavior, permissions and measurement visible. Do not introduce
LLM-specific semantics into generic tensor/job contracts. An implementation change
must include verification appropriate to its behavioral scope.

## Interface Governance Proposal

Use a public proposal process once the project is published. Each interface change
should state motivation, syntax/semantics, compatibility, security impact and
conformance tests. Core API stability should require implementation feedback from
more than one application; standardization claims need independent adoption.

Maintain distinct versioning for core ABI, wire protocol, capability profiles,
packages and OS releases. Commercial features must not silently redefine a public
capability contract. Independent implementations should be able to run the same
conformance suite under Apache-2.0. Certification marks remain separately controlled.

## Licensing and Naming

The explicit grants and exclusions are in [LICENSE-SCOPE.md](LICENSE-SCOPE.md).
Listed community code is Apache-2.0; listed public prose is CC BY 4.0. Original code
examples embedded in public prose are additionally Apache-2.0. Internal material,
private models and unreviewed assets are excluded; no rights to another repository
are granted by a contribution here.

Third-party software, model weights, datasets, firmware and adapters retain their
own terms. Inventory redistribution and modification rights for every shipped
artifact. Both ecOS and CogPOSIX remain working names.

## Contribution Certification

Submit contributions under the license applicable to each changed public file,
including the additional Apache grant for original embedded code examples. Retain
third-party notices and identify imported content and its license. Add a
`Signed-off-by: Name <email>` line to certify the
[Developer Certificate of Origin 1.1](https://developercertificate.org/).
Use a public contact identity you are authorized to supply; sign-off becomes part
of public Git history. DCO certifies origin/authority and is not copyright assignment
or blanket patent clearance. No separate copyright-assignment CLA is required.

Do not submit proprietary LOGFORCE code, internal business notes, confidential
examples or customer data. New public files need an explicit
[publication manifest](PUBLICATION.json) entry and content/license review.

## Security Reporting

There is no public vulnerability-reporting channel or support SLA yet. Establish a
private reporting contact, response ownership and release-signing process before
external deployment. Do not put credentials, private datasets or sensitive exploit
reports into a public issue tracker.
