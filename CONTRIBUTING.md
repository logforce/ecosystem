# Contribution and Governance

## Current Stage

This repository is a design baseline. Proposals should improve an explicit contract,
resolve an open decision or provide reproducible evidence. Do not mark a capability
implemented because its documentation exists.

## Documentation Changes

Keep the original DOCX unchanged. Update the canonical topic document and record
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
conformance suite under the eventual chosen license.

## Licensing and Naming

No project license is selected yet, and this repository does not grant additional
copyright permissions through a placeholder license. Choose documentation and
software terms explicitly before public release. The open-interface business
strategy is a proposal, not an already effective licensing arrangement.

Third-party software, model weights, datasets, firmware and adapters retain their
own terms. Inventory redistribution and modification rights for every shipped
artifact. Both ecOS and CogPOSIX remain working names.

## Security Reporting

There is no public vulnerability-reporting channel or support SLA yet. Establish a
private reporting contact, response ownership and release-signing process before
external deployment. Do not put credentials, private datasets or sensitive exploit
reports into a public issue tracker.
