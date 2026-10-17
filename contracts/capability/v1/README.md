<!-- SPDX-License-Identifier: Apache-2.0 -->
# Capability Exchange v1

Status: draft integration contract. The schemas are testable, but no network or
CogPOSIX adapter implements this exchange yet.

Capability Exchange v1 is the application-facing boundary between a product such
as CogSheet™ and an execution authority such as ecOS >_ CogPOSIX. It keeps product
workflow semantics out of the runtime and keeps model, device and worker selection
out of the application.

The application supplies a versioned capability, immutable input identities,
output schema, placement requirement and bounded constraints. The execution
authority either returns a typed result and receipt or a bounded error. A request
never grants access to data, network, devices or models that the effective system
policy does not already authorize.

## Files

- [capability-request.schema.json](capability-request.schema.json) defines an
  application request.
- [capability-result.schema.json](capability-result.schema.json) defines its
  terminal result.
- [execution-receipt.schema.json](execution-receipt.schema.json) records the
  selected deployment and observable execution boundary.
- [capability-manifest.schema.json](capability-manifest.schema.json) defines
  discovery metadata returned by a runtime.
- [error.schema.json](error.schema.json) defines portable failure classes.
- `examples/` contains illustrative conformance fixtures, not evidence of a live
  implementation or approved deployment.

## Ownership Boundary

Applications own formulas, dependency graphs, user interaction and domain output
schemas. ecOS >_ CogPOSIX owns local capability resolution, package activation,
resource accounting, worker isolation, hardware selection and node placement.

The draft JSON exchange is a semantic layer above the current tensor-oriented
CogPOSIX wire protocol. An adapter must map a supported semantic capability to
approved runtime objects and jobs. Unsupported mappings fail explicitly.

## Compatibility

Minor additions cannot make an optional field required or change existing meaning.
Breaking changes require a new contract directory. Applications pin a released
contract revision and verify its files before use; copying a schema without its
revision and digest does not establish compatibility.

Production discovery requires a signed manifest and an independently configured
trust root. The draft schema describes signature metadata but does not define a
trust store, signing ceremony or activation policy.
