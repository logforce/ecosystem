#!/usr/bin/env python3
"""Optional real AI smoke, kept separate from zero-dependency fixture tests.

Run with the Python environment containing ETE-X's pinned dependencies. A writable
runtime copy of commercial_lab is required; the canonical release stays unchanged.
This checks correctness and binding, not latency, quality, or speedup.
See component LICENSE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etex_bridge.protocol import READY, REQUEST_SCHEMA, RESPONSE_FRAME_BYTES, decode_response, encode_request


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ete-x-root", type=Path, required=True, help="Writable runtime copy; contains commercial_lab")
    parser.add_argument("--snapshot", type=Path, help="Defaults to runtime commercial_lab/demo_snapshot")
    parser.add_argument("--encoder-dir", type=Path)
    parser.add_argument("--reranker-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/real_smoke.json")
    args = parser.parse_args()
    package = args.ete_x_root.resolve() / "commercial_lab"
    snapshot = args.snapshot.resolve() if args.snapshot else package / "demo_snapshot"
    command = [sys.executable, "-I", "-B", "-u", str(ROOT / "worker.py"), "--engine", "etex",
               "--ete-x-root", str(args.ete_x_root.resolve()), "--snapshot", str(snapshot),
               "--threads", "1", "--strategy", "bounded"]
    for name in ("encoder_dir", "reranker_dir"):
        value = getattr(args, name)
        if value is not None:
            command += ["--" + name.replace("_", "-"), str(value.resolve())]
    query = "Which retrieval method preserves exact scores over the stored vectors?"
    dense = {"schema": REQUEST_SCHEMA, "request_id": "real-smoke-dense", "query": query,
             "candidate_count": 8, "top_k": 8, "rerank": False}
    reranked = {**dense, "request_id": "real-smoke-rerank", "top_k": 3, "rerank": True}
    environment = dict(os.environ)
    environment.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", TOKENIZERS_PARALLELISM="false")
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=environment, start_new_session=(os.name == "posix"), cwd="/")
    report = {"kind": "real_etex_worker_smoke", "performance_benchmark": False,
              "fixture_is_ai": False, "passed": False, "requests": 2, "persistent_process": True,
              "strategy_requested": "bounded", "python": sys.version.split()[0]}
    try:
        try:
            output, diagnostics = child.communicate(encode_request(1, dense) + encode_request(2, reranked), timeout=180)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                child.kill()
            child.communicate()
            raise RuntimeError("Smoke worker exceeded the diagnostic timeout") from None
        if child.returncode or len(output) != len(READY) + 2 * RESPONSE_FRAME_BYTES or not output.startswith(READY):
            raise RuntimeError("Real worker did not return the expected ready marker and two complete frames")
        a = decode_response(output[len(READY):len(READY) + RESPONSE_FRAME_BYTES], 1)
        b = decode_response(output[len(READY) + RESPONSE_FRAME_BYTES:], 2)
        for value in (a, b):
            if value["engine"] != "etex" or value["cog_status"] != 0 or value["provenance"]["fixture"]:
                raise RuntimeError("Real smoke unexpectedly failed or selected a fixture")
            if not value["execution"]["dense_exactness"]["certified"] or not value["execution"]["dense_exactness"]["source_verified"]:
                raise RuntimeError("Missing dense certificate/source binding")
            if value["execution"]["strategy"] != "bounded":
                raise RuntimeError("Bounded smoke path was not exercised")
            if value["provenance"]["snapshot_sha256"] != sha(snapshot / "snapshot.json"):
                raise RuntimeError("Snapshot identity mismatch")
            if value["provenance"]["index_manifest_sha256"] != sha(snapshot / "index/manifest.json"):
                raise RuntimeError("Index identity mismatch")
        if a["execution"]["rerank_performed"] or not b["execution"]["rerank_performed"]:
            raise RuntimeError("Rerank selection was not respected")
        dense_scores = {row["row_id"]: struct.pack("<d", row["dense_score"]) for row in a["results"]}
        for row in b["results"]:
            if dense_scores.get(row["row_id"]) != struct.pack("<d", row["dense_score"]):
                raise RuntimeError("Reranking changed dense scores or escaped the candidate set")
        if query.encode() in output + diagnostics or len(diagnostics) > 1024:
            raise RuntimeError("Unexpected query echo or unbounded diagnostic output")
        report.update(passed=True, engine="etex", dense_count=len(a["results"]), reranked_count=len(b["results"]),
                      reranked_dense_scores_bitwise_equal=True, query_not_echoed=True,
                      diagnostics_bytes=len(diagnostics), provenance=a["provenance"],
                      index_layout=a["execution"]["index_layout"],
                      dense_row_ids=[r["row_id"] for r in a["results"]],
                      reranked_row_ids=[r["row_id"] for r in b["results"]],
                      dense_certificate=a["execution"]["dense_exactness"],
                      worker_source_sha256=sha(ROOT / "worker.py"))
    except Exception as error:
        # Every exception raised above has a fixed message. Do not serialize
        # arbitrary lower-layer exception text or the user query.
        report["failure"] = "Real worker smoke did not satisfy its checks; no performance claim."
        report["error_type"] = type(error).__name__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
