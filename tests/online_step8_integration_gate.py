#!/usr/bin/env python3
"""Online Step8 integration regression gate.

This script runs real Step8 pipeline executions and validates summary payloads.
By default it loads matrix profiles and company validation rules from
`config/online_step8_matrix.yaml`.
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

import yaml


SCHEMA_VERSION = "1.1"
DEFAULT_MATRIX_CONFIG_PATH = Path("config/online_step8_matrix.yaml")
DEFAULT_OUTPUT_DIR = "/tmp/step8_online_regression_gate"


@dataclass
class CompanyRule:
    allowed_segment_modes: tuple[str, ...]
    min_segment_rows_when_parsed: int
    require_single_segment_notice: bool


@dataclass
class MatrixProfile:
    companies: list[str]
    years: str
    report_codes: list[str]
    max_retries: int
    retry_delay_seconds: float
    min_success_per_report_code: int


BUILTIN_COMPANY_RULES: dict[str, CompanyRule] = {
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
        "--matrix-config",
        default=str(DEFAULT_MATRIX_CONFIG_PATH),
        help=(
            "Path to matrix configuration YAML "
            f"(default: {DEFAULT_MATRIX_CONFIG_PATH})."
        ),
    )
    parser.add_argument(
        "--matrix",
        default=None,
        help="Matrix profile name defined in matrix config (default: config.default_matrix).",
    )
    parser.add_argument(
        "--list-matrices",
        action="store_true",
        help="List matrix profiles from config and exit.",
    )
    parser.add_argument(
        "--companies",
        default=None,
        help="Comma-separated companies to verify (overrides matrix).",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="Years passed to Step8 (overrides matrix).",
    )
    parser.add_argument(
        "--report-codes",
        default=None,
        help="Comma-separated report codes to verify (overrides matrix).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Maximum retries per company on transient failures (overrides matrix).",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=None,
        help="Sleep seconds between retries (overrides matrix).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for temporary Step8 outputs (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--min-success-per-report-code",
        type=int,
        default=None,
        help=(
            "Minimum PASS count required per report code "
            "(overrides matrix)."
        ),
    )
    return parser.parse_args(argv)


def _to_int(value: Any, *, field: str, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer: {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}: {parsed}")
    return parsed


def _to_float(value: Any, *, field: str, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number: {value!r}") from exc
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
    raise ValueError(f"{field} must be boolean: {value!r}")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_csv_or_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [token.strip() for token in value.split(",") if token.strip()]
        return _dedupe(items)
    if isinstance(value, list):
        items = [str(token).strip() for token in value if str(token).strip()]
        return _dedupe(items)
    raise ValueError(f"{field} must be comma-separated string or list: {value!r}")


def parse_years_csv(value: str) -> list[str]:
    years = [str(x).strip() for x in str(value or "").split(",") if str(x).strip()]
    return _dedupe(years)


def normalize_years_value(value: Any, *, field: str) -> str:
    years = parse_csv_or_list(value, field=field)
    if not years:
        raise ValueError(f"{field} must not be empty")
    return ",".join(years)


def parse_company_rule(company: str, raw: Any) -> CompanyRule:
    if not isinstance(raw, dict):
        raise ValueError(f"company_rules.{company} must be a mapping")

    allowed_segment_modes = tuple(
        parse_csv_or_list(raw.get("allowed_segment_modes"), field=f"company_rules.{company}.allowed_segment_modes")
    )
    if not allowed_segment_modes:
        raise ValueError(f"company_rules.{company}.allowed_segment_modes must not be empty")

    return CompanyRule(
        allowed_segment_modes=allowed_segment_modes,
        min_segment_rows_when_parsed=_to_int(
            raw.get("min_segment_rows_when_parsed", 1),
            field=f"company_rules.{company}.min_segment_rows_when_parsed",
            minimum=0,
        ),
        require_single_segment_notice=_to_bool(
            raw.get("require_single_segment_notice", False),
            field=f"company_rules.{company}.require_single_segment_notice",
        ),
    )


def parse_matrix_profile(name: str, raw: Any) -> MatrixProfile:
    if not isinstance(raw, dict):
        raise ValueError(f"profiles.{name} must be a mapping")

    companies = parse_csv_or_list(raw.get("companies"), field=f"profiles.{name}.companies")
    report_codes = parse_csv_or_list(raw.get("report_codes"), field=f"profiles.{name}.report_codes")
    if not companies:
        raise ValueError(f"profiles.{name}.companies must not be empty")
    if not report_codes:
        raise ValueError(f"profiles.{name}.report_codes must not be empty")

    years = normalize_years_value(raw.get("years"), field=f"profiles.{name}.years")
    max_retries = _to_int(raw.get("max_retries", 3), field=f"profiles.{name}.max_retries", minimum=1)
    retry_delay_seconds = _to_float(
        raw.get("retry_delay_seconds", 2.0),
        field=f"profiles.{name}.retry_delay_seconds",
        minimum=0.0,
    )
    min_success_per_report_code = _to_int(
        raw.get("min_success_per_report_code", 0),
        field=f"profiles.{name}.min_success_per_report_code",
        minimum=0,
    )

    return MatrixProfile(
        companies=companies,
        years=years,
        report_codes=report_codes,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
        min_success_per_report_code=min_success_per_report_code,
    )


def load_matrix_config(path: Path) -> tuple[dict[str, CompanyRule], dict[str, MatrixProfile], str]:
    if not path.exists():
        raise FileNotFoundError(f"matrix config not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("matrix config root must be a mapping")

    company_rules: dict[str, CompanyRule] = dict(BUILTIN_COMPANY_RULES)
    raw_rules = raw.get("company_rules", {})
    if raw_rules is not None:
        if not isinstance(raw_rules, dict):
            raise ValueError("company_rules must be a mapping")
        for company, rule_raw in raw_rules.items():
            company_name = str(company).strip()
            if not company_name:
                continue
            company_rules[company_name] = parse_company_rule(company_name, rule_raw)

    raw_profiles = raw.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ValueError("profiles must be a non-empty mapping")

    profiles: dict[str, MatrixProfile] = {}
    for name, profile_raw in raw_profiles.items():
        profile_name = str(name).strip()
        if not profile_name:
            continue
        profiles[profile_name] = parse_matrix_profile(profile_name, profile_raw)

    if not profiles:
        raise ValueError("no valid profiles in matrix config")

    default_matrix = str(raw.get("default_matrix") or "").strip()
    if not default_matrix:
        default_matrix = "base" if "base" in profiles else next(iter(profiles))
    if default_matrix not in profiles:
        raise ValueError(f"default_matrix not found in profiles: {default_matrix}")

    return company_rules, profiles, default_matrix


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


def _to_int_safe(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_summary(
    company: str,
    report_code: str,
    expected_years: list[str],
    payload: dict[str, Any],
    company_rules: dict[str, CompanyRule],
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
    if _to_int_safe(track_a.get("bs_rows")) <= 0:
        errors.append(f"track_a.bs_rows={track_a.get('bs_rows')} (expected > 0)")
    if _to_int_safe(track_a.get("cf_rows")) <= 0:
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
        if _to_int_safe(metrics.get("runtime_ms")) <= 0:
            errors.append(f"metrics.runtime_ms={metrics.get('runtime_ms')} (expected > 0)")
        warning_types = metrics.get("warning_types")
        if not isinstance(warning_types, dict):
            errors.append("metrics.warning_types is missing")
        normalizer = metrics.get("normalizer")
        if not isinstance(normalizer, dict):
            errors.append("metrics.normalizer is missing")

    track_b = payload.get("track_b_fallback", {})
    segment_mode = str(track_b.get("mode", ""))
    segment_rows = _to_int_safe(track_b.get("segment_rows"))
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

    rule = company_rules.get(company)
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
    company_rules: dict[str, CompanyRule],
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

        errors = validate_summary(company, report_code, expected_years, payload, company_rules)
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


def list_matrices(profiles: dict[str, MatrixProfile], default_matrix: str) -> None:
    print(f"[online-step8] matrices default={default_matrix}")
    for name in sorted(profiles):
        profile = profiles[name]
        print(
            "[online-step8] matrix="
            f"{name} companies={','.join(profile.companies)} years={profile.years} "
            f"report_codes={','.join(profile.report_codes)} "
            f"max_retries={profile.max_retries} "
            f"min_success_per_report_code={profile.min_success_per_report_code}"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    matrix_config_path = Path(str(args.matrix_config).strip())
    try:
        company_rules, profiles, default_matrix = load_matrix_config(matrix_config_path)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[online-step8] matrix_config_error path={matrix_config_path} detail={exc}")
        return 2

    if args.list_matrices:
        list_matrices(profiles, default_matrix)
        return 0

    matrix_name = str(args.matrix or default_matrix).strip()
    profile = profiles.get(matrix_name)
    if profile is None:
        print(
            f"[online-step8] unknown matrix: {matrix_name} "
            f"(available: {','.join(sorted(profiles))})"
        )
        return 2

    try:
        companies = (
            parse_csv_or_list(args.companies, field="--companies")
            if args.companies is not None
            else list(profile.companies)
        )
        years = (
            normalize_years_value(args.years, field="--years")
            if args.years is not None
            else str(profile.years)
        )
        report_codes = (
            parse_csv_or_list(args.report_codes, field="--report-codes")
            if args.report_codes is not None
            else list(profile.report_codes)
        )
        max_retries = (
            _to_int(args.max_retries, field="--max-retries", minimum=1)
            if args.max_retries is not None
            else int(profile.max_retries)
        )
        retry_delay_seconds = (
            _to_float(args.retry_delay_seconds, field="--retry-delay-seconds", minimum=0.0)
            if args.retry_delay_seconds is not None
            else float(profile.retry_delay_seconds)
        )
        min_success_per_report_code = (
            _to_int(args.min_success_per_report_code, field="--min-success-per-report-code", minimum=0)
            if args.min_success_per_report_code is not None
            else int(profile.min_success_per_report_code)
        )
    except ValueError as exc:
        print(f"[online-step8] invalid_argument detail={exc}")
        return 2

    expected_years = parse_years_csv(years)
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
        f"[online-step8] start matrix={matrix_name} matrix_config={matrix_config_path} "
        f"companies={','.join(companies)} years={years} "
        f"report_codes={','.join(report_codes)} max_retries={max_retries} "
        f"min_success_per_report_code={min_success_per_report_code}"
    )

    pass_by_code: dict[str, list[str]] = {code: [] for code in report_codes}
    fail_by_code: dict[str, list[tuple[str, str]]] = {code: [] for code in report_codes}

    for report_code in report_codes:
        for company in companies:
            ok, detail = run_company_with_retries(
                company=company,
                years=years,
                report_code=report_code,
                expected_years=expected_years,
                output_dir=output_dir,
                max_retries=max_retries,
                retry_delay_seconds=retry_delay_seconds,
                company_rules=company_rules,
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

    min_success = max(0, int(min_success_per_report_code))
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
