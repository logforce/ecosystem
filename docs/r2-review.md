# R2 Review Checkpoint

Date: 13 September 2026. Reviewed baseline: `f554cb5`.
Scope: the [sealed-input and snapshot-output subset](shared-memory.md), not a
production security audit or independent review. The implementation author
performed this review; external review remains required before release.

## Findings

The daemon startup message still reported protocol 1.0 although framing requires
1.1. The message now reads the protocol constants, avoiding a misleading version
diagnostic. No blocking ownership or descriptor-lifetime defect was identified
in the reviewed subset; this is not proof of absence of defects.

## Checked Boundaries

| Boundary | Reviewed behavior |
| --- | --- |
| Import validation | Size bounds, tmpfs backing, full write/size/seal checks, exact file extent before mapping |
| Rust mapped slices | Immutable sealed backing; no safe mutable view; mapping lifetime retained by owning references |
| Ancillary parsing | Native system-header layouts; received descriptors closed on surplus, truncation and malformed frame rejection |
| Exec inheritance | Descriptor receipt and explicit duplication use close-on-exec |
| Accepted jobs | Retain input/output resources; terminal status follows backend access and unpinning |
| Cancellation | Output commit serialized with cancellation; cancelled output remains unchanged |
| Quotas | Retained daemon buffers remain charged; reimports charge full size conservatively |
| Export ownership | New sealed snapshot; client closes descriptor and unmaps its own view; no live writable output alias |
| C callers | Valid pointers/storage and call lifetime remain caller responsibilities, not guarantees provided by pointer checks |

## Additional Verification

The Linux smoke suite now also builds the ancillary transport test with AddressSanitizer
and UndefinedBehaviorSanitizer. Both halt on errors. LeakSanitizer is disabled
because its tracing requirements vary in restricted containers; the test independently
checks open descriptor counts after each of 500 transfers. This covers the C
descriptor shim and its C test, not an instrumented build of the Rust runtime.

Result: passed on Linux x86-64, kernel 5.15.49-linuxkit, Rust 1.90.0 and GCC
12.2.0 in the existing non-root, offline validation container. All 26 Rust tests,
C/CLI smoke checks, 14 publication tests and the sanitizer run passed. One ignored
Rust helper was exercised by its parent crash test. The verified snapshot digest
was `278003713320e90ae8a0fa450c2ad1b988faca33f63bda58140701d3a8ed397e`
before this documentation-only result annotation.

Repeat the complete check with `node scripts/validate-linux.mjs`. The existing
Rust/C checks cover imports, wrong extents, shared-input permissions, descriptor
placement, truncation, cancellation, crash cleanup and macOS unsupported behavior.

## Limits Retained

- Same-UID socket access does not isolate hostile applications running as the same user.
- Exported client-owned snapshots and backend/transport temporaries are outside
  daemon buffer-handle quotas; deployment needs OS-level resource limits.
- Per-I/O timeouts are not total message deadlines or comprehensive DoS protection.
- Shared-memory feature availability is platform-advertised; syscall restrictions
  or unsupported kernels can still make a particular operation fail.
- The trusted mock remains in-process. Real-model parsing must move to a separate,
  restricted worker before exposing it to untrusted clients.
- Mutable output mapping, accelerator memory and ABI freeze remain out of scope.

## Sequence

1. R2 checkpoint: commit, review, fixes and validation recorded here.
2. R3: isolated ONNX CPU worker, one reviewed model and reference-output tests,
   including termination and recovery after worker failure.
3. One useful application through CogPOSIX, exercising actual model semantics.
4. Direct-versus-CogPOSIX measurements for that workload, including concurrency.
5. Existing-Linux packaging, service lifecycle and updates; a bootable OS follows.

Each step requires its own evidence and commit. This checkpoint does not claim
that steps 2 through 5 have already been implemented.
