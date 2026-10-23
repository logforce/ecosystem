# CogSheet™ Integration Boundary

Status: architecture decision and draft contract, 6 October 2026, extended with an
ETE-X semantic mapping reference on 22 October 2026. The live CogSheet™ application
bridge and OS-image integration remain separate deliverables.

## Decision

CogSheet™ is an application product and ecOS >_ CogPOSIX is its sovereign system
execution layer. They remain separate projects with a versioned integration
contract. The cloud preview is a deployment profile of CogSheet™, not a competing
runtime and not the implementation of ecOS.

| CogSheet™ owns | ecOS >_ CogPOSIX owns |
| --- | --- |
| Grid, workbook and `.hyx` semantics | Capability resolution and package activation |
| Formula graph, schemas and visible cell errors | Jobs, memory, isolation and resource accounting |
| User identity, collaboration and cloud persistence | Local devices, workers and approved nodes |
| User consent for hosted exchange | Mandatory system locality and network policy |
| Domain validation and result presentation | Deployment identity, revocation and execution receipts |

CogSheet™ can request an allowed placement but cannot select an unapproved local
model, device or node. ecOS >_ CogPOSIX can reject or narrow a request but cannot
reinterpret workbook formulas, grant hosted consent or write unrelated cells.

## Policy Intersection

The effective authority is the intersection of user consent, workbook/cell policy,
system policy and capability/package restrictions. Every layer may narrow the
request. No layer may expand authority received from another layer. In particular,
`local-required` never becomes an external call, and enabling hosted processing in
CogSheet™ does not force ecOS to allow network or node placement.

## Two Schedulers

CogSheet™ schedules the application dependency graph: which cell is ready, stale,
blocked or dependent on another result. ecOS >_ CogPOSIX schedules system resources:
which approved deployment may execute, on which device or node, within what memory,
priority and time limits. Neither scheduler substitutes for the other.

## Contract

The draft [Capability Exchange v1](../contracts/capability/v1/README.md) defines
request, result, error, receipt and discovery-manifest schemas. It is a semantic
layer above the current tensor-oriented CogPOSIX wire protocol. A future adapter
maps a supported semantic profile to runtime objects and jobs.

The optional [ETE-X component](ete-x.md) now supplies a pure, tested mapping for
`etex.rag.search.v1`, alongside the native retrieval backend. The host must supply
an authenticated, authorized corpus context; the mapper validates request/result
shape and binds the actual snapshot digest to that grant. It does not authenticate
a browser or connect the current cloud preview to the native daemon. No ETE-X
spreadsheet formula is advertised as available by this reference alone.

Product formula names do not become system ABI names. For example:

```text
CG.PROFILE(A1:A100)
        -> CogSheet™ validates a range and requests numeric.summary.v1
        -> ecOS resolves an approved deployment or returns a portable error
        -> CogSheet™ maps the result or error back to the originating cell
```

Model IDs, provider credentials and hardware choices never belong in a workbook
formula. A capability manifest describes what the connected runtime can actually
execute; planned formula-catalogue entries are not runtime availability claims.

## Reference Executors

A product repository may retain a deterministic reference executor for contract
tests and cloud-preview development. It must be identified as a fixture, must not
claim ecOS isolation or hardware policy, and must not grow into a parallel sovereign
runtime. Node enrollment, mesh transport, revocation and distributed placement are
owned by ecOS >_ CogPOSIX.

## Joint Milestone

The first joint deliverable is an offline CogSheet™ loop in the graphical Live ISO:

1. open the same CogSheet™ application code used by the online preview;
2. load a bounded `.hyx` workbook;
3. discover a local experimental capability manifest;
4. submit one typed capability through the adapter;
5. display its result, locality and execution receipt in the originating cell;
6. return an explicit error for unsupported work without hosted fallback;
7. complete the workflow with networking disabled.

Start with `numeric.summary.v1` as the conformance handshake. Add one useful,
rights-cleared document capability only after its package, adapter and output schema
pass the model-package and worker-isolation gates. This milestone aligns the
CogSheet™ local-bridge work with the ecOS graphical Live ISO without making either
roadmap dependent on the future distributed fabric.
