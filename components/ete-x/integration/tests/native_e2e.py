#!/usr/bin/env python3
"""Exercise the actual daemon/C ABI/Python-worker chain. No latency benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import struct
import subprocess
import tempfile


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True, type=Path)
    parser.add_argument("--build-cache", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--etex-root", required=True, type=Path)
    parser.add_argument("--evidence-dir", type=Path,
                        help="Write new run records here; default replaces kit/validation/ecos records")
    args = parser.parse_args()
    kit, cache, runtime = args.kit.resolve(), args.build_cache.resolve(), args.etex_root.resolve()
    python = args.python.absolute()  # Preserve the venv interpreter symlink.
    if cache == kit or kit in cache.parents or runtime == kit or kit in runtime.parents:
        parser.error("Build cache and writable runtime source must be outside the kit")
    daemon = cache / "cargo_target/release/ecosd"
    library = cache / "cargo_target/release/libcogposix.so"
    worker = kit / "integration/worker.py"
    client = kit / "integration/examples/cogposix_client.py"
    evidence = (args.evidence_dir or kit / "validation/ecos").resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "etex_ecos_c_abi_e2e_v1", "status": "running",
        "scope": "Functional integration, not a latency, semantic-quality or security benchmark.",
        "python": str(python), "daemon": str(daemon), "library": str(library),
        "daemon_sha256": sha256(daemon), "library_sha256": sha256(library),
        "worker_sha256": sha256(worker), "client_sha256": sha256(client),
        "engines": [], "operator_rejections": [],
    }

    def save():
        (evidence / "c_abi_e2e.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    try:
        with tempfile.TemporaryDirectory(prefix="ec-", dir=cache) as directory:
            temporary = Path(directory)
            temporary.chmod(0o700)
            socket = temporary / "ecos.sock"
            base = [str(daemon), "--socket", str(socket), "--shutdown-on-stdin",
                    "--etex-python", str(python), "--etex-worker", str(worker)]
            invalid = [
                ("explicit_engine_required", [], "--etex-engine is required"),
                ("mutually_exclusive_backends", ["--etex-engine", "fixture", "--worker-python", str(python)], "mutually exclusive"),
                ("zero_threads", ["--etex-engine", "fixture", "--etex-threads", "0"], "threads must be between"),
                ("zero_timeout", ["--etex-engine", "fixture", "--etex-timeout-ms", "0"], "timeout must be positive"),
                ("model_filename_path", ["--etex-engine", "fixture", "--etex-model-file", "../other.onnx"], "must be basenames"),
                ("fixture_model_paths", ["--etex-engine", "fixture", "--etex-encoder-dir", str(runtime)], "Fixture mode must not specify"),
                ("fixture_source_path", ["--etex-engine", "fixture", "--etex-root", str(runtime)], "Fixture mode must not specify"),
                ("real_paths_required", ["--etex-engine", "etex"], "requires snapshot and encoder"),
            ]
            for name, flags, expected in invalid:
                result = subprocess.run(base + flags, capture_output=True, text=True, timeout=10)
                assert result.returncode != 0 and expected in result.stderr and not socket.exists(), name
                report["operator_rejections"].append({"name": name, "exit_code": result.returncode,
                                                       "stderr": result.stderr.strip(), "passed": True})
            save()
            for engine in ("fixture", "etex"):
                command = base + ["--etex-engine", engine, "--etex-timeout-ms", "120000", "--etex-threads", "1"]
                if engine == "etex":
                    lab = runtime / "commercial_lab"
                    command += ["--etex-root", str(runtime), "--etex-snapshot", str(lab / "demo_snapshot"),
                                "--etex-encoder-dir", str(lab / "assets/models/minilm_encoder"),
                                "--etex-reranker-dir", str(lab / "assets/models/minilm_reranker"),
                                "--etex-strategy", "bounded"]
                record = {"engine": engine, "ai_executed": engine == "etex", "command": command, "calls": []}
                report["engines"].append(record)
                stderr_path = temporary / f"{engine}.stderr"
                with stderr_path.open("w+") as stderr:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                               stderr=stderr, text=True)
                    try:
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout, selectors.EVENT_READ)
                            assert selector.select(10), "Daemon did not announce readiness"
                            ready = process.stdout.readline().strip()
                        assert ready.startswith("READY "), ready
                        record["daemon_ready"] = ready
                        record["socket_mode"] = oct(socket.stat().st_mode & 0o777)
                        assert record["socket_mode"] == "0o600"
                        choices = [False, True, False] if engine == "fixture" else [True, True]
                        successes = []
                        for index, rerank in enumerate(choices):
                            request_id = f"{engine}-c-abi-{index+1}"
                            invocation = [str(python), "-I", "-B", str(client), "--library", str(library),
                                          "--socket", str(socket), "--query", "How can local document retrieval protect data?",
                                          "--request-id", request_id, "--candidate-count", "8", "--top-k", "3"]
                            if rerank:
                                invocation.append("--rerank")
                            result = subprocess.run(invocation, capture_output=True, text=True, timeout=150)
                            call = {"request_id": request_id, "rerank": rerank, "exit_code": result.returncode,
                                    "stderr": result.stderr.strip()}
                            if engine == "fixture" and rerank:
                                assert result.returncode == 1 and "status 10" in result.stderr and not result.stdout
                                call["expected_execution_error"] = 10
                                call["failure_payload_committed"] = False
                            else:
                                assert result.returncode == 0, result.stderr
                                output = json.loads(result.stdout)
                                assert output["engine"] == engine and output["status"] == "completed" and output["cog_status"] == 0
                                assert output["request_id"] == request_id and len(output["results"]) == 3
                                assert output["execution"]["rerank_performed"] is (engine == "etex")
                                call["result"] = output
                                successes.append(output["results"])
                            call["passed"] = True
                            record["calls"].append(call)
                            save()
                        assert successes[0] == successes[1], "Successive calls must preserve this deterministic fixture/demo result"
                        record["successive_result_records_equal"] = True
                    finally:
                        try:
                            process.communicate("\n", timeout=10)
                        except (subprocess.TimeoutExpired, BrokenPipeError):
                            process.kill()
                            process.communicate(timeout=10)
                        record["daemon_exit_code"] = process.returncode
                        stderr.seek(0)
                        record["daemon_stderr"] = stderr.read()
                    assert process.returncode == 0
                    assert not socket.exists(), "Daemon shutdown must remove its socket"
                    record["socket_removed_after_shutdown"] = True
                    record["passed"] = True
                    save()
                    print(f"C ABI {engine} integration passed ({len(record['calls'])} calls).", flush=True)
            # Same configured engine and input through the direct worker pipe.
            # Send one request then EOF; communicate drains output concurrently
            # so the fixed 65 KiB reply cannot deadlock on a full stdout pipe.
            request = {"schema": "etex.rag.search.request.v1", "request_id": "etex-direct-control-1",
                       "query": "How can local document retrieval protect data?",
                       "candidate_count": 8, "top_k": 3, "rerank": True}
            body = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            envelope = struct.pack("<I", len(body)) + body + bytes(4092-len(body))
            lab = runtime / "commercial_lab"
            direct_command = [str(python), "-I", "-u", str(worker), "--engine", "etex", "--threads", "1",
                              "--strategy", "bounded", "--model-file", "model.fused.int8.full.onnx",
                              "--reranker-model-file", "model.fused.int8.full.onnx", "--ete-x-root", str(runtime),
                              "--snapshot", str(lab / "demo_snapshot"),
                              "--encoder-dir", str(lab / "assets/models/minilm_encoder"),
                              "--reranker-dir", str(lab / "assets/models/minilm_reranker")]
            direct_env = {"PATH": "/usr/bin:/bin", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                          "TOKENIZERS_PARALLELISM": "false", "PYTHONDONTWRITEBYTECODE": "1"}
            process = subprocess.Popen(direct_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=direct_env, cwd="/", start_new_session=True)
            try:
                stdout, stderr = process.communicate(struct.pack("<Q", 1) + envelope, timeout=150)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)
                raise
            assert process.returncode == 0 and not stderr, "Direct control worker failed"
            assert len(stdout) == 5 + 16 + 65536 and stdout[:5] == b"ETEW1" and stdout[5:9] == b"ETX1"
            seq, status = struct.unpack("<QI", stdout[9:21])
            assert (seq, status) == (1, 0)
            length = struct.unpack("<I", stdout[21:25])[0]
            assert 0 < length <= 65532 and not any(stdout[25+length:])
            direct = json.loads(stdout[25:25+length])
            assert direct["request_id"] == request["request_id"]
            reference = report["engines"][1]["calls"][0]["result"]
            excluded = {"request_id", "timings_ms"}
            direct_comparable = {key: value for key, value in direct.items() if key not in excluded}
            abi_comparable = {key: value for key, value in reference.items() if key not in excluded}
            assert direct_comparable == abi_comparable, "Direct worker and C ABI result records differ"
            report["direct_worker_comparison"] = {
                "passed": True, "command": direct_command, "request": request,
                "exit_code": process.returncode, "stderr": stderr.decode("utf-8"), "result": direct,
                "reference_request_id": reference["request_id"], "excluded_top_level_keys": sorted(excluded),
                "all_other_top_level_fields_equal": True,
                "results_equal": direct["results"] == reference["results"],
                "provenance_equal": direct["provenance"] == reference["provenance"],
                "execution_equal": direct["execution"] == reference["execution"],
                "comparison": "Exact decoded JSON equality; same models, snapshot, query, candidate/top-k settings, rerank, strategy, thread settings and explicit environment.",
            }
            save()
            print("Direct worker and C ABI result/provenance/execution equality passed.", flush=True)
        report["status"] = "pass"
    except BaseException as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
        report["failure"] = str(error)
        raise
    finally:
        save()
    print("Operator rejections, explicit fixture and real ETE-X C ABI integration passed.", flush=True)


if __name__ == "__main__":
    main()
