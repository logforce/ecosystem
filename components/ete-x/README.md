# ETE-X — Early Termination Encoding

A CPU-only retrieval engine built to help make Sovereign AI practical.

ETE-X turns a query into an embedding, searches an approved local corpus and can
rerank the returned candidates. It returns document references, separate dense
and reranker scores, source identities and execution metadata. It retrieves
evidence; it does not generate a chatbot answer.

This optional component integrates with **ecOS >_ CogPOSIX** through a persistent
worker and the existing C ABI. Its pure CogSheet™ mapper targets Capability
Exchange v1. Connecting that mapper to the live CogSheet application is separate
work; no workbook formula or live browser integration is claimed by this release.

## What is included

- A bounded Python worker, direct and C ABI clients, JSON schemas and tests.
- The original local retrieval implementation and numerical-contract tests.
- Two compact, attributed third-party ONNX models and their tokenizers.
- An immutable **16-document synthetic demonstration corpus**, not a relevance
  benchmark or a customer dataset.
- A pure semantic request/result mapper with trusted corpus-grant checks.
- A separately licensed, opt-in Rust process backend. The C header is unchanged.

## Fast checks — no AI, no external services

From the repository root, with Node.js 22+ and Python 3:

```bash
python3 -I -B components/ete-x/integration/tests/run_tests.py --output-dir /tmp/etex-worker-checks
node components/ete-x/cogsheet/adapter.test.mjs
cargo test --workspace --offline --locked
```

The Python fixture suite tests framing and validation without inference. The
mapper suite includes recorded real-result compatibility fixtures; replaying
those records does not run a model. Neither is a performance measurement.

## Real local retrieval

The qualified platform is Linux x86-64 with CPython 3.12 and a C++17 compiler.
Create an isolated environment and install the pinned runtime. Installation may
use the package index deliberately, or use a reviewed wheelhouse offline:

```bash
python3.12 -m venv /tmp/etex-python
/tmp/etex-python/bin/pip install --require-hashes -r components/ete-x/requirements-linux-x86_64-cp312.lock
python3 -I -B components/ete-x/integration/prepare_runtime.py --destination /tmp/etex-runtime
/tmp/etex-python/bin/python -I -B components/ete-x/integration/client.py --engine etex --ete-x-root /tmp/etex-runtime --snapshot /tmp/etex-runtime/commercial_lab/demo_snapshot --strategy bounded --threads 1 --query "How can local document retrieval protect data?" --candidate-count 8 --top-k 3 --rerank
```

Choose fresh temporary paths: the environment and runtime-copy helpers must not
overwrite existing work. Native kernel compilation writes only into the runtime
copy. For offline installation add `--no-index --find-links /path/to/reviewed-wheels`
to pip. Once provisioned, inference needs no API key, hosted model or network.
The numerical contract requires the documented x86-64 floating-point environment;
hardware-specific acceleration and timings do not transfer automatically.

To enable the separately licensed native backend explicitly:

```bash
cargo build --workspace --offline --locked --features etex
```

See [the integration guide](../../docs/ete-x.md) for daemon flags, C ABI execution,
corpus grants, cancellation, limits and qualification evidence.

## Reproduce the full qualification

On the qualified Linux platform, install the additional hash-locked test tools in
the same isolated environment. These are development dependencies, not additional
models or hosted services:

```bash
/tmp/etex-python/bin/pip install --require-hashes -r components/ete-x/requirements-tests-linux-x86_64-cp312.lock
cd /tmp/etex-runtime/commercial_lab
/tmp/etex-python/bin/python -B -m pytest tests -q -p no:cacheprovider
```

For a fully offline setup, download the reviewed wheels first and use the same
`--no-index --find-links` options for both lock files. Return to the repository
root for native acceptance tests. The harness expects release binaries beneath
the build cache's `cargo_target/release` directory:

```bash
CARGO_TARGET_DIR=/tmp/etex-native/cargo_target cargo build --workspace --release --offline --locked --features etex
/tmp/etex-python/bin/python -I -B components/ete-x/integration/tests/real_smoke.py --ete-x-root /tmp/etex-runtime --snapshot /tmp/etex-runtime/commercial_lab/demo_snapshot --output /tmp/etex-real-worker.json
/tmp/etex-python/bin/python -I -B components/ete-x/integration/tests/native_e2e.py --kit "$PWD/components/ete-x" --build-cache /tmp/etex-native --python /tmp/etex-python/bin/python --etex-root /tmp/etex-runtime --evidence-dir /tmp/etex-native-evidence
```

Use fresh output directories. The retained [23 October qualification](validation/local-2026-10-23/qualification.json)
passed with networking disabled, including real encoding, retrieval, reranking
and native C ABI calls. Two FP32 control-model tests were skipped because those
larger control weights are not distributed; the included compact models were
exercised by the real-worker and native tests. These checks qualify functionality
on the tested platform, not a new speed or relevance benchmark.

## Licensing and boundaries

Original ETE-X code, worker, mapper and tests: **CC BY-NC-ND 4.0**, not Apache-2.0.
Read [the component license](LICENSE.md) before using or redistributing them.
Third-party model files retain their own Apache-2.0 terms and attribution;
dependencies retain their own licenses. See [notices](NOTICE.md). The default
community runtime remains independently usable without ETE-X.

Exact top-k is defined against the documented reference scorer over the stored
vectors. It is not a guarantee of semantic relevance, lossless model quantization,
universal bitwise agreement with other engines or production security. The worker
supervisor is lifecycle control, not a hostile-code sandbox. Permissions and
network isolation must be enforced by the host; mapper checks cannot create them.
