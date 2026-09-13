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

Environments: macOS Darwin x86-64 and Linux x86-64, Rust 1.90.0. No downloaded
model, external inference service or API token is involved. This record is functional evidence,
not a latency, energy, commercial saving or security certification benchmark.

| Check | Local result |
| --- | --- |
| Offline workspace build | Passed |
| Rust tests | 20 passed on macOS and Linux; one ignored subprocess helper is exercised by the crash test |
| rustfmt and Clippy with warnings denied | Passed |
| CLI and compiled C smoke suite | Passed, including graceful SIGTERM shutdown |
| Publication tooling tests | 14 passed |
| Publication allowlist validation | Passed, 54 files; no upload performed |

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

Linux execution was verified after starting Docker Desktop. The repeatable
[runner](../scripts/validate-linux.mjs) validated the public snapshot with digest
`28485edec7d546f8242075738ab9f812ea49aa7226df8408015aa2ff5db72d0e`
before these documentation-only evidence updates. Its base was implementation
commit `321abac` plus the new validation harness and two additional runtime tests.

Linux profile: Debian Bookworm userland, kernel 5.15.49-linuxkit, x86-64, Rust
1.90.0, Node.js 22.19.0 and GCC 12.2.0. Docker Engine 20.10.21 ran the suite as UID
10001 with networking disabled, a read-only image, no added capabilities, two CPUs
and a 2 GiB memory limit. Toolchain-image construction used network downloads;
project compilation and all tests ran offline. The full build, formatting,
Clippy, Rust suite, compiled C/CLI smoke suite and publication suite passed.

The additional stress test submits 256 jobs across four concurrent clients with
varied cancellation timing. Completed jobs must expose the expected output;
cancelled jobs must leave the sentinel output unchanged. Both retain a stable
terminal state and release admission capacity. The global-memory test fills the
64 MiB backing quota across eight sessions using tensor-retained buffers after
their buffer handles close, rejects a ninth session's allocation, then verifies
that releasing a tensor restores exactly the freed allocation capacity.

The container harness needed explicit temporary-directory permissions for the
host's restrictive umask and this older Docker engine. No runtime permissions or
non-root restrictions were relaxed. This is one Linux container profile, not
bare-metal, distro-wide, cross-UID security or installable-OS conformance.

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

1. Review the experimental ABI/protocol before interface freeze; retain both
   Linux and macOS validation on future changes.
2. Expand bounded stress into longer adversarial campaigns and cross-UID tests.
3. Implement R2 descriptor-passed shared host memory, including race-safe bounds
   and permissions validation, while preserving the ownership tests.
4. Implement R3 as an isolated ONNX CPU worker with a reviewed fixed model artifact
   and reference-output tests; do not expose arbitrary model-file loading.

The [OS plan](operating-system.md) remains downstream of runtime correctness.
This prototype establishes a testable foundation; it does not justify moving
inference or learned policy into the kernel.
