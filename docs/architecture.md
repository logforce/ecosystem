# Runtime and System Architecture

Status: proposed design. The first runtime scope remains the original Linux CPU
prototype; production requirements below are tied to the roadmap.

The [R1 implementation report](implementation-r1.md) identifies the current
working subset: a per-user mock daemon, now extended with
[sealed Linux shared buffers](shared-memory.md). The component table
below describes the target architecture, not services already delivered.

## 1. Components and Authority

| Component | Responsibility | Authority boundary |
| --- | --- | --- |
| libcogposix | C ABI, local validation, connection, shared-memory mapping | Calling application's identity |
| ecosd | Sessions, objects, queues, admission, accounting | Dedicated service identity; no arbitrary root execution |
| Registry service | Validate package metadata and resolve installed artifacts | Read approved catalogue; activation through separate administration path |
| Backend worker | Parse/load models and perform inference | Restricted filesystem and device access; network denied by default |
| Capability adapter | Pre/postprocessing and semantic mapping | Worker-side code with the same package trust requirements |
| Policy broker | Apply an allowlisted set of privileged operations | Small authenticated interface; no generic shell command endpoint |
| Update service | Verify and activate OS/model releases | Separate signing trust and administrator authority |
| Telemetry service | Collect scoped runtime measurements | No document or prompt collection by default |
| Optimization service | Propose resource-policy changes | Unprivileged; cannot change its enforcement envelope |
| Security analyzer | Score events and propose responses | Advisory initially; no direct administrator privileges |
| Desktop integrations | Invoke capabilities and present results | User-scoped access through existing application permissions |

The trusted computing base includes the kernel, drivers, enforcement services and
release trust roots. Rust reduces some classes of memory errors; it does not make
FFI, dependencies, authorization or device code inherently safe.

## 2. Execution Sequence

1. The client negotiates a protocol and authenticates through the local transport.
2. The daemon establishes identity, quotas and an isolated handle namespace.
3. The client opens a specific approved artifact; later profiles allow capability resolution.
4. The registry returns a pinned artifact and adapter/runtime compatibility record.
5. The client allocates or imports buffers and describes tensor views.
6. The daemon validates bounds, identities, dependencies and mandatory constraints.
7. Admission checks reserve resources or reject bounded queue overflow.
8. Accepted jobs retain model and buffer references; the scheduler chooses dispatch order.
9. An isolated worker executes the job using an existing inference engine.
10. Completion publishes output shape/validity and releases internal references.
11. The client reads results and releases its handles; disconnected clients are reclaimed.

No request may cause a package download, arbitrary path load or network connection
merely because a model manifest requests it.

## 3. Control and Data Planes

The initial control channel is a versioned Unix-domain socket protocol. It carries
identifiers, descriptors, options and errors. Tensor payloads use Linux shared
memory with descriptor passing. The daemon must validate the received descriptor,
size, access mode and expected backing type before import.

Shared host memory removes a particular serialization path. It does not establish
end-to-end zero-copy: preprocessing, layout conversion and accelerator transfers
may still copy data. Count each transfer in benchmarks.

GPU/NPU imports belong to later backend-specific extensions. A host pointer is
never a portable device handle. Device synchronization and buffer ownership must
be explicit; a device fence may outlive the client request.

## 4. Ownership and Isolation

- A client owns its public handles; the daemon owns the authoritative object records.
- A tensor retains its backing buffer. An accepted job retains its inputs, outputs and model.
- Closing a handle revokes that client's future use without freeing still-referenced memory.
- Private state, inputs, outputs and generated caches cannot be shared implicitly.
- Sharing across clients requires an explicit scoped grant with defined permissions.
- Handle type, session, generation and access checks happen on every operation.
- Quotas cover pending jobs, descriptors, mapped memory and resident model costs.

Unix UID authentication distinguishes users, but does not fully isolate hostile
applications within the same user account. Strong per-application policy requires
a sandbox identity and a trusted launcher/portal integration. PID strings supplied
by a client are not sufficient evidence of identity.

