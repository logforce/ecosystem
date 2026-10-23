#!/usr/bin/env python3
"""Run offline tests and save bounded evidence without third-party dependencies.

See component LICENSE.md.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evidence")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    transcript = io.StringIO()
    outcome = unittest.TextTestRunner(stream=transcript, verbosity=2).run(suite)
    text = transcript.getvalue()
    print(text, end="")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "offline_tests.txt").write_text(text, encoding="utf-8")
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for folder in ("etex_bridge", "tests", "schemas", "examples")
              for path in sorted((ROOT / folder).glob("*.py"))}
    for name in ("worker.py", "client.py", "prepare_runtime.py"):
        hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    result = {"kind": "offline_worker_correctness", "engine": "fixture",
              "fixture_is_ai": False, "real_ai_tested": False, "performance_benchmark": False,
              "python": platform.python_version(), "tests_run": outcome.testsRun,
              "failures": len(outcome.failures), "errors": len(outcome.errors),
              "skipped": len(outcome.skipped), "passed": outcome.wasSuccessful(), "source_sha256": hashes}
    (args.output_dir / "offline_tests.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if outcome.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
