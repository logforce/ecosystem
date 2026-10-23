#!/usr/bin/env python3
"""One-shot direct worker client. See component LICENSE.md."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from etex_bridge.backends import SafeArgumentParser, add_engine_arguments, validate_engine_arguments
from etex_bridge.protocol import READY, REQUEST_SCHEMA, ProtocolError, decode_response, encode_request


def _worker_arguments(args):
    command = [sys.executable, "-I", "-B", "-u", str(Path(__file__).with_name("worker.py")),
               "--engine", args.engine, "--threads", str(args.threads), "--strategy", args.strategy,
               "--model-file", args.model_file, "--reranker-model-file", args.reranker_model_file]
    for name in ("snapshot", "ete_x_root", "encoder_dir", "reranker_dir"):
        value = getattr(args, name)
        if value is not None:
            command += ["--" + name.replace("_", "-"), str(Path(value).resolve())]
    if args.no_reranker:
        command.append("--no-reranker")
    return command


def main(argv=None):
    parser = SafeArgumentParser(description="Direct fixed-frame worker demonstration, without ecOS IPC")
    add_engine_arguments(parser)
    parser.add_argument("--query", required=True)
    parser.add_argument("--request-id", default="cli-1")
    parser.add_argument("--candidate-count", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=120.0,
                        help="Whole subprocess demonstration deadline, including startup")
    args = parser.parse_args(argv)
    validate_engine_arguments(args, parser)
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 3600:
        parser.error("Invalid timeout")
    request = {"schema": REQUEST_SCHEMA, "request_id": args.request_id, "query": args.query,
               "candidate_count": args.candidate_count, "top_k": args.top_k, "rerank": args.rerank}
    try:
        frame = encode_request(1, request)
    except ProtocolError:
        parser.error("Invalid request")
    environment = dict(os.environ)
    environment.update(OMP_NUM_THREADS=str(args.threads), OPENBLAS_NUM_THREADS=str(args.threads),
                       TOKENIZERS_PARALLELISM="false")
    child = subprocess.Popen(_worker_arguments(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=environment, start_new_session=(os.name == "posix"))
    try:
        output, _diagnostics = child.communicate(frame, timeout=args.timeout_seconds)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if os.name == "posix":
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            child.kill()
        child.communicate()
        print("ETEX_CLIENT cancelled_or_timed_out; no result was accepted", file=sys.stderr)
        return 1
    try:
        if child.returncode != 0 or not output.startswith(READY):
            raise ProtocolError()
        result = decode_response(output[len(READY):], 1)
    except ProtocolError:
        print("ETEX_CLIENT worker_or_transport_failed; no result was accepted", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0 if result["cog_status"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
