#!/usr/bin/env python3
"""Actual CogPOSIX ctypes example. See component LICENSE.md."""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etex_bridge.backends import SafeArgumentParser
from etex_bridge.cogposix import CogError, search
from etex_bridge.protocol import ProtocolError, REQUEST_SCHEMA


def main():
    parser = SafeArgumentParser(description="Call etex.rag.search.v1 through the real CogPOSIX C ABI")
    parser.add_argument("--library", type=Path, required=True, help="Built libcogposix shared library")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--request-id", default="cogposix-1")
    parser.add_argument("--candidate-count", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=120000)
    args = parser.parse_args()
    request = {"schema": REQUEST_SCHEMA, "request_id": args.request_id, "query": args.query,
               "candidate_count": args.candidate_count, "top_k": args.top_k, "rerank": args.rerank}
    try:
        result = search(args.library, args.socket, request, timeout_ms=args.timeout_ms)
    except CogError as error:
        print(str(error), file=sys.stderr)  # fixed operation and integer status only
        return 1
    except (ProtocolError, ValueError, OSError, AttributeError):
        print("COGPOSIX_EXAMPLE invalid_configuration_or_result", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
