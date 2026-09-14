# R3: Supervised Local ONNX Inference

Status: experimental subset verified on 14 September 2026. This is a real CPU
inference path, not a complete OS, general OCR application or production sandbox.
The default mock remains available without Python, downloads or inference packages.

## Capability Contract

The configured worker exposes only `vision.mnist.v1`. Clients cannot select paths,
download models or execute scripts. One daemon serves one configured backend;
the ONNX configuration replaces the mock catalogue for that daemon.

| Property | Contract |
| --- | --- |
| Input | One contiguous U8 tensor, shape `[28,28]`, 784 grayscale pixels |
| Convention | Black background, white foreground; caller prepares the image |
| Preprocessing | Convert to float32, divide by 255, reshape to `[1,1,28,28]` |
| Engine | ONNX Runtime 1.22.1, CPUExecutionProvider, sequential execution, one thread |
| Output | One U8 tensor, shape `[1]`, class index 0 through 9 |
| Postprocessing | Argmax of finite `[1,10]` scores; lowest index on ties |
| Delay | Must be zero; artificial delay is a mock-only facility |

Output is a prediction, not confidence or a guarantee. Blank images and non-digits
still receive a class. No image decoder, segmentation, handwriting application,
confidence threshold or unknown-class rejection is provided. Shapes are validated
before queueing; input and output must use distinct buffers. Public wire 1.1 and
C ABI 0.2 are unchanged. Inline and sealed shared inputs both work, but the daemon
copies input bytes into the worker pipe: this is not end-to-end zero-copy.

## Reproduce

With Docker running, from the repository root:

```sh
node scripts/validate-linux.mjs --onnx
```

The runner exports only the public allowlist. Initial image construction downloads
Python packages and the pinned model; subsequent tests run without networking as
UID 10001. The default runner without `--onnx` needs neither model nor Python
packages. Both paths retain their tooling images locally and remove test containers.

For native Linux development, install Python with venv support and libseccomp2,
create an operator-controlled venv, install `workers/requirements.txt`, and run
`node scripts/fetch-mnist.mjs /tmp/ecos-models` with a nonexistent destination.
Use absolute paths to the interpreter, checked-in worker and downloaded model:

```sh
target/debug/ecosd --socket /tmp/ecos-digit/ecos.sock \
  --worker-python /opt/ecos-onnx/bin/python \
  --worker-script /opt/ecos-source/workers/onnx_worker.py \
  --mnist-model /tmp/ecos-models/mnist-8.onnx
target/debug/cog --socket /tmp/ecos-digit/ecos.sock digit /tmp/digit.u8
```

These illustrative `/opt` paths must match your installation. Run as an ordinary
user, not root. The input file must contain exactly 784 raw bytes, not a PNG/JPEG.
The CLI prints JSON containing model identity and the predicted digit. The worker
loads lazily on first inference; daemon readiness and model-open success do not
certify that the worker has loaded successfully. Worker failures return bounded
job errors, not Python traceback contents. The validation script runs a direct
worker probe with captured diagnostics when troubleshooting initialization.

## Supervision and Restrictions

The trusted Rust backend starts an isolated-mode Python interpreter with cleared
environment, piped stdin/stdout and no client-controlled command arguments. Worker
readiness is `COGW1`. Requests are 792 bytes: a monotonically increasing little-
endian u64 ID followed by 784 pixels. Replies are 13 bytes: `DIG1`, the matching
ID and one validated class byte. This is a private worker protocol, not CogPOSIX.

A ten-second deadline covers worker startup and inference, excluding daemon queue
time. Nonblocking pipe operations prevent a stopped reader from hanging the
supervisor. Cancellation, timeout, EOF, malformed replies and worker death discard,
kill and reap the worker before backend access finishes. Affected jobs are never
automatically retried. The next job starts a fresh worker. Client wait timeouts
remain separate from this execution deadline; clients must still cancel explicitly
when they no longer want an accepted job.

