#!/usr/bin/env python3
"""Quick evaluator for liveness and face-compare checks.

Usage examples:
  python scripts/evaluate_biometrics.py \
    --liveness-csv data/liveness_samples.csv \
    --compare-csv data/compare_samples.csv

Expected CSV formats:

1) Liveness CSV (header required):
   image_path,label
   samples/live_001.jpg,live
   samples/spoof_001.jpg,spoof

   Allowed labels:
   - positive/live: 1, true, yes, y, live, genuine, real
   - negative/spoof: 0, false, no, n, spoof, fake, attack

2) Compare CSV (header required):
   probe_path,reference_path,label
   probe/p1.jpg,ref/r1.jpg,match
   probe/p2.jpg,ref/r9.jpg,mismatch

   Allowed labels:
   - positive/match: 1, true, yes, y, match, same
   - negative/non-match: 0, false, no, n, mismatch, different, imposter
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional, Tuple

# Allow running the script directly from the `scripts/` directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from basmaapp.face_detection import run_face_compare, run_liveness_check


POSITIVE_TOKENS = {"1", "true", "yes", "y", "live", "genuine", "real", "match", "same"}
NEGATIVE_TOKENS = {"0", "false", "no", "n", "spoof", "fake", "attack", "mismatch", "different", "imposter"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate liveness and face-compare accuracy on labeled samples.")
    parser.add_argument("--liveness-csv", type=Path, help="CSV file for liveness evaluation.")
    parser.add_argument("--compare-csv", type=Path, help="CSV file for compare evaluation.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("."),
        help="Base directory used to resolve relative image paths (default: current directory).",
    )
    parser.add_argument(
        "--liveness-threshold",
        type=float,
        default=0.60,
        help="Threshold passed to run_liveness_check (default: 0.60).",
    )
    parser.add_argument(
        "--compare-threshold",
        type=float,
        default=0.35,
        help="Threshold passed to run_face_compare (default: 0.35).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max number of rows to process from each CSV.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional output path to save full metrics as JSON.",
    )
    return parser.parse_args()


def _parse_bool_label(raw: str, row_number: int, field_name: str) -> bool:
    token = str(raw or "").strip().lower()
    if token in POSITIVE_TOKENS:
        return True
    if token in NEGATIVE_TOKENS:
        return False
    raise ValueError(f"row {row_number}: invalid {field_name}='{raw}'")


def _image_path(base_dir: Path, raw_path: str, row_number: int, field_name: str) -> Path:
    candidate = Path(str(raw_path or "").strip())
    if not str(candidate):
        raise ValueError(f"row {row_number}: missing {field_name}")
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    if not candidate.exists() or not candidate.is_file():
        raise ValueError(f"row {row_number}: file not found for {field_name}: {candidate}")
    return candidate


def _to_base64(image_path: Path) -> str:
    payload = image_path.read_bytes()
    return base64.b64encode(payload).decode("ascii")


def _safe_div(num: float, den: float) -> Optional[float]:
    if den == 0:
        return None
    return num / den


def _calc_metrics(tp: int, tn: int, fp: int, fn: int) -> Dict[str, Optional[float]]:
    total = tp + tn + fp + fn
    accuracy = _safe_div(tp + tn, total)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    specificity = _safe_div(tn, tn + fp)

    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2.0 * precision * recall / (precision + recall)
    else:
        f1 = None

    far = _safe_div(fp, fp + tn)
    frr = _safe_div(fn, fn + tp)

    return {
        "total": float(total),
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": f1,
        "far": far,
        "frr": frr,
    }


def _fmt(value: Optional[float], pct: bool = False) -> str:
    if value is None:
        return "n/a"
    if pct:
        return f"{value * 100:.2f}%"
    if math.isfinite(value):
        return f"{value:.4f}"
    return "n/a"


def _load_rows(path: Path, limit: Optional[int]) -> Iterable[Tuple[int, Dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")

        for i, row in enumerate(reader, start=2):  # header row is line 1
            yield i, row
            if limit is not None and (i - 1) >= limit:
                break


def evaluate_liveness(csv_path: Path, base_dir: Path, threshold: float, limit: Optional[int]) -> Dict[str, object]:
    tp = tn = fp = fn = 0
    processed = skipped = 0
    errors: List[str] = []

    for row_number, row in _load_rows(csv_path, limit):
        try:
            image = _image_path(base_dir, row.get("image_path", ""), row_number, "image_path")
            expected_live = _parse_bool_label(row.get("label", ""), row_number, "label")
            result = run_liveness_check(_to_base64(image), threshold=threshold)
            if not result.get("ok", False):
                skipped += 1
                errors.append(f"row {row_number}: {result.get('error', 'liveness check failed')}")
                continue

            predicted_live = bool(result.get("is_live"))
            processed += 1
            if predicted_live and expected_live:
                tp += 1
            elif (not predicted_live) and (not expected_live):
                tn += 1
            elif predicted_live and (not expected_live):
                fp += 1
            else:
                fn += 1
        except Exception as exc:  # pylint: disable=broad-except
            skipped += 1
            errors.append(str(exc))

    metrics = _calc_metrics(tp, tn, fp, fn)
    return {
        "task": "liveness",
        "input": str(csv_path),
        "threshold": threshold,
        "processed": processed,
        "skipped": skipped,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "metrics": metrics,
        "errors": errors,
    }


def evaluate_compare(csv_path: Path, base_dir: Path, threshold: float, limit: Optional[int]) -> Dict[str, object]:
    tp = tn = fp = fn = 0
    processed = skipped = 0
    errors: List[str] = []

    for row_number, row in _load_rows(csv_path, limit):
        try:
            probe = _image_path(base_dir, row.get("probe_path", ""), row_number, "probe_path")
            reference = _image_path(base_dir, row.get("reference_path", ""), row_number, "reference_path")
            expected_match = _parse_bool_label(row.get("label", ""), row_number, "label")

            result = run_face_compare(
                image1_base64=_to_base64(probe),
                image2_base64=_to_base64(reference),
                threshold=threshold,
            )
            if not result.get("ok", False):
                skipped += 1
                errors.append(f"row {row_number}: {result.get('error', 'face compare failed')}")
                continue

            predicted_match = bool(result.get("is_match"))
            processed += 1
            if predicted_match and expected_match:
                tp += 1
            elif (not predicted_match) and (not expected_match):
                tn += 1
            elif predicted_match and (not expected_match):
                fp += 1
            else:
                fn += 1
        except Exception as exc:  # pylint: disable=broad-except
            skipped += 1
            errors.append(str(exc))

    metrics = _calc_metrics(tp, tn, fp, fn)
    return {
        "task": "compare",
        "input": str(csv_path),
        "threshold": threshold,
        "processed": processed,
        "skipped": skipped,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "metrics": metrics,
        "errors": errors,
    }


def _print_summary(report: Dict[str, object]) -> None:
    metrics = report["metrics"]
    assert isinstance(metrics, dict)

    print(f"\n== {report['task'].upper()} ==")
    print(f"input: {report['input']}")
    print(f"threshold: {report['threshold']}")
    print(f"processed: {report['processed']}   skipped: {report['skipped']}")
    print(f"TP={report['tp']}  TN={report['tn']}  FP={report['fp']}  FN={report['fn']}")

    print("accuracy:", _fmt(metrics.get("accuracy"), pct=True))
    print("precision:", _fmt(metrics.get("precision"), pct=True))
    print("recall:", _fmt(metrics.get("recall"), pct=True))
    print("specificity:", _fmt(metrics.get("specificity"), pct=True))
    print("f1:", _fmt(metrics.get("f1")))
    print("far (false accept rate):", _fmt(metrics.get("far"), pct=True))
    print("frr (false reject rate):", _fmt(metrics.get("frr"), pct=True))

    errors = report.get("errors", [])
    if isinstance(errors, list) and errors:
        print(f"errors ({len(errors)} shown up to 10):")
        for msg in errors[:10]:
            print("-", msg)


def main() -> int:
    args = _parse_args()

    if not args.liveness_csv and not args.compare_csv:
        print("Nothing to evaluate. Provide --liveness-csv and/or --compare-csv.")
        return 2

    base_dir = args.base_dir.resolve()
    reports: List[Dict[str, object]] = []

    if args.liveness_csv:
        reports.append(
            evaluate_liveness(
                csv_path=args.liveness_csv.resolve(),
                base_dir=base_dir,
                threshold=float(args.liveness_threshold),
                limit=args.limit,
            )
        )

    if args.compare_csv:
        reports.append(
            evaluate_compare(
                csv_path=args.compare_csv.resolve(),
                base_dir=base_dir,
                threshold=float(args.compare_threshold),
                limit=args.limit,
            )
        )

    for report in reports:
        _print_summary(report)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps({"reports": reports}, indent=2), encoding="utf-8")
        print(f"\nSaved JSON report to: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
