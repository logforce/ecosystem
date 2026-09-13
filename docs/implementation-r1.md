# R0/R1 Implementation Record

Date: 13 September 2026. Status: experimental mock runtime, not a release or full
milestone sign-off. The [development guide](development.md) supplies reproduction
commands; the [protocol](protocol.md) describes the implemented contract.

## Implemented

- Seven-crate Rust workspace with no third-party dependencies.
- Per-user daemon, bounded binary transport, peer UID checks and handshake.
- Session-owned typed handles; retained buffer/tensor/job references and quotas.
- One FIFO worker, asynchronous state transitions and cooperative cancellation.
- Deterministic U8 mock backend, including wrapping arithmetic and test delay.
- Rust client, callable experimental C ABI, CLI and compiled C example.
- Graceful daemon shutdown, disconnect cleanup and abrupt client-crash tests.
- Explicit public-source allowlist under the existing Apache-2.0 license scope.

## Evidence

Local environment: macOS, Darwin x86-64, Rust 1.90.0. No downloaded model, external
inference service or API token is involved. This record is functional evidence,
not a latency, energy, commercial saving or security certification benchmark.

| Check | Local result |
| --- | --- |
| Offline workspace build | Passed |
| Rust tests | 18 passed; one ignored subprocess helper is exercised by the crash test |
| rustfmt and Clippy with warnings denied | Passed |
| CLI and compiled C smoke suite | Passed, including graceful SIGTERM shutdown |
| Publication tooling tests | 14 passed |
| Publication allowlist validation | Passed, 52 files; no upload performed |

The Rust suite covers framing, bounds/overflow, independent clients, cross-session
handle rejection, pinned-resource lifetime, timeouts, queued/running cancellation,
resource admission, malformed handshakes, disconnect cleanup, abrupt client death,
backend panic containment and socket shutdown. The C smoke suite links the built
shared library and checks layout, invalid arguments, ownership, size queries and
concurrent wait/cancel behavior. The CLI verifies byte results and zero retained
resources after explicit cleanup.

During verification, accepted sockets on macOS inherited nonblocking mode from
the listener and caused premature disconnects. Accepted streams now explicitly
switch to blocking I/O before receiving requests; the integration suite passes
with that fix. Tests are intended to detect lifecycle regressions, not establish
exhaustive race freedom.

Linux execution is not yet verified. The local Docker daemon is unavailable, and
macOS success must not be represented as Linux conformance. Linux CI or a disposable
Linux VM must repeat the build, Rust suite and C smoke suite before R1 acceptance.

## Deliberate Boundaries

R1 uses bounded inline copies, not shared host memory or zero-copy IPC. Tensors
support only contiguous U8, fixed output sizes and one input/output per job.
There is no scheduler priority policy, dynamic shape negotiation, stable extension
registry, model registry, real inference engine or accelerator support.

The trusted mock runs inside the daemon. A caught Rust backend panic becomes a
job failure, but this is not process crash containment, native-code isolation or
protection from a noncooperative backend. Real models require isolated workers
before exposure to untrusted clients. Same-UID peer checks do not distinguish or
authorize mutually hostile applications belonging to the same desktop user.

There is no model learning, adaptive kernel access, cybersecurity detection,
telemetry upload, remote fallback, OS installer, update service or bootable image.
No production hardening, performance benefit or ABI stability is claimed.

## Next Acceptance Work

1. Repeat this evidence on Linux x86-64 and review the experimental ABI/protocol.
2. Expand adversarial cancellation/completion stress and global memory-pressure tests.
3. Implement R2 descriptor-passed shared host memory, including race-safe bounds
   and permissions validation, while preserving the ownership tests.
4. Implement R3 as an isolated ONNX CPU worker with a reviewed fixed model artifact
   and reference-output tests; do not expose arbitrary model-file loading.

The [OS plan](operating-system.md) remains downstream of runtime correctness.
This prototype establishes a testable foundation; it does not justify moving
inference or learned policy into the kernel.