Writable shared inputs create time-of-check/time-of-use hazards. Metadata must be
copied into trusted control state. A cooperative client profile can prohibit input
mutation until completion; an untrusted-client profile needs enforceable ownership
transfer, sealing where applicable, or a validated private snapshot. The secure
profile may deliberately incur a copy.

## 5. Scheduling and Memory Policy

The first scheduler uses FIFO within authorized priority classes. Limit queue
length and concurrent jobs, add aging or another specified starvation bound, and
reserve capacity for system maintenance. Requests for elevated priority must be
authorized; every client calling itself REALTIME defeats arbitration.

Deadline metadata is a requirement or target as explicitly declared by the caller.
The runtime must report unsupported guarantees. It must not describe nonpreemptive
execution with a priority queue as hard real-time scheduling.

Centralized scheduling applies to work submitted through ecOS. Nonparticipating
programs may still consume devices. A controlled OS image can restrict direct
device access for selected sandboxed apps; a runtime installed on an arbitrary
desktop cannot assume exclusive global control.

Backend-managed CPU pools can oversubscribe the host. Measure worker concurrency,
thread count and locality jointly. Shared immutable weight residency is a separate
optimization from reusing a loaded session; execution caches may remain private
and backend-dependent.

## 6. Failure Behavior

| Failure | Required behavior |
| --- | --- |
| Client disconnect | Stop new work, cancel queued jobs, reclaim after in-flight access ends |
| Worker crash | Fail affected jobs, invalidate its device imports, restart under a bounded policy |
| Daemon restart | Existing handles invalid; clients reconnect explicitly; no automatic replay of side effects |
| Device reset | Mark affected work failed and re-enumerate before new dispatch |
| Out of memory | Reject admission or fail the affected job; preserve recovery and policy services |
| Model load failure | Structured error with pinned artifact/runtime diagnostics |
| Deadline missed | Report timing outcome; do not falsely mark success as deadline-compliant |
| Cancel unsupported | Report noncancellable state/result; continue retaining memory |
| Telemetry failure | Preserve baseline execution and enforcement; disable learning |

The system needs request identifiers and a bounded duplicate-request policy.
Inference retries must be explicit. An application may use an inference result to
perform a side effect, so automatic replay is not universally harmless.

## 7. Observability

Record model and capability version, device/backend, queue/execute/transfer times,
resource peaks, copies, policy decision, status and error class. Separate public
client diagnostics from administrator-only information about other workloads.

Sampling, rotation and storage quotas must prevent telemetry from becoming an
availability problem. Detailed traces are opt-in and may contain sensitive
metadata even without payload content. Model outputs and documents are excluded
from default logs and crash reports.

## 8. Backend Strategy

Start with a deterministic mock backend, then ONNX Runtime CPU. Reuse its execution
provider mechanisms where suitable; they already abstract multiple accelerator
libraries. This is a dependency to build on, not a differentiator by itself.
[ONNX Runtime documentation](https://onnxruntime.ai/docs/execution-providers/).

The internal backend contract covers enumerate, load/unload, submit, completion,
cancel support, import/export and diagnostic capabilities. Workers advertise
supported dtypes, layouts, dynamic shapes, synchronization and cancellation.
Unsupported features return explicit errors rather than silently changing data.

Language-generation features such as KV-cache management can be optional
extensions. They must not determine the core representation of audio, images,
signals or generic computation.

## 9. Optional Distributed Intelligence

The planned [CogPOSIX fabric](distributed-intelligence.md) connects enrolled
workstations, AI nodes and edge devices within an ecOS policy domain. It is an
opt-in brokered transport, not an extension of local shared memory or implicit
worker networking. Another machine always remains another execution boundary.
Whole-job placement precedes multi-node pipelines or distributed training.

Execution authority, knowledge-service access and model improvement are separate
planes. DuckDB/Qdrant are candidate services behind scoped authorization, not a
globally shared database file. Actual placement, transfers, revocation and uncertain
outcomes must be exposed through a future versioned contract. Current runtime
behavior and ABI are unchanged.
