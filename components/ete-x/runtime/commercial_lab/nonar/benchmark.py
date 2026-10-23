"""Reproducible CPU benchmarks for non-autoregressive ONNX text models.

Run from the workspace root with ``python -m commercial_lab.nonar.benchmark``.
The supplied texts, truncation policy and task are identical for every variant.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from commercial_lab.nonar.encoder import MiniLMEncoder, MiniLMReranker, _positive_integer


def compare_outputs(reference: np.ndarray, candidate: np.ndarray,
                    *, atol: float = 3e-5, rtol: float = 3e-4) -> dict:
    """Numerical comparison, not a retrieval/relevance quality measurement."""
    a, b = np.asarray(reference, dtype=np.float64), np.asarray(candidate, dtype=np.float64)
    if a.shape != b.shape or not a.size:
        raise ValueError("non-empty outputs must have identical shapes")
    delta = np.abs(a - b)
    result = {"shape": list(a.shape), "max_abs_difference": float(delta.max()),
              "mean_abs_difference": float(delta.mean()),
              "root_mean_square_difference": float(np.sqrt(np.mean(delta * delta))),
              "allclose": bool(np.allclose(a, b, atol=atol, rtol=rtol)),
              "atol": atol, "rtol": rtol,
              "bit_identical": bool(np.asarray(reference).dtype == np.asarray(candidate).dtype
                                    and np.asarray(reference).shape == np.asarray(candidate).shape
                                    and np.asarray(reference).tobytes(order="C") ==
                                        np.asarray(candidate).tobytes(order="C"))}
    if a.ndim == 2:
        norms = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
        cosines = np.sum(a * b, axis=1) / np.maximum(norms, 1e-30)
        result.update(min_row_cosine=float(cosines.min()), mean_row_cosine=float(cosines.mean()))
    return result


def _timing_summary(samples, count):
    values = np.asarray(samples, dtype=np.float64)
    if not len(values) or np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("timings must be positive and finite")
    median = float(np.median(values))
    return {"samples_seconds": values.tolist(), "median_seconds": median,
            "min_seconds": float(values.min()), "max_seconds": float(values.max()),
            "p95_seconds": float(np.percentile(values, 95)),
            "items_per_second_at_median": count / median,
            "amortized_milliseconds_per_item": 1000 * median / count}


def _environment_metadata():
    result = {"python": platform.python_version(), "platform": platform.platform(),
              "machine": platform.machine(), "processor": platform.processor(),
              "logical_cpus_reported": os.cpu_count(),
              "thread_environment": {key: os.environ.get(key) for key in
                 ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                  "TOKENIZERS_PARALLELISM", "RAYON_NUM_THREADS")}}
    result["process_cpu_affinity"] = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    try:
        lines = Path("/proc/cpuinfo").read_text().splitlines()
        cpu_fields = {}
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                cpu_fields.setdefault(key.strip(), value.strip())
        result.update(cpuinfo_visible=bool(cpu_fields),
                      cpu_model=cpu_fields.get("model name"),
                      cpu_flags=cpu_fields.get("flags", "").split())
    except OSError as exc:
        result.update(cpuinfo_visible=False, cpuinfo_read_error=type(exc).__name__)
    for name, path in (("cgroup_cpu_max", "/sys/fs/cgroup/cpu.max"),
                       ("cgroup_memory_max", "/sys/fs/cgroup/memory.max")):
        try:
            result[name] = Path(path).read_text().strip()
        except OSError:
            result[name] = None
    return result


def benchmark_model(model, items, *, batch_sizes=(1, 8, 32), sort_modes=(False, True),
                    warmup=1, repeats=5, progress=None) -> dict:
    """Benchmark one model/session while rotating batch configurations.

    ORT-only time is the sum of timed session.run calls on prepared inputs.
    A separate complete pass includes tokenization, batching, inference,
    postprocessing and restoration of input order. No model/session construction
    is charged to warmed inference. Original per-repeat observations are kept.
    """
    items = list(items) if not isinstance(items, str) else [items]
    if not items:
        raise ValueError("benchmark requires at least one text or pair")
    repeats = _positive_integer(repeats, "repeats")
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
        raise ValueError("warmup must be a nonnegative integer")
    sizes = list(dict.fromkeys(_positive_integer(size, "batch_size") for size in batch_sizes))
    modes = list(dict.fromkeys(bool(mode) for mode in sort_modes))
    if not sizes or not modes:
        raise ValueError("at least one batch size and sorting mode are required")
    prepared_reference = model.prepare(items, batch_size=1, sort_by_length=False)
    reference = model.predict_prepared(prepared_reference)
    cases = []
    for size in sizes:
        for sorted_mode in modes:
            prepared = model.prepare(items, batch_size=size, sort_by_length=sorted_mode)
            output = model.predict_prepared(prepared)
            for _ in range(warmup):
                model.predict_prepared(prepared)
            cases.append({"batch_size": size, "sort_by_length": sorted_mode,
                          "prepared": prepared, "consistency_vs_batch1": compare_outputs(reference, output),
                          "samples": []})
    if progress:
        progress(f"{model.task}: validation and {warmup} additional warmup pass(es) complete")
    for repetition in range(repeats):
        order = cases[repetition % len(cases):] + cases[:repetition % len(cases)]
        for case in order:
            prepared = case["prepared"]
            # Alternate pass order to reduce a systematic hot-cache advantage.
            result = {}
            for phase in (("prepared", "end_to_end") if repetition % 2 == 0 else ("end_to_end", "prepared")):
                if phase == "prepared":
                    _, measurement = model._predict(prepared)
                    result.update(ort_only_seconds=measurement["ort_seconds"],
                                  ort_batch_seconds=measurement["ort_batch_seconds"],
                                  prepared_pipeline_seconds=measurement["prepared_pipeline_seconds"],
                                  prepared_postprocess_seconds=measurement["postprocess_seconds"])
                else:
                    started = time.perf_counter()
                    fresh = model.prepare(items, batch_size=case["batch_size"],
                                          sort_by_length=case["sort_by_length"])
                    _, measurement = model._predict(fresh)
                    result.update(end_to_end_seconds=time.perf_counter() - started,
                                  tokenization_seconds=fresh.tokenization_seconds,
                                  batching_seconds=fresh.batching_seconds,
                                  end_to_end_ort_seconds=measurement["ort_seconds"],
                                  end_to_end_postprocess_seconds=measurement["postprocess_seconds"])
            result["repetition"] = repetition
            case["samples"].append(result)
        if progress:
            progress(f"{model.task}: repetition {repetition + 1}/{repeats} complete")
    rows = []
    for case in cases:
        prepared = case.pop("prepared")
        summaries = {name: _timing_summary([sample[name] for sample in case["samples"]], len(items))
                     for name in ("ort_only_seconds", "prepared_pipeline_seconds", "end_to_end_seconds")}
        case.update(batch_count=len(prepared.batches), real_tokens=prepared.real_tokens,
                    padded_tokens=prepared.padded_tokens, padding_fraction=prepared.padding_fraction,
                    timing=summaries)
        rows.append(case)
    return {
        "schema": "nonar_cpu_benchmark_v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "model": model.metadata(),
        "environment": _environment_metadata(),
        "workload": {"items": len(items),
            "distinct_items": len({json.dumps(item, ensure_ascii=False) for item in items}),
            "distinct_queries": len({item[0] for item in items}) if model.task == "reranker" else None,
            "distinct_documents": len({item[1] for item in items}) if model.task == "reranker" else len(set(items)),
            "sha256_of_json_items": hashlib.sha256(
            json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
            "tokens_after_truncation_min": int(prepared_reference.lengths.min()),
            "tokens_after_truncation_median": float(np.median(prepared_reference.lengths)),
            "tokens_after_truncation_max": int(prepared_reference.lengths.max())},
        "method": {"repeats": repeats, "additional_warmup_passes_per_configuration": warmup,
                   "validation_pass_also_warms_model": True,
                   "configuration_order": "rotated once per repetition",
                   "phase_order": "prepared/e2e alternates per repetition",
                   "ort_only": "sum of session.run wall times on prepared inputs; includes output allocation",
                   "prepared_pipeline": "ORT plus pooling/logit extraction, validation, allocation and order restoration",
                   "end_to_end": "text/pair validation, tokenization, dynamic padding/sorting, ORT and postprocessing",
                   "excluded": "model loading, session construction, file hashing, dataset I/O, quality evaluation",
                   "latency_note": "whole-workload timing; amortized per-item time is not an online request p95",
                   "consistency_note": "dynamic INT8 activation scales can vary with batch composition; report numerical differences, then evaluate task quality"},
        "results": rows,
    }


def load_jsonl_items(path: str | Path, task: str, limit: int | None = None):
    items = []
    with Path(path).open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if task == "encoder":
                if isinstance(record, str):
                    item = record
                elif isinstance(record, dict) and isinstance(record.get("text"), str):
                    item = " ".join(part for part in (record.get("title", ""), record["text"]) if part)
                else:
                    raise ValueError(f"line {line_number}: expected a string or text/title record")
            else:
                if isinstance(record, list) and len(record) == 2:
                    item = tuple(record)
                elif isinstance(record, dict) and "query" in record and "document" in record:
                    item = (record["query"], record["document"])
                else:
                    raise ValueError(f"line {line_number}: expected [query,document] or query/document record")
            items.append(item)
            if limit is not None and len(items) >= limit:
                break
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--task", choices=("encoder", "reranker"), default="encoder")
    parser.add_argument("--precision-label", default="source_fp32",
                        help="descriptive label only; never changes weights")
    parser.add_argument("--texts-jsonl", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=96)
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 8, 32])
    parser.add_argument("--threads", nargs="+", type=int, default=[1, 4])
    parser.add_argument("--sort-modes", nargs="+", choices=("original", "length"), default=["original", "length"])
    parser.add_argument("--max-length", type=int)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    items = load_jsonl_items(args.texts_jsonl, args.task, args.limit)
    factory = MiniLMEncoder if args.task == "encoder" else MiniLMReranker
    reports = []
    for threads in args.threads:
        model = factory(args.model_dir, model_path=args.model_path, num_threads=threads,
                        max_length=args.max_length)
        reports.append(benchmark_model(model, items, batch_sizes=args.batch_sizes,
                                       sort_modes=[value == "length" for value in args.sort_modes],
                                       warmup=args.warmup, repeats=args.repeats,
                                       progress=lambda text: print(text, file=sys.stderr, flush=True)))
        del model
        gc.collect()
    result = {"precision_label": args.precision_label, "dataset_path": str(args.texts_jsonl.resolve()),
              "reports": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "configurations": sum(len(r["results"]) for r in reports)}, indent=2))


if __name__ == "__main__":
    main()