The supplied Python worker requires non-root Linux, sets parent-death SIGKILL,
disables core dumps and file growth, and limits address space to 2 GiB and open
descriptors to 64. It verifies exact model size and SHA-256 before parsing. Trusted
Python/native imports happen before syscall restrictions; ONNX model parsing
happens afterward. No-new-privileges and a libseccomp denylist reject file opening,
ordinary network sockets, process creation/execution and selected mutation/process
inspection interfaces. File-open, socket and fork probes must return EPERM before
readiness. Restriction installation failure prevents inference.

This is defense in depth, not a complete hostile-code sandbox. The denylist is not
a reviewed syscall allowlist and does not cover every current/future kernel attack
surface. Interpreter, script, dependencies and their writable directories remain
trusted operator configuration. Same-user hostile applications are still outside
the per-user daemon's isolation claim. Container restrictions in the test runner
are additional controls, not automatically installed for native deployments.
Broader kernel/architecture tests, dependency audits, stronger worker isolation
and independent security review remain required before production service.

## Artifact and Distribution Boundary

The downloader accepts exactly 26,454 bytes with SHA-256:

```text
2f06e72de813a8635c9bc0397ac447a601bdbfa7df4bebc278723b958831c9bf
```

It uses `validated/vision/classification/mnist/model/mnist-8.onnx` at upstream
commit `4f43949841cb55a0b98dc8fcd045431ccafd9f96`. The worker independently verifies
the same bytes. The [upstream model description](https://github.com/onnx/models/tree/4f43949841cb55a0b98dc8fcd045431ccafd9f96/validated/vision/classification/mnist)
defines its input/output conventions and labels the model MIT, while the
[repository license](https://github.com/onnx/models/blob/4f43949841cb55a0b98dc8fcd045431ccafd9f96/LICENSE)
is Apache-2.0. Record this difference for asset-specific rights review; do not
assume the project's own software license relicenses third-party weights.

Weights, installed wheels and validation images are not public-source allowlist
entries. Optional images contain third-party assets downloaded for local testing;
do not publish those images as ecOS releases without a dependency/model license
inventory, required notices and redistribution review. Python versions are pinned,
but wheel hashes and apt package versions are not locked. This is a development
image, not a reproducible or approved distributable model pack. The CPU provider
configuration uses the [official ONNX Runtime API](https://onnxruntime.ai/docs/api/python/api_summary.html).

## Evidence and Remaining Work

Validation environment: Linux x86-64, kernel 5.15.49-linuxkit, Rust 1.90.0,
Node.js 22.19.0, Debian GCC 12.2.0, Python 3.11 and ONNX Runtime 1.22.1.

| Check | Result |
| --- | --- |
| Rust workspace build, formatting and strict Clippy | Passed |
| Linux Rust tests | 30 passed; separate ignored crash helper invoked by its parent test |
| Existing C ABI, lifecycle, sealed memory and ancillary sanitizer tests | Passed |
| ONNX Runtime versus ONNX ReferenceEvaluator | Matching class indices on eight synthetic inputs |
| Real C inference with sealed input and early handle release | Passed |
| Real worker SIGKILL followed by failed request and fresh-worker recovery | Passed |
| Supervisor startup/inference timeout, cancellation, malformed response/ID | Passed with controlled worker fixtures |
| Wrong model hash and worker restriction probes | Passed |
| Public export boundary tests | 14 passed |

The eight fixtures are black, white and six seeded random images. They test engine
agreement and integration, not handwritten-digit accuracy. No latency, energy,
commercial savings or general model quality claim follows from this evidence.
Real-worker kill testing occurs between requests; deterministic fixtures cover
in-flight hangs/cancellation. Wider crash races and parent-death behavior need
additional adversarial validation. Full R3 sign-off still needs independent review.

Next engineering gates are scheduling/accounting (R4), reviewed model packages
(R5), and a useful application with representative data and a measured direct-engine
baseline. OS installation remains a later, separate validation gate.
