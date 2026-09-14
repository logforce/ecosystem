# Runtime Development

This is an experimental local runtime, not an installable OS. The default backend
is a mock; the optional Linux [ONNX worker](onnx-worker.md) performs real inference.
Read that report and the historical [R1 limits](implementation-r1.md) before deployment.

## Prerequisites

- Rust 1.85 or newer with Cargo, rustfmt and Clippy.
- A C11 compiler, system headers and `ar` for native Linux builds; pthreads for C tests.
- Node.js 20 or newer for smoke tests and publication tooling.
- macOS or Linux with Unix-domain sockets; Linux x86-64 is the intended target.

There are no third-party Rust dependencies. Default builds/tests download no models
and run offline with the toolchain installed. Optional ONNX validation downloads
Python dependencies and one pinned model at image-build time. No API key is required.
The Linux shared-memory crate compiles a small C shim against the system's socket
and mapping headers. Cross-compiling that shim is not supported by the initial
build script; use a native Linux host or the validation container.

## Build and Verify

From the repository root:

```sh
cargo build --workspace --offline
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --offline -- -D warnings
cargo test --workspace --offline
node scripts/smoke.mjs
node --test tests/publication.test.mjs
node scripts/publication.mjs check
```

The runtime tests require permission to create local Unix sockets. A restricted
execution sandbox may reject socket binding even though compilation succeeds.
One ignored Rust test is a subprocess helper executed and killed by the crash
cleanup test; it should not be invoked directly without that test's environment.

The smoke script compiles and links the actual C shared library, runs the CLI and
C tests against a temporary daemon, tests graceful termination and removes its
temporary files. It does not leave a background service running.
On Linux it also checks sealed mappings, ancillary-message descriptor cleanup and
runs the bounded transport benchmark. macOS retains the inline path and explicitly
reports shared memory as unsupported.

## Linux Validation Container

With Docker running, execute from the repository root:

```sh
node scripts/validate-linux.mjs
```

Add `--onnx` to also build the optional dependency/model image and test real CPU
inference, independent reference agreement and worker failure recovery. See the
[worker setup and artifact boundaries](onnx-worker.md).

The [runner](../scripts/validate-linux.mjs) exports only the publication allowlist
to a disposable directory. It never mounts the working repository or its Git
history. The [tooling image](../containers/validation.Dockerfile) pins Rust 1.90.0
and Node.js 22.19.0 base images by digest. Initial image construction downloads
public toolchains and requires network access; it is not an offline bootstrap.

The actual build, lint, Rust/C/CLI and publication tests run on Linux amd64 with
networking disabled, as UID 10001, with dropped capabilities, no privilege
escalation and a read-only image. Only temporary directories are writable. Limits
are two CPUs, 2 GiB memory and 256 processes. The container is removed after the
run; the tooling image remains cached locally. This is a development validation
image, not an ecOS distribution or a production base image. The Docker service
itself is outside the test container's security boundary.

## Run Manually

In one terminal, as an ordinary user:

```sh
target/debug/ecosd --socket /tmp/ecos-dev/ecos.sock
```

The daemon creates the immediate parent directory with mode 0700 if absent.
An existing parent must belong to the current user and deny group/other access.
Choose a short path inside a trusted directory. Existing socket paths are refused,
never silently replaced. Do not run this prototype as a system-wide root service.

In another terminal:

```sh
target/debug/cog --socket /tmp/ecos-dev/ecos.sock demo
target/debug/cog --socket /tmp/ecos-dev/ecos.sock demo 5,10,255
target/debug/cog --socket /tmp/ecos-dev/ecos.sock stats
```

The default demo returns `[1, 2, 255, 0]` for input `[0, 1, 254, 255]` and prints
zero retained resources after cleanup. Stats describe that client's session, not
other applications. Ctrl-C or SIGTERM stops the daemon and cleans up its socket.
SIGKILL cannot run cleanup; only remove a stale socket after confirming its daemon
is no longer running. The optional `--shutdown-on-stdin` mode stops on a line or EOF.

## Source Map

| Component | Responsibility |
| --- | --- |
| [cog-core](../crates/cog-core/src/lib.rs) | Error values, shapes, job states and resource limits |
| [cog-shm](../crates/cog-shm/src/linux.rs) | Sealed Linux mappings and descriptor-aware stream I/O |
| [cog-protocol](../crates/cog-protocol/src/lib.rs) | Bounded framing and typed message encoding |
| [ecos-daemon](../crates/ecos-daemon/src/lib.rs) | Socket lifecycle, peer identity and sessions |
| [runtime](../crates/ecos-daemon/src/runtime.rs) | Handles, quotas, retained resources and worker queue |
| [backend interface](../crates/cog-backend-api/src/lib.rs) | Internal Rust trait, not a stable plugin ABI |
| [mock backend](../crates/cog-backend-mock/src/lib.rs) | Deterministic cancellable byte transformation |
| [process backend](../crates/cog-backend-process/src/linux.rs) | Linux worker supervision, deadlines and response validation |
| [ONNX worker](../workers/onnx_worker.py) | Pinned CPU model, preprocessing and syscall restrictions |
| [Rust client](../crates/cog-client/src/lib.rs) | Protocol client and bounded polling waits |
| [C implementation](../crates/cog-client/src/ffi.rs) | Context registry and C ABI boundary |
| [C header](../include/cogposix/cog.h) | Experimental callable API, layouts and ownership |
| [C example](../examples/mock-client.c) | Complete model/buffer/tensor/job lifecycle |
| [CLI](../crates/cog-cli/src/main.rs) | Mock demo, digit classification, transport benchmark and stats |

Cargo produces `ecosd`, `cog` and `libcogposix` under `target/debug`. The library
has Rust, static C and shared C artifacts (`.dylib` on macOS, `.so` on Linux).
The [smoke script](../scripts/smoke.mjs) records exact compiler/linker arguments.

For the Linux buffer-transport benchmark against a running daemon:

```sh
target/debug/cog --socket /tmp/ecos-dev/ecos.sock transport-bench
```

It reports application-frame byte counts and P50/P95 timings for 16 measured
1 MiB round trips after two warmups per mode. Input creation and output snapshots
are included; inference is not. Read the [copy accounting](shared-memory.md)
before using these numbers in a performance claim.

## Source and Licenses

The [manifest](../PUBLICATION.json) records the distributed files and their license
classifications. See [license scope](../LICENSE-SCOPE.md) for community software,
documentation and third-party terms. Contributions follow the
[contribution guide](../CONTRIBUTING.md).
