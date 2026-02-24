#!/usr/bin/env python3
"""Collect Step8 warning/metrics summaries into artifact reports."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def find_summary_files(summary_root: Path) -> list[Path]:
    if not summary_root.exists() or not summary_root.is_dir():
        return []
    return sorted(path for path in summary_root.rglob("*_summary.json") if path.is_file())


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count_map_add(counter: dict[str, int], key: str, value: int = 1) -> None:
    if not key:
        return
    counter[key] = int(counter.get(key, 0)) + int(value)


def build_metrics_report(summary_root: Path) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    parse_failures: list[str] = []

    warning_types_total: dict[str, int] = {}
    track_c_modes: dict[str, int] = {}
    track_b_modes: dict[str, int] = {}
    report_code_counts: dict[str, int] = {}
    year_set_counts: dict[str, int] = {}

    runtime_values: list[int] = []
    warning_total = 0

    for summary_file in find_summary_files(summary_root):
        try:
            payload = json.loads(summary_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-except
            parse_failures.append(str(summary_file))
            continue

        metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
        if not isinstance(metrics, dict):
            metrics = {}

        warning_types = metrics.get("warning_types", {})
        if not isinstance(warning_types, dict):
            warning_types = {}

        years = [str(x) for x in payload.get("years", []) if str(x)]
        years_key = ",".join(years)
        report_code = str(payload.get("report_code", "")).strip()
        track_c_mode = str(payload.get("track_c", {}).get("mode", "")).strip()
        track_b_mode = str(payload.get("track_b_fallback", {}).get("mode", "")).strip()
        warning_count = len(payload.get("warnings", [])) if isinstance(payload, dict) else 0

        runtime_ms = _to_int(metrics.get("runtime_ms"))
        if runtime_ms is not None and runtime_ms >= 0:
            runtime_values.append(runtime_ms)

        for warning_type, count in warning_types.items():
            count_i = _to_int(count) or 0
            if count_i > 0:
                _count_map_add(warning_types_total, str(warning_type), count_i)

        _count_map_add(track_c_modes, track_c_mode)
        _count_map_add(track_b_modes, track_b_mode)
        _count_map_add(report_code_counts, report_code)
        _count_map_add(year_set_counts, years_key)
        warning_total += int(warning_count)

        runs.append(
            {
                "summary_file": str(summary_file),
                "company_name": str(payload.get("company_name", "")),
                "corp_code": str(payload.get("corp_code", "")),
                "years": years,
                "report_code": report_code,
                "track_c_mode": track_c_mode,
                "track_b_mode": track_b_mode,
                "warning_count": int(warning_count),
                "runtime_ms": runtime_ms,
                "cache_hit_rate": _to_float(
                    metrics.get("normalizer", {}).get("cache_hit_rate")
                    if isinstance(metrics.get("normalizer"), dict)
                    else None
                ),
                "warning_types": {
                    str(key): int(_to_int(value) or 0)
                    for key, value in warning_types.items()
                },
            }
        )

    runtime_stats: dict[str, Any] = {
        "count": len(runtime_values),
        "sum_ms": int(sum(runtime_values)) if runtime_values else 0,
        "min_ms": min(runtime_values) if runtime_values else None,
        "max_ms": max(runtime_values) if runtime_values else None,
        "avg_ms": round(sum(runtime_values) / len(runtime_values), 2) if runtime_values else None,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary_root": str(summary_root),
        "totals": {
            "run_count": len(runs),
            "warning_count": int(warning_total),
            "runtime": runtime_stats,
            "warning_types": dict(sorted(warning_types_total.items())),
            "track_c_modes": dict(sorted(track_c_modes.items())),
            "track_b_modes": dict(sorted(track_b_modes.items())),
            "report_codes": dict(sorted(report_code_counts.items())),
            "year_sets": dict(sorted(year_set_counts.items())),
            "parse_failures": parse_failures,
        },
        "runs": runs,
    }


def _render_count_lines(title: str, counter: dict[str, int]) -> list[str]:
    lines = [f"## {title}"]
    if not counter:
        lines.append("- (none)")
        return lines
    for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{key}`: {value}")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals", {})
    runtime = totals.get("runtime", {})
    lines: list[str] = []
    lines.append("# Step8 Warning Metrics Report")
    lines.append("")
    lines.append(f"- generated_at: `{report.get('generated_at')}`")
    lines.append(f"- summary_root: `{report.get('summary_root')}`")
    lines.append(f"- run_count: `{totals.get('run_count')}`")
    lines.append(f"- warning_count: `{totals.get('warning_count')}`")
    lines.append(
        "- runtime_ms: "
        f"count={runtime.get('count')} avg={runtime.get('avg_ms')} "
        f"min={runtime.get('min_ms')} max={runtime.get('max_ms')}"
    )
    lines.append("")
    lines.extend(_render_count_lines("Warning Types", totals.get("warning_types", {})))
    lines.append("")
    lines.extend(_render_count_lines("Track C Modes", totals.get("track_c_modes", {})))
    lines.append("")
    lines.extend(_render_count_lines("Track B Modes", totals.get("track_b_modes", {})))
    lines.append("")
    lines.extend(_render_count_lines("Report Codes", totals.get("report_codes", {})))
    lines.append("")
    lines.extend(_render_count_lines("Year Sets", totals.get("year_sets", {})))
    parse_failures = totals.get("parse_failures", [])
    lines.append("")
    lines.append("## Parse Failures")
    if parse_failures:
        for path in parse_failures:
            lines.append(f"- `{path}`")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Step8 warning metrics from summary JSON files.")
    parser.add_argument(
        "--summary-root",
        default="/tmp/step8_online_artifacts",
        help="Root directory to scan for *_summary.json files.",
    )
    parser.add_argument(
        "--output-json",
        default="/tmp/step8_online_artifacts/metrics/step8_warning_metrics.json",
        help="Output report JSON path.",
    )
    parser.add_argument(
        "--output-md",
        default="/tmp/step8_online_artifacts/metrics/step8_warning_metrics.md",
        help="Output report markdown path.",
    )
    parser.add_argument(
        "--require-runs",
        action="store_true",
        help="Fail if no parsed summary runs are found.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary_root = Path(str(args.summary_root))
    output_json = Path(str(args.output_json))
    output_md = Path(str(args.output_md))
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    report = build_metrics_report(summary_root)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_md.write_text(render_markdown(report), encoding="utf-8")

    run_count = int(report.get("totals", {}).get("run_count", 0))
    parse_failures = report.get("totals", {}).get("parse_failures", [])
    print(
        f"[metrics-collector] summary_root={summary_root} "
        f"runs={run_count} parse_failures={len(parse_failures)}"
    )
    print(f"[metrics-collector] output_json={output_json}")
    print(f"[metrics-collector] output_md={output_md}")

    if args.require_runs and run_count <= 0:
        print("[metrics-collector] no runs found")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
