#!/usr/bin/env python3
"""Build Step8 warning metrics trend reports from recent runs."""

from __future__ import annotations

import argparse
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


DEFAULT_QUALITY_GATE_CONFIG_PATH = Path("config/step8_warning_quality_gate.yaml")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int_strict(value: Any, *, field: str, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be integer: {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}: {parsed}")
    return parsed


def _to_float_strict(value: Any, *, field: str, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be float: {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}: {parsed}")
    return parsed


def _to_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError(f"{field} must be bool: {value!r}")


def _normalize_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, count in value.items():
        key_s = str(key).strip()
        if not key_s:
            continue
        count_i = _to_int(count, 0)
        if count_i <= 0:
            continue
        normalized[key_s] = count_i
    return normalized


def _normalize_float_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, raw in value.items():
        key_s = str(key).strip()
        if not key_s:
            continue
        value_f = _to_float(raw)
        if value_f is None or value_f < 0:
            continue
        normalized[key_s] = round(float(value_f), 2)
    return normalized


def _normalize_string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    items: list[str] = []
    if isinstance(value, str):
        items = [token.strip() for token in value.split(",") if token.strip()]
    elif isinstance(value, list):
        items = [str(token).strip() for token in value if str(token).strip()]
    else:
        raise ValueError(f"expected list or comma-separated string, got: {value!r}")
    return {item for item in items if item}


def _parse_iso_datetime(value: str | None) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _count_map_delta(latest: dict[str, int], previous: dict[str, int]) -> dict[str, int]:
    keys = set(latest) | set(previous)
    out: dict[str, int] = {}
    for key in keys:
        delta = int(latest.get(key, 0)) - int(previous.get(key, 0))
        if delta != 0:
            out[key] = delta
    return dict(sorted(out.items()))


def _float_map_delta(latest: dict[str, float], previous: dict[str, float]) -> dict[str, float]:
    keys = set(latest) | set(previous)
    out: dict[str, float] = {}
    for key in keys:
        latest_v = _to_float(latest.get(key)) or 0.0
        previous_v = _to_float(previous.get(key)) or 0.0
        delta = round(float(latest_v) - float(previous_v), 2)
        if delta != 0:
            out[key] = delta
    return dict(sorted(out.items()))


def _sum_count_maps(items: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        for key, count in item.items():
            out[key] = int(out.get(key, 0)) + int(count)
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _point_profile_signature(point: dict[str, Any]) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    report_codes = _normalize_count_map(point.get("report_codes", {}))
    year_sets = _normalize_count_map(point.get("year_sets", {}))
    return (
        tuple(sorted(report_codes.items())),
        tuple(sorted(year_sets.items())),
    )


def _collect_run_level_metrics(payload: dict[str, Any]) -> tuple[dict[str, int], dict[str, float]]:
    runs = payload.get("runs", []) if isinstance(payload, dict) else []
    if not isinstance(runs, list):
        return {}, {}

    warning_count_by_code: dict[str, int] = {}
    runtime_sum_by_code: dict[str, float] = {}
    runtime_count_by_code: dict[str, int] = {}

    for run in runs:
        if not isinstance(run, dict):
            continue
        report_code = str(run.get("report_code", "")).strip()
        if not report_code:
            continue

        warning_count = _to_int(run.get("warning_count"), 0)
        if warning_count > 0:
            warning_count_by_code[report_code] = int(warning_count_by_code.get(report_code, 0)) + warning_count

        runtime_ms = _to_float(run.get("runtime_ms"))
        if runtime_ms is None or runtime_ms < 0:
            continue
        runtime_sum_by_code[report_code] = float(runtime_sum_by_code.get(report_code, 0.0)) + float(runtime_ms)
        runtime_count_by_code[report_code] = int(runtime_count_by_code.get(report_code, 0)) + 1

    runtime_avg_by_code: dict[str, float] = {}
    for report_code, runtime_sum in runtime_sum_by_code.items():
        count = int(runtime_count_by_code.get(report_code, 0))
        if count <= 0:
            continue
        runtime_avg_by_code[report_code] = round(float(runtime_sum) / count, 2)

    return (
        dict(sorted(warning_count_by_code.items())),
        dict(sorted(runtime_avg_by_code.items())),
    )


def _build_point(
    payload: dict[str, Any],
    *,
    source: str,
    run_id: int | None = None,
    artifact_id: int | None = None,
    fallback_generated_at: str | None = None,
) -> dict[str, Any]:
    totals = payload.get("totals", {}) if isinstance(payload, dict) else {}
    if not isinstance(totals, dict):
        totals = {}
    runtime = totals.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}

    warning_count_by_code, runtime_avg_by_code = _collect_run_level_metrics(payload)
    generated_at = str(payload.get("generated_at") or fallback_generated_at or "")

    return {
        "generated_at": generated_at,
        "source": source,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "run_count": _to_int(totals.get("run_count"), 0),
        "warning_count": _to_int(totals.get("warning_count"), 0),
        "runtime_avg_ms": _to_float(runtime.get("avg_ms")),
        "runtime_max_ms": _to_int(runtime.get("max_ms"), 0) if runtime.get("max_ms") is not None else None,
        "warning_types": _normalize_count_map(totals.get("warning_types")),
        "track_c_modes": _normalize_count_map(totals.get("track_c_modes")),
        "track_b_modes": _normalize_count_map(totals.get("track_b_modes")),
        "report_codes": _normalize_count_map(totals.get("report_codes")),
        "year_sets": _normalize_count_map(totals.get("year_sets")),
        "warning_count_by_report_code": warning_count_by_code,
        "runtime_avg_ms_by_report_code": runtime_avg_by_code,
    }


def _json_get(url: str, *, headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_artifact_metrics_payload(
    archive_download_url: str,
    *,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    req = urllib.request.Request(archive_download_url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        data = response.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        candidates = [
            name
            for name in zf.namelist()
            if str(name).endswith("step8_warning_metrics.json")
        ]
        if not candidates:
            raise RuntimeError("step8_warning_metrics.json not found in artifact zip")
        with zf.open(candidates[0], "r") as fp:
            return json.loads(fp.read().decode("utf-8"))


def load_history_entries_from_dir(
    history_dir: Path,
    *,
    exclude_paths: set[Path] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    failures: list[str] = []
    excluded = set(exclude_paths or set())
    if not history_dir.exists() or not history_dir.is_dir():
        return entries, failures
    for path in sorted(history_dir.rglob("*.json")):
        if not path.is_file():
            continue
        if path.resolve() in excluded:
            continue
        name = path.name
        if name.endswith("step8_warning_trends.json"):
            continue
        if "step8_warning_metrics" not in name and "metrics" not in name:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entries.append(
                {
                    "payload": payload,
                    "source": str(path),
                    "run_id": None,
                    "artifact_id": None,
                    "fallback_generated_at": None,
                }
            )
        except Exception:  # pylint: disable=broad-except
            failures.append(str(path))
    return entries, failures


def fetch_history_entries_from_github(
    *,
    github_repo: str,
    github_token: str,
    artifact_name: str,
    history_dir: Path,
    recent_runs: int,
    api_base: str,
    max_pages: int,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary: dict[str, Any] = {
        "enabled": True,
        "repo": github_repo,
        "artifact_name": artifact_name,
        "considered_artifacts": 0,
        "fetched_runs": 0,
        "errors": [],
    }
    if not github_repo or not github_token:
        summary["errors"].append("github_repo or github_token missing")
        return [], summary

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "User-Agent": "dart-export-step8-warning-trends",
    }

    artifacts: list[dict[str, Any]] = []
    per_page = 100
    try:
        for page in range(1, max(1, int(max_pages)) + 1):
            params = urllib.parse.urlencode({"per_page": per_page, "page": page})
            url = f"{api_base.rstrip('/')}/repos/{github_repo}/actions/artifacts?{params}"
            payload = _json_get(url, headers=headers, timeout_seconds=timeout_seconds)
            items = payload.get("artifacts", [])
            if not isinstance(items, list) or not items:
                break
            artifacts.extend(items)
            if len(items) < per_page:
                break
    except urllib.error.URLError as exc:
        summary["errors"].append(f"list_artifacts_failed: {exc}")
        return [], summary
    except Exception as exc:  # pylint: disable=broad-except
        summary["errors"].append(f"list_artifacts_failed: {exc}")
        return [], summary

    filtered = [
        artifact
        for artifact in artifacts
        if str(artifact.get("name", "")) == artifact_name and not bool(artifact.get("expired"))
    ]
    filtered.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    summary["considered_artifacts"] = len(filtered)

    entries: list[dict[str, Any]] = []
    history_dir.mkdir(parents=True, exist_ok=True)
    target_count = max(0, int(recent_runs))

    for artifact in filtered:
        if len(entries) >= target_count:
            break
        artifact_id = _to_int(artifact.get("id"), 0)
        run_id = _to_int((artifact.get("workflow_run") or {}).get("id"), 0)
        created_at = str(artifact.get("created_at", ""))
        cache_file = history_dir / f"artifact_{artifact_id}_run_{run_id}_step8_warning_metrics.json"

        payload: dict[str, Any]
        try:
            if cache_file.exists():
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                archive_download_url = str(artifact.get("archive_download_url", ""))
                if not archive_download_url:
                    raise RuntimeError("archive_download_url missing")
                payload = _download_artifact_metrics_payload(
                    archive_download_url,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                )
                cache_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:  # pylint: disable=broad-except
            summary["errors"].append(f"artifact_{artifact_id}_failed: {exc}")
            continue

        entries.append(
            {
                "payload": payload,
                "source": str(cache_file),
                "run_id": run_id if run_id > 0 else None,
                "artifact_id": artifact_id if artifact_id > 0 else None,
                "fallback_generated_at": created_at or None,
            }
        )
    summary["fetched_runs"] = len(entries)
    return entries, summary


def build_trend_report(
    *,
    current_payload: dict[str, Any],
    current_source: str,
    history_entries: list[dict[str, Any]],
    recent_runs: int,
    fetch_summary: dict[str, Any],
    parse_failures: list[str],
) -> dict[str, Any]:
    points: list[dict[str, Any]] = [
        _build_point(current_payload, source=current_source),
    ]
    for entry in history_entries:
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        points.append(
            _build_point(
                payload,
                source=str(entry.get("source", "")),
                run_id=entry.get("run_id"),
                artifact_id=entry.get("artifact_id"),
                fallback_generated_at=entry.get("fallback_generated_at"),
            )
        )

    dedup: dict[str, dict[str, Any]] = {}
    for point in points:
        source = str(point.get("source", ""))
        if source and source not in dedup:
            dedup[source] = point
    points = list(dedup.values())
    points.sort(key=lambda x: _parse_iso_datetime(str(x.get("generated_at", ""))), reverse=True)

    limit = max(1, int(recent_runs))
    recent_points = points[:limit]
    latest = recent_points[0] if recent_points else None
    previous = None
    previous_selection_mode = "none"
    if latest is not None:
        latest_signature = _point_profile_signature(latest)
        for candidate in recent_points[1:]:
            if _point_profile_signature(candidate) == latest_signature:
                previous = candidate
                previous_selection_mode = "profile_match"
                break
        if previous is None and len(recent_points) > 1:
            previous = recent_points[1]
            previous_selection_mode = "fallback_recent"

    latest_warning = _to_int((latest or {}).get("warning_count"), 0)
    prev_warning = _to_int((previous or {}).get("warning_count"), 0)
    latest_run_count = _to_int((latest or {}).get("run_count"), 0)
    prev_run_count = _to_int((previous or {}).get("run_count"), 0)
    latest_avg = _to_float((latest or {}).get("runtime_avg_ms"))
    prev_avg = _to_float((previous or {}).get("runtime_avg_ms"))

    runtime_avg_delta = None
    if latest_avg is not None and prev_avg is not None:
        runtime_avg_delta = round(latest_avg - prev_avg, 2)

    latest_warning_types = _normalize_count_map((latest or {}).get("warning_types"))
    previous_warning_types = _normalize_count_map((previous or {}).get("warning_types"))

    latest_warning_by_code = _normalize_count_map((latest or {}).get("warning_count_by_report_code"))
    previous_warning_by_code = _normalize_count_map((previous or {}).get("warning_count_by_report_code"))

    latest_runtime_by_code = _normalize_float_map((latest or {}).get("runtime_avg_ms_by_report_code"))
    previous_runtime_by_code = _normalize_float_map((previous or {}).get("runtime_avg_ms_by_report_code"))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recent_runs_limit": limit,
        "point_count_total": len(points),
        "point_count_used": len(recent_points),
        "latest": latest,
        "previous": previous,
        "previous_selection_mode": previous_selection_mode,
        "deltas": {
            "warning_count": latest_warning - prev_warning if previous else None,
            "run_count": latest_run_count - prev_run_count if previous else None,
            "runtime_avg_ms": runtime_avg_delta if previous else None,
            "warning_types": _count_map_delta(latest_warning_types, previous_warning_types) if previous else {},
            "warning_count_by_report_code": _count_map_delta(latest_warning_by_code, previous_warning_by_code)
            if previous
            else {},
            "runtime_avg_ms_by_report_code": _float_map_delta(latest_runtime_by_code, previous_runtime_by_code)
            if previous
            else {},
        },
        "aggregates_recent": {
            "run_count_sum": sum(_to_int(x.get("run_count"), 0) for x in recent_points),
            "warning_count_sum": sum(_to_int(x.get("warning_count"), 0) for x in recent_points),
            "warning_types": _sum_count_maps(
                [_normalize_count_map(x.get("warning_types")) for x in recent_points]
            ),
            "track_c_modes": _sum_count_maps(
                [_normalize_count_map(x.get("track_c_modes")) for x in recent_points]
            ),
            "track_b_modes": _sum_count_maps(
                [_normalize_count_map(x.get("track_b_modes")) for x in recent_points]
            ),
            "report_codes": _sum_count_maps(
                [_normalize_count_map(x.get("report_codes")) for x in recent_points]
            ),
            "year_sets": _sum_count_maps(
                [_normalize_count_map(x.get("year_sets")) for x in recent_points]
            ),
            "warning_count_by_report_code": _sum_count_maps(
                [_normalize_count_map(x.get("warning_count_by_report_code")) for x in recent_points]
            ),
        },
        "points": recent_points,
        "fetch": fetch_summary,
        "parse_failures": parse_failures,
    }
    return report


def load_quality_gate_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("quality gate config root must be mapping")
    return raw


def _parse_optional_int(value: Any, *, field: str, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _to_int_strict(value, field=field, minimum=minimum)


def _parse_optional_float(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    return _to_float_strict(value, field=field)


def _parse_optional_bool(value: Any, *, field: str) -> bool | None:
    if value is None:
        return None
    return _to_bool(value, field=field)


def _parse_threshold_int_map(value: Any, *, field: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be mapping")
    out: dict[str, int] = {}
    for key, raw in value.items():
        key_s = str(key).strip()
        if not key_s:
            continue
        out[key_s] = _to_int_strict(raw, field=f"{field}.{key_s}")
    return dict(sorted(out.items()))


def _parse_threshold_float_map(value: Any, *, field: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be mapping")
    out: dict[str, float] = {}
    for key, raw in value.items():
        key_s = str(key).strip()
        if not key_s:
            continue
        out[key_s] = _to_float_strict(raw, field=f"{field}.{key_s}")
    return dict(sorted(out.items()))


def _parse_report_code_thresholds(value: Any, *, field: str) -> tuple[dict[str, int], dict[str, float]]:
    warning_thresholds: dict[str, int] = {}
    runtime_thresholds: dict[str, float] = {}
    if value is None:
        return warning_thresholds, runtime_thresholds
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be mapping")

    for code, raw in value.items():
        code_s = str(code).strip()
        if not code_s:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"{field}.{code_s} must be mapping")
        warning_raw = raw.get("max_warning_delta")
        runtime_raw = raw.get("max_runtime_avg_ms_delta")
        if warning_raw is not None:
            warning_thresholds[code_s] = _to_int_strict(
                warning_raw,
                field=f"{field}.{code_s}.max_warning_delta",
            )
        if runtime_raw is not None:
            runtime_thresholds[code_s] = _to_float_strict(
                runtime_raw,
                field=f"{field}.{code_s}.max_runtime_avg_ms_delta",
            )

    return dict(sorted(warning_thresholds.items())), dict(sorted(runtime_thresholds.items()))


def resolve_quality_gate_options(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(str(args.quality_gate_config).strip()) if str(args.quality_gate_config).strip() else None
    config_root: dict[str, Any] = {}
    if config_path is not None:
        config_root = load_quality_gate_config(config_path)

    raw_gate = config_root.get("quality_gate") if isinstance(config_root.get("quality_gate"), dict) else config_root
    if not isinstance(raw_gate, dict):
        raw_gate = {}

    cfg_max_warning_delta = _parse_optional_int(
        raw_gate.get("max_warning_delta"),
        field="quality_gate.max_warning_delta",
    )
    cfg_max_runtime_delta = _parse_optional_float(
        raw_gate.get("max_runtime_avg_ms_delta"),
        field="quality_gate.max_runtime_avg_ms_delta",
    )
    cfg_require_previous = _parse_optional_bool(
        raw_gate.get("require_previous"),
        field="quality_gate.require_previous",
    )
    cfg_min_run_count = _parse_optional_int(
        raw_gate.get("min_run_count"),
        field="quality_gate.min_run_count",
        minimum=1,
    )
    cfg_min_run_count_by_report_code = _parse_optional_int(
        raw_gate.get("min_run_count_by_report_code"),
        field="quality_gate.min_run_count_by_report_code",
        minimum=1,
    )

    cfg_warning_by_code_direct = _parse_threshold_int_map(
        raw_gate.get("max_warning_delta_by_report_code"),
        field="quality_gate.max_warning_delta_by_report_code",
    )
    cfg_runtime_by_code_direct = _parse_threshold_float_map(
        raw_gate.get("max_runtime_avg_ms_delta_by_report_code"),
        field="quality_gate.max_runtime_avg_ms_delta_by_report_code",
    )
    cfg_warning_by_code_nested, cfg_runtime_by_code_nested = _parse_report_code_thresholds(
        raw_gate.get("report_code_thresholds"),
        field="quality_gate.report_code_thresholds",
    )

    max_warning_delta_by_report_code = dict(cfg_warning_by_code_direct)
    max_warning_delta_by_report_code.update(cfg_warning_by_code_nested)

    max_runtime_avg_ms_delta_by_report_code = dict(cfg_runtime_by_code_direct)
    max_runtime_avg_ms_delta_by_report_code.update(cfg_runtime_by_code_nested)

    max_warning_type_deltas = _parse_threshold_int_map(
        raw_gate.get("max_warning_type_delta"),
        field="quality_gate.max_warning_type_delta",
    )
    ignore_warning_types = _normalize_string_set(raw_gate.get("ignore_warning_types", []))

    resolved = {
        "config_path": str(config_path) if config_path is not None else "",
        "config_loaded": bool(raw_gate),
        "max_warning_delta": (
            int(args.quality_gate_max_warning_delta)
            if args.quality_gate_max_warning_delta is not None
            else cfg_max_warning_delta
        ),
        "max_runtime_avg_ms_delta": (
            float(args.quality_gate_max_runtime_avg_ms_delta)
            if args.quality_gate_max_runtime_avg_ms_delta is not None
            else cfg_max_runtime_delta
        ),
        "require_previous": bool(args.quality_gate_require_previous or bool(cfg_require_previous)),
        "min_run_count": (
            int(args.quality_gate_min_run_count)
            if args.quality_gate_min_run_count is not None
            else cfg_min_run_count
        ),
        "min_run_count_by_report_code": (
            int(args.quality_gate_min_run_count_by_report_code)
            if args.quality_gate_min_run_count_by_report_code is not None
            else cfg_min_run_count_by_report_code
        ),
        "max_warning_delta_by_report_code": dict(sorted(max_warning_delta_by_report_code.items())),
        "max_runtime_avg_ms_delta_by_report_code": dict(sorted(max_runtime_avg_ms_delta_by_report_code.items())),
        "max_warning_type_delta": dict(sorted(max_warning_type_deltas.items())),
        "ignore_warning_types": sorted(ignore_warning_types),
    }
    return resolved


def evaluate_quality_gate(
    report: dict[str, Any],
    *,
    max_warning_delta: int | None,
    max_runtime_avg_ms_delta: float | None,
    require_previous: bool,
    min_run_count: int | None = None,
    min_run_count_by_report_code: int | None = None,
    max_warning_delta_by_report_code: dict[str, int] | None = None,
    max_runtime_avg_ms_delta_by_report_code: dict[str, float] | None = None,
    max_warning_type_delta: dict[str, int] | None = None,
    ignore_warning_types: set[str] | None = None,
) -> dict[str, Any]:
    warning_by_code_thresholds = dict(max_warning_delta_by_report_code or {})
    runtime_by_code_thresholds = dict(max_runtime_avg_ms_delta_by_report_code or {})
    warning_type_thresholds = dict(max_warning_type_delta or {})
    ignore_warning_types_set = set(ignore_warning_types or set())

    enabled = (
        max_warning_delta is not None
        or max_runtime_avg_ms_delta is not None
        or bool(require_previous)
        or min_run_count is not None
        or min_run_count_by_report_code is not None
        or bool(warning_by_code_thresholds)
        or bool(runtime_by_code_thresholds)
        or bool(warning_type_thresholds)
    )
    gate: dict[str, Any] = {
        "enabled": bool(enabled),
        "status": "disabled",
        "thresholds": {
            "max_warning_delta": max_warning_delta,
            "max_runtime_avg_ms_delta": max_runtime_avg_ms_delta,
            "require_previous": bool(require_previous),
            "min_run_count": min_run_count,
            "min_run_count_by_report_code": min_run_count_by_report_code,
            "max_warning_delta_by_report_code": dict(sorted(warning_by_code_thresholds.items())),
            "max_runtime_avg_ms_delta_by_report_code": dict(sorted(runtime_by_code_thresholds.items())),
            "max_warning_type_delta": dict(sorted(warning_type_thresholds.items())),
            "ignore_warning_types": sorted(ignore_warning_types_set),
        },
        "violations": [],
        "skip_reasons": [],
    }
    if not enabled:
        return gate

    previous = report.get("previous")
    if not isinstance(previous, dict) or not previous:
        if require_previous:
            gate["status"] = "fail"
            gate["violations"] = ["previous_run_missing"]
        else:
            gate["status"] = "skipped"
        return gate

    latest_point = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    previous_point = report.get("previous") if isinstance(report.get("previous"), dict) else {}

    skip_reasons: list[str] = []
    latest_run_count = _to_int((latest_point or {}).get("run_count"), 0)
    previous_run_count = _to_int((previous_point or {}).get("run_count"), 0)
    if min_run_count is not None:
        required_min_run_count = int(min_run_count)
        if latest_run_count < required_min_run_count or previous_run_count < required_min_run_count:
            skip_reasons.append(
                "min_run_count_not_met "
                f"latest={latest_run_count} previous={previous_run_count} required={required_min_run_count}"
            )
    if skip_reasons:
        gate["status"] = "skipped"
        gate["skip_reasons"] = skip_reasons
        return gate

    deltas = report.get("deltas", {})
    if not isinstance(deltas, dict):
        deltas = {}

    violations: list[str] = []

    warning_delta = deltas.get("warning_count")
    warning_delta_i = _to_int(warning_delta, 0) if warning_delta is not None else None
    if (
        max_warning_delta is not None
        and warning_delta_i is not None
        and warning_delta_i > int(max_warning_delta)
    ):
        violations.append(
            f"warning_count_delta={warning_delta_i} > {int(max_warning_delta)}"
        )

    runtime_delta = deltas.get("runtime_avg_ms")
    runtime_delta_f = _to_float(runtime_delta) if runtime_delta is not None else None
    if (
        max_runtime_avg_ms_delta is not None
        and runtime_delta_f is not None
        and runtime_delta_f > float(max_runtime_avg_ms_delta)
    ):
        violations.append(
            f"runtime_avg_ms_delta={runtime_delta_f} > {float(max_runtime_avg_ms_delta)}"
        )

    latest = latest_point if isinstance(latest_point, dict) else {}

    latest_report_code_counts = _normalize_count_map((latest or {}).get("report_codes"))
    previous_report_code_counts = _normalize_count_map((previous_point or {}).get("report_codes"))

    warning_delta_by_code = deltas.get("warning_count_by_report_code", {})
    if not isinstance(warning_delta_by_code, dict):
        warning_delta_by_code = {}

    runtime_delta_by_code = deltas.get("runtime_avg_ms_by_report_code", {})
    if not isinstance(runtime_delta_by_code, dict):
        runtime_delta_by_code = {}
    min_report_code_samples = (
        int(min_run_count_by_report_code)
        if min_run_count_by_report_code is not None
        else None
    )

    for report_code, threshold in sorted(warning_by_code_thresholds.items()):
        latest_count = latest_report_code_counts.get(report_code, 0)
        previous_count = previous_report_code_counts.get(report_code, 0)
        if latest_count <= 0:
            continue
        if previous_count <= 0:
            continue
        if (
            min_report_code_samples is not None
            and (latest_count < min_report_code_samples or previous_count < min_report_code_samples)
        ):
            continue
        delta_i = _to_int(warning_delta_by_code.get(report_code), 0)
        if delta_i > int(threshold):
            violations.append(
                f"report_code={report_code} warning_count_delta={delta_i} > {int(threshold)}"
            )

    for report_code, threshold in sorted(runtime_by_code_thresholds.items()):
        latest_count = latest_report_code_counts.get(report_code, 0)
        previous_count = previous_report_code_counts.get(report_code, 0)
        if latest_count <= 0:
            continue
        if previous_count <= 0:
            continue
        if (
            min_report_code_samples is not None
            and (latest_count < min_report_code_samples or previous_count < min_report_code_samples)
        ):
            continue
        delta_f = _to_float(runtime_delta_by_code.get(report_code))
        if delta_f is None:
            continue
        if delta_f > float(threshold):
            violations.append(
                f"report_code={report_code} runtime_avg_ms_delta={delta_f} > {float(threshold)}"
            )

    warning_type_deltas = deltas.get("warning_types", {})
    if not isinstance(warning_type_deltas, dict):
        warning_type_deltas = {}

    for warning_type, threshold in sorted(warning_type_thresholds.items()):
        warning_type_key = str(warning_type).strip()
        if not warning_type_key or warning_type_key in ignore_warning_types_set:
            continue
        delta_i = _to_int(warning_type_deltas.get(warning_type_key), 0)
        if delta_i > int(threshold):
            violations.append(
                f"warning_type={warning_type_key} delta={delta_i} > {int(threshold)}"
            )

    gate["status"] = "fail" if violations else "pass"
    gate["violations"] = violations
    return gate


def _render_count_lines(title: str, counter: dict[str, int]) -> list[str]:
    lines = [f"## {title}"]
    if not counter:
        lines.append("- (none)")
        return lines
    for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{key}`: {value}")
    return lines


def _render_float_lines(title: str, values: dict[str, float]) -> list[str]:
    lines = [f"## {title}"]
    if not values:
        lines.append("- (none)")
        return lines
    for key, value in sorted(values.items()):
        lines.append(f"- `{key}`: {value}")
    return lines


def _render_signed_int_lines(title: str, values: dict[str, int]) -> list[str]:
    lines = [f"## {title}"]
    if not values:
        lines.append("- (none)")
        return lines
    for key, value in sorted(values.items()):
        lines.append(f"- `{key}`: {value:+d}")
    return lines


def _render_signed_float_lines(title: str, values: dict[str, float]) -> list[str]:
    lines = [f"## {title}"]
    if not values:
        lines.append("- (none)")
        return lines
    for key, value in sorted(values.items()):
        lines.append(f"- `{key}`: {value:+.2f}")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest") or {}
    previous = report.get("previous") or {}
    deltas = report.get("deltas") or {}
    fetch = report.get("fetch") or {}
    points = report.get("points") or []
    quality_gate = report.get("quality_gate") or {}
    quality_gate_config = report.get("quality_gate_config") or {}

    lines: list[str] = []
    lines.append("# Step8 Warning Metrics Trend Report")
    lines.append("")
    lines.append(f"- generated_at: `{report.get('generated_at')}`")
    lines.append(f"- recent_runs_limit: `{report.get('recent_runs_limit')}`")
    lines.append(f"- points_used: `{report.get('point_count_used')}` / `{report.get('point_count_total')}`")
    lines.append(f"- latest_source: `{latest.get('source')}`")
    lines.append(f"- previous_selection_mode: `{report.get('previous_selection_mode')}`")
    lines.append(
        f"- quality_gate_config: path=`{quality_gate_config.get('path')}` loaded=`{quality_gate_config.get('loaded')}`"
    )
    lines.append("")
    lines.append("## Latest vs Previous")
    lines.append(f"- latest.warning_count: `{latest.get('warning_count')}`")
    lines.append(f"- previous.warning_count: `{previous.get('warning_count')}`")
    lines.append(f"- delta.warning_count: `{deltas.get('warning_count')}`")
    lines.append(f"- delta.run_count: `{deltas.get('run_count')}`")
    lines.append(f"- delta.runtime_avg_ms: `{deltas.get('runtime_avg_ms')}`")
    lines.append("")
    lines.append("## Quality Gate")
    lines.append(f"- enabled: `{quality_gate.get('enabled')}`")
    lines.append(f"- status: `{quality_gate.get('status')}`")
    gate_thresholds = quality_gate.get("thresholds", {})
    lines.append(
        f"- thresholds(global): warning_delta<={gate_thresholds.get('max_warning_delta')} "
        f"runtime_avg_ms_delta<={gate_thresholds.get('max_runtime_avg_ms_delta')} "
        f"min_run_count>={gate_thresholds.get('min_run_count')} "
        f"require_previous={gate_thresholds.get('require_previous')}"
    )
    lines.append(
        "- thresholds(by_report_code.min_run_count): "
        f"`{gate_thresholds.get('min_run_count_by_report_code')}`"
    )
    lines.append(
        "- thresholds(by_report_code.warning): "
        f"`{(gate_thresholds.get('max_warning_delta_by_report_code') or {})}`"
    )
    lines.append(
        "- thresholds(by_report_code.runtime_avg_ms): "
        f"`{(gate_thresholds.get('max_runtime_avg_ms_delta_by_report_code') or {})}`"
    )
    lines.append(
        "- thresholds(by_warning_type): "
        f"`{(gate_thresholds.get('max_warning_type_delta') or {})}`"
    )
    lines.append(
        "- ignore_warning_types: "
        f"`{(gate_thresholds.get('ignore_warning_types') or [])}`"
    )
    violations = quality_gate.get("violations", [])
    if violations:
        lines.append("- violations:")
        for item in violations:
            lines.append(f"  - `{item}`")
    else:
        lines.append("- violations: (none)")
    skip_reasons = quality_gate.get("skip_reasons", [])
    if skip_reasons:
        lines.append("- skip_reasons:")
        for item in skip_reasons:
            lines.append(f"  - `{item}`")
    else:
        lines.append("- skip_reasons: (none)")
    lines.append("")
    lines.append("## Recent Points")
    if points:
        for idx, point in enumerate(points, start=1):
            lines.append(
                f"- #{idx} generated_at=`{point.get('generated_at')}` "
                f"run_count={point.get('run_count')} "
                f"warning_count={point.get('warning_count')} "
                f"runtime_avg_ms={point.get('runtime_avg_ms')} "
                f"source=`{point.get('source')}`"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.extend(
        _render_signed_int_lines(
            "Delta Warning Count by Report Code",
            deltas.get("warning_count_by_report_code", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_signed_float_lines(
            "Delta Runtime Avg ms by Report Code",
            deltas.get("runtime_avg_ms_by_report_code", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_signed_int_lines(
            "Delta Warning Types",
            deltas.get("warning_types", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_count_lines(
            "Aggregated Warning Types (Recent)",
            (report.get("aggregates_recent") or {}).get("warning_types", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_count_lines(
            "Aggregated Track C Modes (Recent)",
            (report.get("aggregates_recent") or {}).get("track_c_modes", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_count_lines(
            "Aggregated Track B Modes (Recent)",
            (report.get("aggregates_recent") or {}).get("track_b_modes", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_count_lines(
            "Aggregated Warning Count by Report Code (Recent)",
            (report.get("aggregates_recent") or {}).get("warning_count_by_report_code", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_count_lines(
            "Aggregated Report Codes (Recent)",
            (report.get("aggregates_recent") or {}).get("report_codes", {}),
        )
    )
    lines.append("")
    lines.extend(
        _render_count_lines(
            "Aggregated Year Sets (Recent)",
            (report.get("aggregates_recent") or {}).get("year_sets", {}),
        )
    )
    lines.append("")
    lines.append("## Fetch Summary")
    lines.append(f"- enabled: `{fetch.get('enabled')}`")
    lines.append(f"- repo: `{fetch.get('repo')}`")
    lines.append(f"- artifact_name: `{fetch.get('artifact_name')}`")
    lines.append(f"- considered_artifacts: `{fetch.get('considered_artifacts')}`")
    lines.append(f"- fetched_runs: `{fetch.get('fetched_runs')}`")
    fetch_errors = fetch.get("errors", [])
    if fetch_errors:
        lines.append("- fetch_errors:")
        for err in fetch_errors:
            lines.append(f"  - `{err}`")
    else:
        lines.append("- fetch_errors: (none)")
    parse_failures = report.get("parse_failures", [])
    lines.append("")
    lines.append("## Parse Failures")
    if parse_failures:
        for path in parse_failures:
            lines.append(f"- `{path}`")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Step8 warning trend report from recent runs.")
    parser.add_argument(
        "--current-metrics-json",
        default="/tmp/step8_online_artifacts/metrics/step8_warning_metrics.json",
        help="Current run metrics JSON path.",
    )
    parser.add_argument(
        "--history-dir",
        default="/tmp/step8_online_artifacts/history",
        help="Directory storing previous run metrics snapshots.",
    )
    parser.add_argument(
        "--output-json",
        default="/tmp/step8_online_artifacts/metrics/step8_warning_trends.json",
        help="Trend report JSON output path.",
    )
    parser.add_argument(
        "--output-md",
        default="/tmp/step8_online_artifacts/metrics/step8_warning_trends.md",
        help="Trend report markdown output path.",
    )
    parser.add_argument(
        "--recent-runs",
        type=int,
        default=5,
        help="Number of recent points to keep in trend report.",
    )
    parser.add_argument(
        "--fetch-from-github",
        action="store_true",
        help="Fetch previous step8-warning-metrics artifacts from GitHub Actions.",
    )
    parser.add_argument(
        "--github-repo",
        default="",
        help="Repository slug like owner/repo for GitHub Actions artifact query.",
    )
    parser.add_argument(
        "--github-token",
        default="",
        help="GitHub token used for artifact query/download.",
    )
    parser.add_argument(
        "--artifact-name",
        default="step8-warning-metrics",
        help="Artifact name to query in GitHub Actions.",
    )
    parser.add_argument(
        "--github-api-base",
        default="https://api.github.com",
        help="GitHub API base URL.",
    )
    parser.add_argument(
        "--max-artifact-pages",
        type=int,
        default=3,
        help="Max pages to scan in GitHub Actions artifact list API.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=15.0,
        help="HTTP timeout seconds for GitHub API/artifact download.",
    )
    parser.add_argument(
        "--quality-gate-config",
        default=str(DEFAULT_QUALITY_GATE_CONFIG_PATH),
        help=(
            "Quality gate config YAML path "
            f"(default: {DEFAULT_QUALITY_GATE_CONFIG_PATH})."
        ),
    )
    parser.add_argument(
        "--quality-gate-max-warning-delta",
        type=int,
        default=None,
        help="Fail gate when warning_count delta exceeds this threshold.",
    )
    parser.add_argument(
        "--quality-gate-max-runtime-avg-ms-delta",
        type=float,
        default=None,
        help="Fail gate when runtime_avg_ms delta exceeds this threshold.",
    )
    parser.add_argument(
        "--quality-gate-require-previous",
        action="store_true",
        help="Require at least one previous point for gate evaluation.",
    )
    parser.add_argument(
        "--quality-gate-min-run-count",
        type=int,
        default=None,
        help="Skip gate when latest/previous run_count is below this threshold.",
    )
    parser.add_argument(
        "--quality-gate-min-run-count-by-report-code",
        type=int,
        default=None,
        help="Require at least this many runs per report_code for code-level gate checks.",
    )
    parser.add_argument(
        "--fail-on-quality-gate",
        action="store_true",
        help="Exit with non-zero code when quality gate status is fail.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    current_metrics_json = Path(str(args.current_metrics_json))
    history_dir = Path(str(args.history_dir))
    output_json = Path(str(args.output_json))
    output_md = Path(str(args.output_md))

    if not current_metrics_json.exists():
        print(f"[warning-trends] current metrics not found: {current_metrics_json}")
        return 1

    current_payload = json.loads(current_metrics_json.read_text(encoding="utf-8"))

    parse_failures: list[str] = []
    history_entries, failures = load_history_entries_from_dir(
        history_dir,
        exclude_paths={current_metrics_json.resolve()},
    )
    parse_failures.extend(failures)

    fetch_summary: dict[str, Any] = {
        "enabled": False,
        "repo": str(args.github_repo or ""),
        "artifact_name": str(args.artifact_name or ""),
        "considered_artifacts": 0,
        "fetched_runs": 0,
        "errors": [],
    }
    if args.fetch_from_github:
        fetched_entries, fetch_summary = fetch_history_entries_from_github(
            github_repo=str(args.github_repo or "").strip(),
            github_token=str(args.github_token or "").strip(),
            artifact_name=str(args.artifact_name or "step8-warning-metrics"),
            history_dir=history_dir,
            recent_runs=max(0, int(args.recent_runs)),
            api_base=str(args.github_api_base or "https://api.github.com"),
            max_pages=max(1, int(args.max_artifact_pages)),
            timeout_seconds=max(1.0, float(args.request_timeout_seconds)),
        )
        # Prefer freshly fetched entries over stale local snapshots when available.
        if fetched_entries:
            existing_sources = {str(entry.get("source", "")) for entry in history_entries}
            for entry in fetched_entries:
                source = str(entry.get("source", ""))
                if source in existing_sources:
                    continue
                history_entries.append(entry)
                existing_sources.add(source)

    report = build_trend_report(
        current_payload=current_payload,
        current_source=str(current_metrics_json),
        history_entries=history_entries,
        recent_runs=max(1, int(args.recent_runs)),
        fetch_summary=fetch_summary,
        parse_failures=parse_failures,
    )

    try:
        gate_options = resolve_quality_gate_options(args)
    except ValueError as exc:
        print(f"[warning-trends] quality_gate_config_error detail={exc}")
        return 2

    quality_gate = evaluate_quality_gate(
        report,
        max_warning_delta=gate_options.get("max_warning_delta"),
        max_runtime_avg_ms_delta=gate_options.get("max_runtime_avg_ms_delta"),
        require_previous=bool(gate_options.get("require_previous")),
        min_run_count=gate_options.get("min_run_count"),
        min_run_count_by_report_code=gate_options.get("min_run_count_by_report_code"),
        max_warning_delta_by_report_code=gate_options.get("max_warning_delta_by_report_code", {}),
        max_runtime_avg_ms_delta_by_report_code=gate_options.get("max_runtime_avg_ms_delta_by_report_code", {}),
        max_warning_type_delta=gate_options.get("max_warning_type_delta", {}),
        ignore_warning_types=set(gate_options.get("ignore_warning_types", [])),
    )
    report["quality_gate"] = quality_gate
    report["quality_gate_config"] = {
        "path": gate_options.get("config_path", ""),
        "loaded": bool(gate_options.get("config_loaded")),
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")

    print(
        f"[warning-trends] points_used={report.get('point_count_used')} "
        f"points_total={report.get('point_count_total')} "
        f"fetched_runs={fetch_summary.get('fetched_runs')}"
    )
    print(
        f"[warning-trends] quality_gate_status={quality_gate.get('status')} "
        f"violations={len(quality_gate.get('violations', []))} "
        f"config_loaded={report.get('quality_gate_config', {}).get('loaded')}"
    )
    print(f"[warning-trends] output_json={output_json}")
    print(f"[warning-trends] output_md={output_md}")
    if bool(args.fail_on_quality_gate) and str(quality_gate.get("status")) == "fail":
        print("[warning-trends] quality gate failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
