#!/usr/bin/env python3
"""Online Step8 integration regression gate.

This script runs real Step8 pipeline executions and validates summary payloads.
It is intended for workflow_dispatch runs with DART_API_KEY configured.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_COMPANIES = ["삼성전자", "SK하이닉스", "LG전자"]
DEFAULT_REPORT_CODES = ["11011"]
SCHEMA_VERSION = "1.1"


@dataclass
class CompanyRule:
    allowed_segment_modes: tuple[str, ...]
    min_segment_rows_when_parsed: int
    require_single_segment_notice: bool


COMPANY_RULES: dict[str, CompanyRule] = {
    "삼성전자": CompanyRule(
        allowed_segment_modes=("parsed",),
        min_segment_rows_when_parsed=1,
        require_single_segment_notice=False,
    ),
    "SK하이닉스": CompanyRule(
        allowed_segment_modes=("parsed", "skipped(single_segment_notice)"),
        min_segment_rows_when_parsed=1,
        require_single_segment_notice=True,
    ),
    "LG전자": CompanyRule(
        allowed_segment_modes=("parsed",),
        min_segment_rows_when_parsed=1,
        require_single_segment_notice=False,
    ),
}


TRANSIENT_ERROR_MARKERS = (
    "dns 해석 실패",
    "failed to resolve",
    "temporary failure in name resolution",
    "network is unreachable",
    "네트워크 연결 실패",
    "요청 시간 초과",
)


def to_safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value).strip("_") or "sample"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run online Step8 integration regression gate.")
    parser.add_argument(
        "--companies",
        default=",".join(DEFAULT_COMPANIES),
        help="Comma-separated companies to verify.",
    )
    parser.add_argument(
        "--years",
        default="2024",
        help="Years passed to Step8 (default: 2024).",
    )
    parser.add_argument(
        "--report-codes",
        default=",".join(DEFAULT_REPORT_CODES),
        help="Comma-separated report codes to verify (default: 11011).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum retries per company on transient failures (default: 3).",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=2.0,
        help="Sleep seconds between retries (default: 2.0).",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/step8_online_regression_gate",
        help="Directory for temporary Step8 outputs.",
    )
    parser.add_argument(
        "--min-success-per-report-code",
        type=int,
        default=0,
        help=(
            "Minimum PASS count required per report code (default: 0, "
            "which means all company checks must pass)."
        ),
    )
    return parser.parse_args(argv)


def run_step8(company: str, years: str, report_code: str, output_dir: Path) -> tuple[int, str, Path]:
    token = to_safe_token(company)
    code = str(report_code).strip()
    out_xlsx = output_dir / f"{token}_{code}_step8.xlsx"
    out_summary = output_dir / f"{token}_{code}_step8_summary.json"
    cmd = [
        sys.executable,
        "main.py",
        "--step8-run-pipeline",
        "--company-name",
        company,
        "--years",
        years,
        "--report-code",
        code,
        "--step8-strict-trackc",
        "--output-path",
        str(out_xlsx),
        "--step8-summary-path",
        str(out_summary),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, output, out_summary


def is_transient_failure(output: str) -> bool:
    lowered = str(output or "").lower()
    return any(marker in lowered for marker in TRANSIENT_ERROR_MARKERS)


def extract_error_snippet(output: str) -> str:
    for line in (output or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("[ERROR]"):
            return stripped
    for line in reversed((output or "").splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:220]
    return "no_output"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_years_csv(value: str) -> list[str]:
    years = [str(x).strip() for x in str(value or "").split(",") if str(x).strip()]
    normalized: list[str] = []
    for year in years:
        if year not in normalized:
            normalized.append(year)
    return normalized


def validate_summary(
    company: str,
    report_code: str,
    expected_years: list[str],
    payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    expected_report_code = str(report_code).strip()

    if str(payload.get("schema_version")) != SCHEMA_VERSION:
        errors.append(f"schema_version={payload.get('schema_version')} (expected {SCHEMA_VERSION})")
    if str(payload.get("report_code")) != expected_report_code:
        errors.append(
            f"report_code={payload.get('report_code')} (expected {expected_report_code})"
        )
    payload_years = [str(x) for x in payload.get("years", []) if str(x)]
    if sorted(payload_years) != sorted(expected_years):
        errors.append(
            f"years={payload_years} (expected {sorted(expected_years)})"
        )

    track_a = payload.get("track_a", {})
    if _to_int(track_a.get("bs_rows")) <= 0:
        errors.append(f"track_a.bs_rows={track_a.get('bs_rows')} (expected > 0)")
    if _to_int(track_a.get("cf_rows")) <= 0:
        errors.append(f"track_a.cf_rows={track_a.get('cf_rows')} (expected > 0)")

    track_c_mode = str(payload.get("track_c", {}).get("mode", ""))
    if track_c_mode != "parsed":
        errors.append(f"track_c.mode={track_c_mode} (expected parsed)")

    writes = payload.get("writes", [])
    if not isinstance(writes, list) or not writes:
        errors.append("writes is empty")

    metrics = payload.get("metrics", {})
    if not isinstance(metrics, dict):
        errors.append("metrics is missing")
    else:
        if _to_int(metrics.get("runtime_ms")) <= 0:
            errors.append(f"metrics.runtime_ms={metrics.get('runtime_ms')} (expected > 0)")
        warning_types = metrics.get("warning_types")
        if not isinstance(warning_types, dict):
            errors.append("metrics.warning_types is missing")
        normalizer = metrics.get("normalizer")
        if not isinstance(normalizer, dict):
            errors.append("metrics.normalizer is missing")

    track_b = payload.get("track_b_fallback", {})
    segment_mode = str(track_b.get("mode", ""))
    segment_rows = _to_int(track_b.get("segment_rows"))
    sources = track_b.get("single_segment_sources", [])
    infos = payload.get("infos", [])
    has_single_segment_notice = bool(sources) or any("단일사업부문" in str(x) for x in infos)

    if expected_report_code != "11011":
        allowed = {
            "parsed",
            "skipped(single_segment_notice)",
            "skipped(no_segment_data)",
            "skipped(no_docs)",
        }
        if segment_mode not in allowed:
            errors.append(f"track_b_fallback.mode={segment_mode} (unexpected)")
            return errors
        if segment_mode == "parsed" and segment_rows < 1:
            errors.append("track_b_fallback.segment_rows=0 (expected > 0 when parsed)")
        return errors

    rule = COMPANY_RULES.get(company)
    if rule is None:
        if segment_mode not in {"parsed", "skipped(single_segment_notice)", "skipped(no_segment_data)"}:
            errors.append(f"track_b_fallback.mode={segment_mode} (unexpected)")
        return errors

    if segment_mode not in rule.allowed_segment_modes:
        errors.append(
            f"track_b_fallback.mode={segment_mode} (allowed: {','.join(rule.allowed_segment_modes)})"
        )
    if segment_mode == "parsed" and segment_rows < rule.min_segment_rows_when_parsed:
        errors.append(
            f"track_b_fallback.segment_rows={segment_rows} "
            f"(expected >= {rule.min_segment_rows_when_parsed} when parsed)"
        )
    if segment_mode == "skipped(single_segment_notice)" and rule.require_single_segment_notice:
        if not has_single_segment_notice:
            errors.append(
                "single-segment notice evidence missing (single_segment_sources/infos)"
            )

    return errors


def run_company_with_retries(
    *,
    company: str,
    years: str,
    report_code: str,
    expected_years: list[str],
    output_dir: Path,
    max_retries: int,
    retry_delay_seconds: float,
) -> tuple[bool, str]:
    attempts = max(1, int(max_retries))
    for attempt in range(1, attempts + 1):
        print(
            f"[online-step8] try company={company} report_code={report_code} "
            f"attempt={attempt}/{attempts}"
        )
        rc, output, summary_path = run_step8(company, years, report_code, output_dir)
        if rc != 0:
            snippet = extract_error_snippet(output)
            transient = is_transient_failure(output)
            print(
                f"[online-step8] command_fail company={company} report_code={report_code} "
                f"transient={transient} detail={snippet}"
            )
            if transient and attempt < attempts:
                time.sleep(max(0.0, float(retry_delay_seconds)))
                continue
            return False, snippet

        if not summary_path.exists():
            return False, f"summary missing: {summary_path}"

        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-except
            return False, f"summary parse failed: {exc}"

        errors = validate_summary(company, report_code, expected_years, payload)
        if errors:
            return False, "; ".join(errors)

        track_b = payload.get("track_b_fallback", {})
        print(
            f"[online-step8] PASS company={company} report_code={report_code} "
            f"years={','.join(payload.get('years', []))} "
            f"track_c={payload.get('track_c', {}).get('mode')} "
            f"segment_mode={track_b.get('mode')} "
            f"segment_rows={track_b.get('segment_rows')}"
        )
        return True, "ok"

    return False, "unknown"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    companies = [x.strip() for x in str(args.companies).split(",") if x.strip()]
    report_codes = [x.strip() for x in str(args.report_codes).split(",") if x.strip()]
    expected_years = parse_years_csv(str(args.years))
    if not companies:
        print("[online-step8] no companies")
        return 2
    if not report_codes:
        print("[online-step8] no report_codes")
        return 2
    if not expected_years:
        print("[online-step8] no years")
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[online-step8] start companies={','.join(companies)} years={args.years} "
        f"report_codes={','.join(report_codes)} max_retries={args.max_retries} "
        f"min_success_per_report_code={args.min_success_per_report_code}"
    )

    pass_by_code: dict[str, list[str]] = {code: [] for code in report_codes}
    fail_by_code: dict[str, list[tuple[str, str]]] = {code: [] for code in report_codes}

    for report_code in report_codes:
        for company in companies:
            ok, detail = run_company_with_retries(
                company=company,
                years=str(args.years),
                report_code=report_code,
                expected_years=expected_years,
                output_dir=output_dir,
                max_retries=int(args.max_retries),
                retry_delay_seconds=float(args.retry_delay_seconds),
            )
            if ok:
                pass_by_code[report_code].append(company)
            else:
                fail_by_code[report_code].append((company, detail))

    total_pass = sum(len(items) for items in pass_by_code.values())
    total_fail = sum(len(items) for items in fail_by_code.values())
    print(f"[online-step8] result pass={total_pass} fail={total_fail}")
    for report_code in report_codes:
        passes = pass_by_code[report_code]
        fails = fail_by_code[report_code]
        print(f"[online-step8] report_code={report_code} pass={len(passes)} fail={len(fails)}")
        if passes:
            print(f"[online-step8] report_code={report_code} pass_list={','.join(passes)}")
        if fails:
            print(
                "[online-step8] report_code="
                f"{report_code} fail_list="
                + ",".join(f"{company}:{detail}" for company, detail in fails)
            )

    min_success = max(0, int(args.min_success_per_report_code))
    if min_success > 0:
        unmet = [
            report_code
            for report_code in report_codes
            if len(pass_by_code[report_code]) < min_success
        ]
        if unmet:
            print(
                f"[online-step8] requirement_fail "
                f"min_success_per_report_code={min_success} unmet={','.join(unmet)}"
            )
            return 1
        return 0

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
