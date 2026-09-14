# R2 Sealed Shared Memory

Status: experimental Linux implementation, 13 September 2026. This is an immutable
input and snapshot-output subset of R2, not a general writable-mapping API or a
fully zero-copy inference pipeline. The [wire contract](protocol.md) is version
1.1 and the [C ABI](../include/cogposix/cog.h) is version 0.2. Rebuild both endpoints;
old wire 1.0 clients are intentionally rejected. macOS keeps inline execution and
returns Unsupported for shared-memory operations.

## Ownership Contract

1. A client creates and populates a Linux memfd. The Rust `SharedBuffer::from_bytes`
   helper performs these steps for an existing byte slice.
2. Before import, the file must have all four seals: `F_SEAL_WRITE`, `F_SEAL_GROW`,
   `F_SEAL_SHRINK` and `F_SEAL_SEAL`. A writable shared mapping must be unmapped
   before full write sealing can succeed. `F_SEAL_FUTURE_WRITE` alone is not enough.
3. Import sends a duplicate file reference with `SCM_RIGHTS`, plus the exact size.
   The daemon checks a seal-capable tmpfs backing, the full seal set, regular-file
   metadata, exact length and quotas before accepting a read-only mapping. Seal
   validation precedes size inspection so a sender cannot race validation by
   shrinking the file. Huge-page files and ordinary disk files are not accepted.
4. The new handle is session-owned and input-only. Tensors and accepted jobs retain
   its backing mapping after the client closes its own file or buffer handle.
   Writes or submission as an output return Permission. Reimporting the same file
   creates a new handle and conservatively charges its full size again.
5. The backend borrows the immutable mapped input without an input snapshot copy.
   Existing cancellation and terminal-state rules still apply. The mock backend
   allocates a result, which is copied into private daemon output storage only if
   completion wins the cancellation race.
6. Export creates a new, fully sealed snapshot of an unpinned buffer and sends its
   descriptor to the client. It is not a live view. Later jobs or writes cannot
   change it. The client owns the received descriptor and must close it; any client
   mapping must also be unmapped. A snapshot can outlive the context and daemon.

The write and size seals provide the mutation boundary, rather than a client-side
promise to respect Busy. This follows the Linux
[memfd sealing model](https://man7.org/linux/man-pages/man2/memfd_create.2.html) and
[seal definitions](https://man7.org/linux/man-pages/man2/F_ADD_SEALS.2const.html).

Mappings use `PROT_READ` with `MAP_PRIVATE`: read-only private mappings of
immutable file pages, with no writable slice exposed by the Rust API. This avoids
the observed EPERM for read-only `MAP_SHARED` mappings of write-sealed O_RDWR files
on the tested 5.15.49 kernel. Private mapping here does not eagerly copy the file;
it is not a shared writable output mechanism. Related kernel behavior is recorded
in the upstream [read-only sealed mapping fix](https://www.spinics.net/lists/stable/msg875614.html).

## Descriptor Transport

The first byte of a frame is sent separately with at most one descriptor. Linux
receivers read that byte independently and use `recvmsg` for every subsequent
read. Extra descriptors, misplaced rights and truncated ancillary data are rejected;
every received descriptor is closed on failure. Accepted descriptors have
close-on-exec set atomically. Import requires one descriptor; all other requests
require none. Only a successful export response contains a descriptor.

The small [C shim](../crates/cog-shm/src/linux.c) uses system socket headers and
`CMSG_*` macros rather than reproducing native ancillary layouts in Rust. The
protocol header itself remains explicit little-endian bytes. See the Linux
[ancillary-data API](https://man7.org/linux/man-pages/man3/cmsg.3.html).

## Limits and Copies

The existing limits remain: 1 MiB per buffer, 8 MiB per session and 64 MiB of
daemon-retained backing buffers. Imported mappings remain charged while tensors
or jobs retain them. Failed imports release their received descriptor and do not
consume object or buffer quota.

These are not total-RSS or system-wide memory limits. Export creates a transient
snapshot, then transfers ownership to the client. Client-retained snapshot files,
client allocations, protocol framing, page tables and backend result allocations
are not charged as daemon buffer handles. A process can retain many exported
snapshots; deployment needs OS-level user/process memory limits. Same-UID access
is still not hostile-application sandboxing.

| Stage | Implemented behavior |
| --- | --- |
| Build shared input from an existing byte slice | One population write into the memfd |
| Import into daemon | Descriptor and size only; no tensor bytes in the socket |
| Backend access to imported input | Borrowed immutable mapping; no input snapshot copy |
| Mock output | Allocated result copied to daemon buffer |
| Export | Buffer copied into a new sealed memfd |
| Client access to export | Read-only mapping; no tensor bytes in the socket |

Shared memory is explicit. Existing `buffer_alloc/write/read` calls retain their
inline behavior. The C functions `cog_buffer_import_fd` and `cog_buffer_export_fd`
expose borrowed-input and owned-output descriptors respectively. Use
`cog_context_features` to discover support. The [C test](../tests/c-shm.c) provides
a compilable complete lifecycle; the Rust client exposes the corresponding file
operations and `SharedBuffer` mapping helper.

## Verification and Measurement

The non-root, offline Linux container passed the build, Clippy, formatting, 26 Rust
tests, compiled C/CLI tests and 14 publication tests. One ignored Rust helper is
executed by the abrupt-client-crash test. Coverage includes mapping seals, exact
size rejection, input-only permissions, retained ownership, quota recovery,
running cancellation and snapshot validity after disconnect. The ancillary C test
performs 500 transfers with 1/2/4/8/16 descriptors, checks excess and truncation
rejection, and verifies unchanged process descriptor counts after each transfer.
Protocol tests also check malformed frames and rights attached mid-frame.

The [benchmark](../crates/cog-cli/src/bench.rs) compares 1 MiB buffer round trips,
not inference: inline allocate/write/read/free versus sealed create/import/export/
map/free. It alternates modes, discards two warmups per mode, and records 16 samples.
Validation runs an unoptimized debug build. Input allocation/population, descriptor
operations and output snapshot creation are timed; result comparison is outside
the timing. Object destruction after the recorded endpoint and total RSS are not
measured. The workloads have different ownership semantics and request counts.

One observed run on Linux 5.15.49 x86-64, Debian Bookworm, Rust 1.90.0, two container
CPUs and a 2 GiB memory limit produced:

| Mode | P50 microseconds | P95 microseconds | Application frame bytes per round trip |
| --- | --- | --- | --- |
| Inline | 13,374 | 15,030 | 2,097,416 |
| Sealed | 1,615 | 2,669 | 200 |

The frame counts include request/response headers and payloads, but exclude kernel
ancillary metadata and memory traffic. They establish control/data separation,
not zero-copy execution. The timing sample is small and host-dependent; it is not
a production speedup, energy-saving or commercial-cost claim. The measured public
snapshot was `e9675414684ecb8fc80e6436077e02cd98d174f1b07a5e2dd09fd6313dc34a44`
before the subsequent documentation and additional crash/fallback checks. Reproduce with
`node scripts/validate-linux.mjs` or the manual command in the
[development guide](development.md).

## Remaining Work

The [R2 review checkpoint](r2-review.md) records the reviewed boundaries, follow-up
fix and sanitizer coverage before the next milestone.

Longer adversarial stress, cross-UID isolation tests, wider kernel coverage and
independent ABI/security review remain outstanding. Mutable mappings, output
buffer rotation, device memory and dynamic shapes are not implemented. The next
inference milestone remains an isolated ONNX CPU worker with a reviewed model and
reference-output tests. This work does not add models, learning or an OS installer.
