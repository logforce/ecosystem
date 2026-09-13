# Experimental R1 Protocol

This document describes the implemented mock transport, not the complete
[CogPOSIX design](cogposix.md). Wire version is 1.0; the C ABI is experimental 0.1.
Both may change with an explicit version revision before interface freeze.

## Transport and Framing

Each connection is one session on a Unix-domain stream socket. The daemon accepts
only peers with its effective user ID. The socket is mode 0600 in an owner-only
directory. The service is per-user: separate connections have isolated handles,
but hostile applications sharing that user ID are outside the isolation claim.

All integers are little-endian; the header is exactly 28 bytes, encoded field by
field rather than copied from a native struct.

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | 4 bytes | ASCII `COGP` |
| 4 | u16 | Major version, 1 |
| 6 | u16 | Minor version, 0 |
| 8 | u64 | Positive, strictly increasing request ID |
| 16 | u16 | Opcode |
| 18 | u16 | Flags: 0 request, 1 response; other bits invalid |
| 20 | u32 | Payload length, at most 1 MiB + 128 bytes |
| 24 | i32 | Status: 0 success/request; error value on response |

Responses echo opcode and request ID. Errors have empty payloads. Bad framing,
unsupported versions, invalid session handshake or repeated/out-of-order IDs
close the connection and release session resources. A well-framed request with
an unsupported opcode receives an error. Payloads must be consumed exactly;
trailing bytes and malformed fields are rejected. No pipelined client API is
provided; each context serializes request/response exchanges.

## Messages

`h` is a session-owned u64 handle. `bytes` means the remaining exact payload.
No descriptor passing or shared memory exists in R1.

| Opcode | Request | Success response |
| --- | --- | --- |
| 1 HELLO | Empty; required once as first request | Feature bits u64 (1 = inline mock), max buffer u32, max rank u32 |
| 2 STATS | Empty | Object count u32, backing buffer bytes u64, retained jobs u32 |
| 10 MODEL_OPEN | UTF-8 byte length u32, name bytes, at most 128 | h |
| 11 MODEL_CLOSE | h | Empty |
| 20 BUFFER_ALLOC | Size u32, nonzero | h; initially zeroed |
| 21 BUFFER_WRITE | h, byte length u32, bytes | Empty; must fill the entire buffer |
| 22 BUFFER_READ | h | Entire buffer bytes |
| 23 BUFFER_FREE | h | Empty |
| 30 TENSOR_CREATE | Buffer h, offset u64, rank u32, dimensions u64[rank] | Tensor h |
| 31 TENSOR_RELEASE | h | Empty |
| 40 SUBMIT | Model h, input tensor h, output tensor h, delay milliseconds u32 | Job h |
| 41 JOB_STATUS | h | State u32, terminal error u32 (0 if none) |
| 42 JOB_CANCEL | h | Empty |
| 43 JOB_RELEASE | h | Empty; terminal jobs only |

The only model is `mock.increment.v1`. It maps every input byte to that byte plus
one modulo 256. Delay is a test facility, limited to 5,000 ms. There is no model
file loading, network backend, capability discovery or model inference.

## Ownership and Execution

Tensors are contiguous U8 views, rank 1 through 8, with nonzero dimensions and a
checked product plus offset within the backing buffer. Submission requires one
input and one output with equal shapes and distinct backing buffers. The caller
allocates fixed output storage; dynamic-size outputs are not implemented.

Handles are typed and connection-owned. Closing a handle removes the caller's
reference; tensors retain their buffer, and accepted jobs retain tensors, model
and buffers until backend access ends. Thus early handle closure cannot cause
use-after-free or release memory quota prematurely. IDs are not recycled during
a daemon's lifetime. Stale or foreign handles are invalid, not transferable tokens.

Accepted jobs pin both buffers exclusively. Buffer reads and writes return Busy
until pinning ends; a second job using either buffer is also Busy. One trusted
worker processes a bounded FIFO queue. No priorities or fairness guarantee exist.

States: Queued=1, Running=2, Completed=3, Failed=4, Cancelled=5. The last three are
terminal. Queued cancellation removes work and releases pins. Running cancellation
is cooperative: terminal cancellation is published only after backend access ends.
Completion and cancellation are serialized, and cancelled output is not committed.
Cancelling an already terminal job succeeds without changing its state. Job release
before terminal status returns Busy. Disconnect cancels work and drops session
handles; in-flight worker references persist until the backend stops.

## Limits and Timing

| Limit | R1 value |
| --- | --- |
| Sessions | 32 |
| Objects per session | 128, across all handle types |
| Jobs per session | 16, including retained terminal jobs |
| Buffer size | 1 MiB |
| Backing bytes per session | 8 MiB |
| Backing bytes across daemon | 64 MiB |

Memory quotas count retained backing buffers, not total process RSS. Transport
frames, buffer copies, worker snapshots and bookkeeping consume additional bounded
memory. Release terminal jobs promptly to restore admission capacity.

Waiting is a client-side monotonic poll, normally every 2 ms; it is not a separate
wire request. Timeout zero polls once. A wait timeout does not cancel a job.
The client's 5-second socket timeout can outlast a shorter requested wait, so this
is not a hard deadline API. The daemon has a 120-second read timeout and a 5-second
write timeout; these are per-I/O safeguards, not a total frame deadline or a
complete denial-of-service defense. An idle connection can therefore expire.

The client poisons a disconnected or protocol-invalid transport and does not retry
or reconnect automatically. Applications must create a new context; old handles
do not survive. The Rust client rejects reuse after fork. C applications must exec
in a forked child before using the library because inherited process locks are not
fork-safe. Destroy a C context only after its concurrent calls have finished.

## Errors and ABI

Error numbers: Invalid=1, NoMemory=2, NoModel=3, Busy=4, Timeout=5, Cancelled=6,
Permission=7, Backend=8, Io=9, Unsupported=10, Disconnected=11. The
[core definitions](../crates/cog-core/src/lib.rs) and
[C header](../include/cogposix/cog.h) share these values.

Every C resource call takes an explicit context. The tensor descriptor is 88 bytes:
four u32 fields (`struct_size`, `dtype`, `rank`, `flags`), offset u64, then eight u64
dimensions. Dtype 1 means U8, flags must be zero, and unused dimensions must be zero.
The [C integration test](../tests/c-abi.c) checks layout and lifecycle behavior.
This is not a stable backend plugin ABI: backends currently implement an internal
[Rust trait](../crates/cog-backend-api/src/lib.rs).
