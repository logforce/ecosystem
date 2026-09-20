# Distributed Intelligence, When You Want It

Status: planned architecture, not an implemented runtime feature. The current
daemon remains local-only. No remote transport, database service, cluster or
distributed training capability is installed by this proposal.

## Local-First Does Not Mean Isolated

ecOS is designed so that execution may be distributed across trusted nodes when
distribution has been explicitly enabled. An organization could connect
workstations, dedicated AI nodes and edge devices within an ecOS policy domain.
Applications would continue to use a common capability contract while policy
determines which resources and data may participate.

```text
                      ecOS Policy Domain
                             |
             +---------------+---------------+
             |               |               |
        Workstation       AI Node         Edge Node
         GPU / NPU       GPU / NPU       Accelerator
             |               |               |
             +---------------+---------------+
                             |
                      CogPOSIX fabric
```

"CogPOSIX fabric" names the proposed policy-controlled execution interconnection,
not a new network protocol already implemented, a shared address space or a claim
that all accelerators support interchangeable workloads. GPUs/NPUs remain subject
to supported drivers, model profiles and backend validation.

From an endpoint's perspective, another machine is another execution boundary.
A LAN, organization-owned server or private cloud is still off-device execution.
Organizational sovereignty can encompass several approved nodes; endpoint-local
execution is a narrower guarantee. Neither is a synonym for the other.

## Three Independent Permissions

| Plane | What participation permits | What it does not permit |
| --- | --- | --- |
| Execution | Dispatch an authorized job and its necessary inputs to an approved node | Read arbitrary databases or train on inputs |
| Knowledge and data | Query an authorized dataset, collection or snapshot for a stated purpose | Replicate everything, bypass row/document access rules, or change model weights |
| Improvement | Use explicitly approved examples/feedback in evaluation or a training workflow | Automatically promote a model or relax security/locality policy |

All three are off by default across machines. An administrator enabling execution
does not silently enable shared knowledge or training. Applications may tighten
constraints; they cannot weaken mandatory system, tenant or dataset rules.

## Policy Domain and Placement

Node participation, authentication, capability availability, data classes,
execution locality and trust relationships belong to system policy. The proposed
resolver first intersects permissions and locality constraints, then optimizes
only among eligible placements. No eligible placement means an explicit failure
or authorized queueing, never an undisclosed cloud fallback.

Required decisions include:

- Enrolled node identities, operator ownership, tenants and approved trust roots.
- Mutually authenticated encrypted connections, key rotation, revocation and expiry.
- Approved model/adapter digests, semantic profiles, device support and resource limits.
- Permitted data classes, destinations, purposes, retention and onward transfers.
- Per-application authorization mapped to remote identity; local Unix UIDs are not
  remote credentials, and discovery advertisements are not authorization.
- Default endpoint-local mode; an explicit trusted-domain mode may allow named nodes.
  Any external-provider mode requires separate configuration and disclosure.
- Constraints on preprocessing, retrieval, execution, output storage, diagnostics
  and learning: every stage must satisfy the applicable data policy.
- Visible actual node identity, local/off-device status, model version, policy
  decision and transfer/accounting metadata without logging payloads by default.

Successful authentication establishes identity, not proof that a node is safe.
Compromised administrators, nodes and accelerators remain threat-model concerns.
Hardware attestation, where available, is a separate assurance mechanism with its
own limitations, not a substitute for authorization or isolation.

## First Execution Profile

Start with whole-job placement on one selected node, not tensor/model parallelism
over arbitrary networks. Preserve the capability's input/output semantics while
making the execution boundary visible. Multi-node pipelines come later, with
explicit policy checks on every transfer and intermediate result.

The local CogPOSIX interface can remain the application's entry point, but remote
identity, placement, transfer and failure metadata need a separately versioned
extension. The current wire 1.1/C ABI 0.2 is not claimed to implement it. Linux
file descriptors, pointers and sealed mappings do not cross machines as usable
handles; transport must serialize or explicitly transfer bounded, authenticated
data. No cross-machine zero-copy claim follows from local shared memory.

A broker, not the model worker, owns network access. Enforce admission and quotas
at both ends. Bound uploads, result sizes, queue times, deadlines and retained
state. Define idempotency keys, replay rejection and cancellation acknowledgments.
A partition after dispatch can leave the outcome unknown: report that uncertainty,
do not report successful cancellation or silently duplicate side effects. Node
revocation blocks new transfers; already received data cannot be recalled by
revoking a credential. Define expiry, cleanup and late-result rejection.

## Governed Knowledge Services

