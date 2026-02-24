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


def _parse_iso_datetime(value: str | None) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


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
    }


def _sum_count_maps(items: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        for key, count in item.items():
            out[key] = int(out.get(key, 0)) + int(count)
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


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
    previous = recent_points[1] if len(recent_points) > 1 else None

    latest_warning = _to_int((latest or {}).get("warning_count"), 0)
    prev_warning = _to_int((previous or {}).get("warning_count"), 0)
    latest_run_count = _to_int((latest or {}).get("run_count"), 0)
    prev_run_count = _to_int((previous or {}).get("run_count"), 0)
    latest_avg = _to_float((latest or {}).get("runtime_avg_ms"))
    prev_avg = _to_float((previous or {}).get("runtime_avg_ms"))
    runtime_avg_delta = None
    if latest_avg is not None and prev_avg is not None:
        runtime_avg_delta = round(latest_avg - prev_avg, 2)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recent_runs_limit": limit,
        "point_count_total": len(points),
        "point_count_used": len(recent_points),
        "latest": latest,
        "previous": previous,
        "deltas": {
            "warning_count": latest_warning - prev_warning if previous else None,
            "run_count": latest_run_count - prev_run_count if previous else None,
            "runtime_avg_ms": runtime_avg_delta if previous else None,
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
        },
        "points": recent_points,
        "fetch": fetch_summary,
        "parse_failures": parse_failures,
    }
    return report


def _render_count_lines(title: str, counter: dict[str, int]) -> list[str]:
    lines = [f"## {title}"]
    if not counter:
        lines.append("- (none)")
        return lines
    for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{key}`: {value}")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest") or {}
    previous = report.get("previous") or {}
    deltas = report.get("deltas") or {}
    fetch = report.get("fetch") or {}
    points = report.get("points") or []

    lines: list[str] = []
    lines.append("# Step8 Warning Metrics Trend Report")
    lines.append("")
    lines.append(f"- generated_at: `{report.get('generated_at')}`")
    lines.append(f"- recent_runs_limit: `{report.get('recent_runs_limit')}`")
    lines.append(f"- points_used: `{report.get('point_count_used')}` / `{report.get('point_count_total')}`")
    lines.append(f"- latest_source: `{latest.get('source')}`")
    lines.append("")
    lines.append("## Latest vs Previous")
    lines.append(f"- latest.warning_count: `{latest.get('warning_count')}`")
    lines.append(f"- previous.warning_count: `{previous.get('warning_count')}`")
    lines.append(f"- delta.warning_count: `{deltas.get('warning_count')}`")
    lines.append(f"- delta.run_count: `{deltas.get('run_count')}`")
    lines.append(f"- delta.runtime_avg_ms: `{deltas.get('runtime_avg_ms')}`")
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
    lines.extend(_render_count_lines("Aggregated Warning Types (Recent)", (report.get("aggregates_recent") or {}).get("warning_types", {})))
    lines.append("")
    lines.extend(_render_count_lines("Aggregated Track C Modes (Recent)", (report.get("aggregates_recent") or {}).get("track_c_modes", {})))
    lines.append("")
    lines.extend(_render_count_lines("Aggregated Track B Modes (Recent)", (report.get("aggregates_recent") or {}).get("track_b_modes", {})))
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

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")

    print(
        f"[warning-trends] points_used={report.get('point_count_used')} "
        f"points_total={report.get('point_count_total')} "
        f"fetched_runs={fetch_summary.get('fetched_runs')}"
    )
    print(f"[warning-trends] output_json={output_json}")
    print(f"[warning-trends] output_md={output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
