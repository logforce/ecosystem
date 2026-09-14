# Specification Guide

Status: expanded design with an experimental mock implementation, 13 September
2026. No full specification conformance or stable ABI is asserted.

## Authority and Scope

The original v0.1 planning document is the historical runtime baseline and is not
distributed here. The Markdown documents record the subsequent product
direction: controlled multi-model capabilities, an installable sovereign OS,
bounded adaptive optimization and defensive security assistance.

For implementation, use the public roadmap and explicit clarifications below.
The removed planning attachment is not required to build or contribute. Resolve
conflicts in a recorded decision before freezing interfaces. The
[implementation report](docs/implementation-r1.md) and
[wire contract](docs/protocol.md) identify the working subset.

## Requirement Language

- MUST / MUST NOT: proposed release requirements within the named milestone.
- SHOULD: recommended behavior with a documented reason for any exception.
- MAY: an optional extension; support must be discoverable.
- Research: an experiment, not a product or compatibility promise.
- Commercial hypothesis: an assumption requiring customer evidence.

Requirements in the design documents describe intended behavior unless explicitly
identified as implemented in the implementation report.

## Contract Map

| Domain | Canonical Markdown document |
| --- | --- |
| Product boundary and sovereignty | [Vision](docs/vision.md) |
| Runtime components and resource ownership | [Architecture](docs/architecture.md) |
| ABI, IPC, object lifecycle and compatibility | [CogPOSIX](docs/cogposix.md) |
| Capability semantics and model lifecycle | [Models](docs/models-and-capabilities.md) |
| Deployment, installer and recovery | [OS](docs/operating-system.md) |
| Permissions, threat model and defensive learning | [Security](docs/security.md) |
| Optimization privileges and experiments | [Adaptation](docs/adaptive-optimization.md) |
| Milestone acceptance and measurement | [Roadmap](docs/roadmap.md) |

## Changes and Clarifications Since v0.1

| Topic | Original baseline | Expanded decision and effect |
| --- | --- | --- |
| Final distribution | User-space runtime first | Retained; a complete installable Linux OS is now an explicit later deliverable |
| Model catalogue | Explicit artifacts; later capability discovery | Retained for MVP; controlled semantic profiles are required for interchangeable OS capabilities |
| Privileges | Described as a privileged coordinator | Split unprivileged coordination from narrowly privileged enforcement; avoid inference parsers in a root process |
| Backend isolation | Eventually separate workers | Mock experimentation may precede it; real models offered to untrusted clients require isolated workers |
| Job cancellation | Simplified state diagram | Cancellation is a request; resources stay pinned until backend access ends |
| Priority | REALTIME queue terminology | A priority name is not a deadline guarantee; priority authorization is required |
| ABI stability | Stable C ABI draft | Versioned experimental ABI until lifecycle and conformance gates pass; no premature permanent freeze |
| Locality | Future remote execution | Local-only default; off-device access disabled in strict sovereign profile |
| Learning | Future advanced scheduler | Start with telemetry and recommendations; no online core model training or kernel rewriting in MVP |
| Security learning | Not a distinct product track | Advisory detection research behind deterministic security controls |
| Business | Technical use cases | Add buyer hypotheses, unit economics and paid-pilot gates, not forecasts |

## Cross-Cutting Invariants

1. An untrusted input or model output cannot create authority.
2. Accepted work holds references to resources until access has ended.
3. Model substitution preserves the requested semantic and quality contract.
4. Required constraints either hold or cause an explicit error; preferences may fall back.
5. Local-only policy includes preprocessing, inference, diagnostics and crash handling.
6. Learning cannot alter trust roots, permissions, signing requirements or its own limits.
7. Recovery does not depend on a model, a network connection or a healthy ecOS daemon.
8. Performance, security and cost claims need reproducible evidence for a stated configuration.

## Unresolved Before Interface Freeze

Exact function signatures, wire schema, extension identifiers, capability ontology,
numeric ABI layouts, error enum values and language binding ergonomics
are not final. Community licensing is recorded in [LICENSE-SCOPE.md](LICENSE-SCOPE.md).
The compilable [C header](include/cogposix/cog.h) exposes the experimental subset;
its numeric layouts are testable but remain subject to versioned revision.