DuckDB and Qdrant are candidates, not mandatory components or shared database-file
protocols. Keep their credentials and query authority outside model workers.

| Candidate | Proposed role | Initial access pattern |
| --- | --- | --- |
| DuckDB | Structured analytics, evaluation records and feature preparation | Node-owned database through a constrained service, or approved immutable snapshots |
| Qdrant | Embeddings, semantic retrieval and approved knowledge collections | Authenticated scoped queries; separately enabled replication where required |

For the initial DuckDB profile, use one owning writer service rather than a
network-mounted mutable database file shared by many machines. Other concurrency
or client-server modes need a separate version and security evaluation. Consult
the [DuckDB concurrency guidance](https://duckdb.org/docs/stable/connect/concurrency.html).

Qdrant already supports [distributed deployment](https://qdrant.tech/documentation/scaling/distributed_deployment/),
but replication does not implement ecOS locality or tenant policy. Configure
[authentication, TLS and network restrictions](https://qdrant.tech/documentation/security/)
explicitly before exposing any service. Query service authorization must enforce
the original document/dataset permissions; a collection name or a client-supplied
filter is not an adequate authorization boundary by itself.

Prefer scoped queries or immutable snapshots over blanket synchronization.
Record schema, source version, content provenance and the embedding model/profile
version. Do not mix incompatible embedding spaces. Define consistency and
freshness, deletion/revocation propagation, replica/cache invalidation, encryption
and backup retention. Embeddings and query results may themselves be sensitive.

Cross-company collaboration requires a separate explicitly authorized federation
with purpose limits and tenant separation. Enrollment in one network never grants
access to another business's data. Treat shared-data products as separate services,
not as an automatic consequence of installing ecOS.

## Improving Models Without Implicit Training

Retrieving better context can improve application answers without changing model
weights. DuckDB/Qdrant do not make a model learn merely by storing information.
Separate retrieval quality, model selection, threshold tuning, fine-tuning and
federated learning in both the implementation and marketing claims.

Begin with permissioned retrieval and offline evaluation. A later improvement path
requires eligible source data, explicit purpose/rights, user/tenant policy,
provenance, contamination checks, a held-out evaluation set and versioned artifacts.
Candidates pass independent quality/security checks before approved activation,
with rollback available. Production prompts, outputs and telemetry are not training
data by default. Updates, gradients and adapters can leak data or carry poisoning;
federated learning is not automatic anonymization. Deleting a source record does
not demonstrate removal of its influence from already trained weights.

## Adoption and Commercial Value

Potential uses include an office GPU shared by authorized workstations, local
document/search services, on-premises analytics and edge processing that exports
only authorized results. The value hypothesis is better use of owned infrastructure
and governed access to specialized capabilities, not free computation or guaranteed
latency/cost savings. Measure network overhead, utilization, energy, queueing and
administration costs against local-only and existing serving systems.

Distributed inference itself is established technology; for example,
[Ray Serve](https://docs.ray.io/en/latest/serve/index.html) provides distributed
serving infrastructure. The ecOS opportunity is the combination of application
contracts, visible execution boundaries, deterministic policy and an OS delivery
path. Reuse existing components where they meet those contracts.

Keep basic distributed execution and policy in the community foundation. Optional
supported deployments, fleet administration, maintained model/device profiles and
enterprise operations can provide commercial value. LOGFORCE remains optional
and separately licensed; neither cluster participation nor data access requires it.

## License Direction

Planned project-owned community fabric, policy and basic data adapters: Apache-2.0
when implemented and explicitly released. This specification: CC BY 4.0.
[DuckDB](https://github.com/duckdb/duckdb/blob/main/LICENSE) is MIT and the
[Qdrant open-source engine](https://github.com/qdrant/qdrant/blob/master/LICENSE)
is Apache-2.0; cloud offerings, extensions, data and model assets retain their own
terms. No DuckDB/Qdrant binaries, datasets or integration code are shipped here.

## Evidence Gates

1. Two-node whole-job execution after R4/R5 and approved capability profiles:
   enrollment, authentication, downgrade/egress denial, explicit placement and
   no network activity in endpoint-local mode.
2. Failure and isolation tests: revocation, partitions, replay, cancellation races,
   remote resource exhaustion, tenant crossover and compromised-node responses.
3. Knowledge-service evaluation: authorization, freshness, provenance, deletion,
   replication boundaries and cross-tenant retrieval leakage.
4. Optional learning study only after those gates: held-out benefit, poisoning
   resistance, independent promotion/rollback and data-use controls.

There is no requirement to complete this distributed track before delivering a
useful standalone runtime or local OS preview.
