#!/usr/bin/env python3
"""
DART pipeline CLI entrypoint (Step 0 scaffold).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd


class ConfigError(RuntimeError):
    pass


DART_API_ENV_KEY = "DART_API_KEY"


REQUIRED_SETTINGS_SCHEMA: dict[str, tuple[str, ...]] = {
    "dart": ("api_key", "corp_code_db_path"),
    "paths": ("raw_dir", "parsed_dir", "cache_db_path", "template_path"),
    "llm": ("provider", "gateway_url", "default_model"),
    "pipeline": ("fs_div", "years"),
}

REQUIRED_TAXONOMY_SECTIONS: tuple[str, ...] = (
    "sga_detail",
    "segment_revenue",
    "revenue_detail",
)
STEP8_SUMMARY_SCHEMA_VERSION = "1.1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DART financial pipeline scaffold CLI.",
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to settings yaml (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--taxonomy",
        default="config/taxonomy.yaml",
        help="Path to taxonomy yaml (default: config/taxonomy.yaml)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate settings/taxonomy files and exit.",
    )
    parser.add_argument(
        "--check-network",
        action="store_true",
        help="Check DART DNS/HTTPS/API-key connectivity and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose diagnostics (used with --check-network).",
    )
    parser.add_argument(
        "--step1-init-corp-db",
        action="store_true",
        help="Build/refresh corp_code DB from DART corpCode.xml.",
    )
    parser.add_argument(
        "--company-name",
        default=None,
        help="Company name for corp_code lookup (e.g., 삼성전자).",
    )
    parser.add_argument(
        "--stock-code",
        default=None,
        help="Stock code for corp_code lookup (e.g., 005930).",
    )
    parser.add_argument(
        "--corp-code",
        default=None,
        help="Direct corp_code input (8 digits).",
    )
    parser.add_argument(
        "--year",
        default=None,
        help="Business year for report lookup (e.g., 2024).",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="Comma-separated years for multi-year fetch (e.g., 2022,2023,2024).",
    )
    parser.add_argument(
        "--report-code",
        default="11011",
        help="Report code: 11011/11012/11013/11014 (default: 11011).",
    )
    parser.add_argument(
        "--fs-div",
        default=None,
        help="Financial statement scope: CFS or OFS (default: settings.pipeline.fs_div).",
    )
    parser.add_argument(
        "--step1-latest-report",
        action="store_true",
        help="Lookup latest report by corp_code/company_name + year.",
    )
    parser.add_argument(
        "--step2-fetch-statements",
        action="store_true",
        help="Fetch Track A financial statements and print summary.",
    )
    parser.add_argument(
        "--step3-parse-xbrl",
        action="store_true",
        help="Parse Track C XBRL note structure and print summary.",
    )
    parser.add_argument(
        "--xbrl-dir",
        default=None,
        help="Local XBRL extracted directory path (contains *_pre.xml).",
    )
    parser.add_argument(
        "--step3-include-separate",
        action="store_true",
        help="Include separate financial statement roles (*...5).",
    )
    parser.add_argument(
        "--step3-include-unknown-roles",
        action="store_true",
        help="Include roles not listed in built-in role map.",
    )
    parser.add_argument(
        "--step4-validate-caldef",
        action="store_true",
        help="Parse cal/def linkbase and run Step4 summary/validation.",
    )
    parser.add_argument(
        "--cal-file",
        default=None,
        help="Path to *_cal.xml file. If omitted, searched under --xbrl-dir.",
    )
    parser.add_argument(
        "--def-file",
        default=None,
        help="Path to *_def.xml file. If omitted, searched under --xbrl-dir.",
    )
    parser.add_argument(
        "--step4-role-code",
        default="D210000",
        help="Role code for Step4 outputs (default: D210000).",
    )
    parser.add_argument(
        "--step4-values-json",
        default=None,
        help="JSON path containing {account_id: value} for cal validation.",
    )
    parser.add_argument(
        "--step4-tolerance",
        type=float,
        default=1.0,
        help="Absolute tolerance for cal validation (default: 1.0).",
    )
    parser.add_argument(
        "--step4-require-all-children",
        action="store_true",
        help="Skip parent checks unless all child values are present.",
    )
    parser.add_argument(
        "--step8-run-pipeline",
        action="store_true",
        help="Run end-to-end pipeline (Track A + Track C + Excel output).",
    )
    parser.add_argument(
        "--output-path",
        default=None,
        help="Output excel path for Step8 pipeline.",
    )
    parser.add_argument(
        "--step8-summary-path",
        default=None,
        help="Output json summary path for Step8 (default: <output>_summary.json).",
    )
    parser.add_argument(
        "--step8-skip-trackc",
        action="store_true",
        help="Skip Track C(XBRL) in Step8 and run Track A + Excel only.",
    )
    parser.add_argument(
        "--step8-strict-trackc",
        action="store_true",
        help="Fail Step8 if Track C cannot be parsed (missing XBRL dir or parse error).",
    )
    parser.add_argument(
        "--step8-strict-template",
        action="store_true",
        help="Fail if template file does not exist (default: auto-create workbook).",
    )
    parser.add_argument(
        "--step8-normalizer-cache-policy",
        choices=("read_write", "read_only", "bypass"),
        default="read_write",
        help=(
            "Cache policy for Step6 normalizer: "
            "read_write(default), read_only(cache-hit only), bypass(no read/no write)."
        ),
    )
    parser.add_argument(
        "--step8-enable-llm-normalize",
        action="store_true",
        help="Enable optional LLM mapping for unmapped Step6 accounts.",
    )
    parser.add_argument(
        "--step8-llm-gateway-url",
        default=None,
        help="Override LLM gateway URL (default: settings.llm.gateway_url).",
    )
    parser.add_argument(
        "--step8-llm-model",
        default=None,
        help="Override LLM model id (default: settings.llm.default_model).",
    )
    parser.add_argument(
        "--step8-llm-timeout-seconds",
        type=float,
        default=20.0,
        help="LLM gateway timeout seconds (default: 20).",
    )
    parser.add_argument(
        "--step8-llm-max-calls",
        type=int,
        default=20,
        help="Max LLM calls per Step8 run (default: 20).",
    )
    parser.add_argument(
        "--step8-llm-min-unmapped",
        type=int,
        default=2,
        help="Call LLM only when unmapped count is at least this value (default: 2).",
    )
    parser.add_argument(
        "--step8-llm-max-unmapped",
        type=int,
        default=12,
        help="Max unmapped accounts sent to one LLM call (default: 12).",
    )
    return parser.parse_args(argv)


def load_dotenv_file(path: Path) -> None:
    """
    Minimal .env loader.

    - Existing environment variables are preserved.
    - Supports KEY=VALUE and quoted values.
    """
    if not path.exists():
        return

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f".env 파일을 읽을 수 없습니다: {path}") from exc

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ('"', "'")
        ):
            value = value[1:-1]

        if key not in os.environ:
            os.environ[key] = value


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path}")

    try:
        import yaml  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise ConfigError(
            "PyYAML이 설치되지 않았습니다. `pip install -r requirements.txt`를 먼저 실행하세요."
        ) from exc

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다: {path}") from exc

    try:
        loaded = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 파싱 실패 ({path}): {exc}") from exc

    if not isinstance(loaded, dict):
        raise ConfigError(f"YAML 최상위 구조는 dict여야 합니다: {path}")

    return loaded


def validate_settings(settings: dict[str, Any], path: Path) -> None:
    for section, required_keys in REQUIRED_SETTINGS_SCHEMA.items():
        section_data = settings.get(section)
        if not isinstance(section_data, dict):
            raise ConfigError(
                f"{path}: '{section}' 섹션이 없거나 dict 형식이 아닙니다."
            )

        for key in required_keys:
            if key not in section_data:
                raise ConfigError(
                    f"{path}: '{section}.{key}' 키가 누락되었습니다."
                )

    fs_div = settings["pipeline"]["fs_div"]
    if fs_div not in ("CFS", "OFS"):
        raise ConfigError(f"{path}: 'pipeline.fs_div'는 CFS 또는 OFS여야 합니다.")

    years = settings["pipeline"]["years"]
    if not isinstance(years, list) or not years:
        raise ConfigError(f"{path}: 'pipeline.years'는 1개 이상 문자열 리스트여야 합니다.")
    if not all(isinstance(y, str) and len(y) == 4 and y.isdigit() for y in years):
        raise ConfigError(f"{path}: 'pipeline.years'의 각 값은 '2024' 형태 문자열이어야 합니다.")


def validate_taxonomy(taxonomy: dict[str, Any], path: Path) -> None:
    for section in REQUIRED_TAXONOMY_SECTIONS:
        section_data = taxonomy.get(section)
        if not isinstance(section_data, dict):
            raise ConfigError(
                f"{path}: '{section}' 섹션이 없거나 dict 형식이 아닙니다."
            )

        standard_accounts = section_data.get("standard_accounts")
        if not isinstance(standard_accounts, list):
            raise ConfigError(
                f"{path}: '{section}.standard_accounts'는 list 형식이어야 합니다."
            )

        aliases = section_data.get("aliases", {})
        if not isinstance(aliases, dict):
            raise ConfigError(
                f"{path}: '{section}.aliases'는 dict 형식이어야 합니다."
            )


def run_config_check(config_path: Path, taxonomy_path: Path) -> int:
    settings = load_yaml(config_path)
    taxonomy = load_yaml(taxonomy_path)

    validate_settings(settings, config_path)
    validate_taxonomy(taxonomy, taxonomy_path)

    print("설정 검증 완료")
    print(f"- settings: {config_path}")
    print(f"- taxonomy: {taxonomy_path}")
    return 0


def run_network_check_actions(settings: dict[str, Any], args: argparse.Namespace) -> int:
    if not args.check_network:
        return -1

    import socket  # pylint: disable=import-outside-toplevel
    import requests  # pylint: disable=import-outside-toplevel

    from src.dart_api import DartAPI  # pylint: disable=import-outside-toplevel

    host = "opendart.fss.or.kr"
    endpoint = f"{DartAPI.BASE_URL}/list.json"
    timeout = (5, 15)
    verbose = bool(args.verbose)
    overall_ok = True
    corp_db_path = str(settings["dart"]["corp_code_db_path"])

    probe_client = DartAPI(api_key="NETWORK_CHECK_DUMMY_KEY", corp_db_path=corp_db_path)
    session = probe_client.session

    def _extract_retry_count(response: Any) -> int:
        retries = getattr(getattr(response, "raw", None), "retries", None)
        history = getattr(retries, "history", None)
        if history is None:
            return 0
        return len(history)

    def _elapsed_ms(started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)

    def _append_probe_metrics(message: str, elapsed_ms: int, retries: int) -> str:
        if not verbose:
            return message
        return f"{message} elapsed_ms={elapsed_ms} retries={retries}"

    def _format_error_line(prefix: str, exc: Exception, elapsed_ms: int) -> str:
        error_code = "unknown"
        error_message = exc.__class__.__name__
        if isinstance(exc, requests.RequestException):
            error_code, error_message = DartAPI._classify_request_error(exc)

        line = f"{prefix} error={error_message}"
        if verbose:
            line = f"{line} error_code={error_code} elapsed_ms={elapsed_ms} retries=-"
        return line

    if verbose:
        adapter = session.get_adapter(endpoint)
        retry_cfg = getattr(adapter, "max_retries", None)
        if retry_cfg is not None:
            print(
                "[Network][verbose] retry_policy "
                f"total={retry_cfg.total} connect={retry_cfg.connect} "
                f"read={retry_cfg.read} status={retry_cfg.status} "
                f"backoff={retry_cfg.backoff_factor}"
            )
        print(f"[Network][verbose] timeout connect={timeout[0]}s read={timeout[1]}s")

    dns_started_at = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        dns_elapsed_ms = _elapsed_ms(dns_started_at)
        resolved_ips = sorted(
            {
                info[4][0]
                for info in infos
                if isinstance(info, tuple) and len(info) >= 5 and info[4]
            }
        )
        if not resolved_ips:
            raise OSError("IP 결과 없음")
        message = f"[Network] DNS: OK host={host} ip={','.join(resolved_ips)}"
        if verbose:
            message = f"{message} elapsed_ms={dns_elapsed_ms}"
        print(message)
    except OSError as exc:
        dns_elapsed_ms = _elapsed_ms(dns_started_at)
        overall_ok = False
        message = f"[Network] DNS: FAIL host={host} error={exc}"
        if verbose:
            message = f"{message} elapsed_ms={dns_elapsed_ms}"
        print(message)

    https_started_at = time.perf_counter()
    try:
        response = session.get(endpoint, timeout=timeout)
        https_elapsed_ms = _elapsed_ms(https_started_at)
        https_retries = _extract_retry_count(response)
        payload: dict[str, Any]
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        dart_status = str(payload.get("status", "")).strip()
        message = str(payload.get("message") or payload.get("msg") or "").strip()

        if response.status_code != 200:
            overall_ok = False
            print(
                _append_probe_metrics(
                    f"[Network] HTTPS: FAIL status_code={response.status_code}",
                    https_elapsed_ms,
                    https_retries,
                )
            )
        else:
            if message:
                print(
                    _append_probe_metrics(
                        f"[Network] HTTPS: OK status_code={response.status_code} "
                        f"dart_status={dart_status or '-'} message={message}",
                        https_elapsed_ms,
                        https_retries,
                    )
                )
            else:
                print(
                    _append_probe_metrics(
                        f"[Network] HTTPS: OK status_code={response.status_code} "
                        f"dart_status={dart_status or '-'}",
                        https_elapsed_ms,
                        https_retries,
                    )
                )
    except requests.RequestException as exc:
        https_elapsed_ms = _elapsed_ms(https_started_at)
        overall_ok = False
        print(_format_error_line("[Network] HTTPS: FAIL", exc, https_elapsed_ms))

    try:
        api_key = resolve_dart_api_key(settings)
    except ConfigError as exc:
        print(f"[Network] API key: SKIP reason={exc}")
    else:
        api_started_at = time.perf_counter()
        try:
            response = session.get(
                endpoint,
                params={
                    "crtfc_key": api_key,
                    "corp_code": "00126380",
                    "bgn_de": "20240101",
                    "end_de": "20241231",
                    "pblntf_ty": "A",
                    "page_count": 1,
                },
                timeout=timeout,
            )
            api_elapsed_ms = _elapsed_ms(api_started_at)
            api_retries = _extract_retry_count(response)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError:
                overall_ok = False
                print(
                    _append_probe_metrics(
                        "[Network] API key: FAIL 응답 JSON 파싱 실패",
                        api_elapsed_ms,
                        api_retries,
                    )
                )
            else:
                status = str(payload.get("status", "")).strip()
                message = str(payload.get("message") or payload.get("msg") or "").strip()

                if status in {"010", "011", "012"}:
                    overall_ok = False
                    print(
                        _append_probe_metrics(
                            f"[Network] API key: FAIL status={status} message={message or '-'}",
                            api_elapsed_ms,
                            api_retries,
                        )
                    )
                else:
                    print(
                        _append_probe_metrics(
                            f"[Network] API key: OK status={status or '-'} message={message or '-'}",
                            api_elapsed_ms,
                            api_retries,
                        )
                    )
        except requests.RequestException as exc:
            api_elapsed_ms = _elapsed_ms(api_started_at)
            overall_ok = False
            print(_format_error_line("[Network] API key: FAIL", exc, api_elapsed_ms))

    print(f"[Network] result={'OK' if overall_ok else 'FAIL'}")
    return 0 if overall_ok else 1


def resolve_dart_api_key(settings: dict[str, Any]) -> str:
    configured = str(settings["dart"].get("api_key", "")).strip()
    if configured and configured != "YOUR_DART_API_KEY":
        return configured

    env_api_key = os.getenv(DART_API_ENV_KEY, "").strip()
    if env_api_key:
        return env_api_key

    raise ConfigError(
        "DART API 키가 없습니다. "
        "`config/settings.yaml`의 `dart.api_key`를 설정하거나 "
        "`.env`에 `DART_API_KEY=...`를 추가하세요."
    )


def parse_years_option(args: argparse.Namespace, settings: dict[str, Any]) -> list[str]:
    if args.years:
        years = [part.strip() for part in str(args.years).split(",") if part.strip()]
    elif args.year:
        years = [str(args.year).strip()]
    else:
        years = [str(y).strip() for y in settings["pipeline"]["years"]]

    if not years:
        raise ConfigError("연도 정보가 비어 있습니다. --year 또는 --years를 지정하세요.")
    for year in years:
        if len(year) != 4 or not year.isdigit():
            raise ConfigError(f"잘못된 year 값: {year}")
    return years


def resolve_step4_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """
    Resolve xbrl_dir/cal_file/def_file for Step4.
    """
    cal_file = Path(str(args.cal_file)) if args.cal_file else None
    def_file = Path(str(args.def_file)) if args.def_file else None
    xbrl_dir = Path(str(args.xbrl_dir)) if args.xbrl_dir else None

    if cal_file and (not cal_file.exists() or not cal_file.is_file()):
        raise ConfigError(f"유효한 cal.xml 파일이 아닙니다: {cal_file}")
    if def_file and (not def_file.exists() or not def_file.is_file()):
        raise ConfigError(f"유효한 def.xml 파일이 아닙니다: {def_file}")

    if xbrl_dir is None:
        if cal_file is not None:
            xbrl_dir = cal_file.parent
        elif def_file is not None:
            xbrl_dir = def_file.parent

    if xbrl_dir is None:
        raise ConfigError(
            "Step4 실행에는 --xbrl-dir 또는 --cal-file/--def-file 중 하나가 필요합니다."
        )
    if not xbrl_dir.exists() or not xbrl_dir.is_dir():
        raise ConfigError(f"유효한 XBRL 디렉토리가 아닙니다: {xbrl_dir}")

    if cal_file is None:
        candidates = sorted(xbrl_dir.glob("*_cal.xml"))
        if not candidates:
            raise ConfigError(f"cal.xml 파일을 찾을 수 없습니다: {xbrl_dir}")
        cal_file = candidates[0]

    if def_file is None:
        candidates = sorted(xbrl_dir.glob("*_def.xml"))
        if not candidates:
            raise ConfigError(f"def.xml 파일을 찾을 수 없습니다: {xbrl_dir}")
        def_file = candidates[0]

    return xbrl_dir, cal_file, def_file


def resolve_corp_code_for_args(dart: Any, args: argparse.Namespace) -> str | None:
    corp_code = str(args.corp_code).strip() if args.corp_code else None
    if corp_code:
        return corp_code
    if args.company_name:
        return dart.get_corp_code(args.company_name)
    if args.stock_code:
        return dart.get_corp_code_by_stock_code(args.stock_code)
    return None


def to_safe_filename_token(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return "output"
    token = re.sub(r"\s+", "_", token)
    token = re.sub(r"[^\w\-가-힣]", "_", token)
    token = re.sub(r"_+", "_", token).strip("_")
    return token or "output"


def build_step8_summary_payload(
    *,
    corp_code: str,
    company_name: str,
    fs_div: str,
    years: list[str],
    report_code: str,
    bs_rows: int,
    is_rows: int,
    cf_rows: int,
    trackc_mode: str,
    trackc_source: str,
    xbrl_dir: Path | None,
    xbrl_note_count: int,
    sga_accounts_count: int,
    segment_members_count: int,
    segment_mode: str,
    latest_rcept_no: str,
    fallback_doc_count: int,
    fallback_tables: int,
    fallback_sga_rows: int,
    fallback_segment_rows: int,
    single_segment_sources: list[str] | set[str],
    statement_summaries: list[Any],
    infos: list[str],
    warnings: list[str],
    output_excel: Path,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": STEP8_SUMMARY_SCHEMA_VERSION,
        "corp_code": corp_code,
        "company_name": company_name,
        "fs_div": fs_div,
        "years": sorted(str(year) for year in years),
        "report_code": str(report_code),
        "track_a": {
            "bs_rows": int(bs_rows),
            "is_rows": int(is_rows),
            "cf_rows": int(cf_rows),
        },
        "track_c": {
            "mode": trackc_mode,
            "source": trackc_source,
            "xbrl_dir": str(xbrl_dir) if xbrl_dir is not None else None,
            "notes": int(xbrl_note_count),
            "sga_accounts": int(sga_accounts_count),
            "segment_members": int(segment_members_count),
        },
        "track_b_fallback": {
            "mode": segment_mode,
            "rcept_no": latest_rcept_no or None,
            "docs": int(fallback_doc_count),
            "tables": int(fallback_tables),
            "sga_rows": int(fallback_sga_rows),
            "segment_rows": int(fallback_segment_rows),
            "single_segment_sources": sorted(str(x) for x in single_segment_sources),
        },
        "writes": [
            {
                "sheet_name": str(summary.sheet_name),
                "written_cells": int(summary.written_cells),
                "matched_accounts": int(summary.matched_accounts),
                "unmatched_accounts": len(getattr(summary, "unmatched_accounts", [])),
            }
            for summary in statement_summaries
        ],
        "infos": list(infos),
        "warnings": list(warnings),
        "output_excel": str(output_excel),
    }
    if isinstance(metrics, dict):
        payload["metrics"] = metrics
    return payload


def classify_step8_warning_type(message: str) -> str:
    text = str(message or "")
    if "Track A 수집 실패" in text:
        return "track_a_fetch"
    if "최신 공시 조회/원문 다운로드 실패" in text:
        return "report_fetch"
    if "Track C 파싱 실패" in text:
        return "track_c_parse"
    if "Track B 파싱 실패" in text:
        return "track_b_parse"
    if "Step6 정규화/캐시 초기화 실패" in text:
        return "normalizer_init"
    if "taxonomy 파일이 없어 Step6 정규화를 건너뜀" in text:
        return "taxonomy_missing"
    if "시계열 데이터가 비어 있어" in text:
        return "empty_timeseries"
    if "Step8 요약 JSON 저장 실패" in text:
        return "summary_save"
    return "other"


def summarize_step8_warning_types(messages: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        key = classify_step8_warning_type(message)
        counts[key] = int(counts.get(key, 0)) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def extract_step8_year_columns(df: Any, latest_report_year_hint: int | None = None) -> dict[Any, str]:
    year_map: dict[Any, str] = {}
    if not isinstance(df, pd.DataFrame) or df.empty:
        return year_map

    for col in df.columns[1:]:
        col_s = str(col)
        match = re.search(r"(20\d{2})", col_s)
        if match:
            year_map[col] = match.group(1)
            continue
        if col_s.isdigit() and len(col_s) == 4:
            year_map[col] = col_s
    if year_map:
        return year_map

    if latest_report_year_hint is None:
        return year_map

    symbolic_offsets = (
        ("전전기말", 2),
        ("전전반기", 2),
        ("전전분기", 2),
        ("전전기", 2),
        ("전기말", 1),
        ("전반기", 1),
        ("전분기", 1),
        ("전기", 1),
        ("당기말", 0),
        ("당반기", 0),
        ("당분기", 0),
        ("당기", 0),
    )
    for col in df.columns[1:]:
        col_s = str(col).replace(" ", "")
        for token, offset in symbolic_offsets:
            if token in col_s:
                year_map[col] = str(latest_report_year_hint - offset)
                break
    if year_map:
        return year_map

    # 제55기/제54기 같은 회기 표현을 최신 공시 연도 기준으로 보정한다.
    term_map: dict[Any, int] = {}
    for col in df.columns[1:]:
        match = re.search(r"제\s*(\d+)\s*기", str(col))
        if match:
            term_map[col] = int(match.group(1))
    if term_map:
        max_term = max(term_map.values())
        for col, term in term_map.items():
            year_map[col] = str(latest_report_year_hint - (max_term - term))
    if year_map:
        return year_map

    # 멀티헤더가 본문 행으로 내려온 경우(열명이 0,1,2...) 첫 행들에서 연도를 추정한다.
    header_scan_rows = min(3, len(df))
    seen_years: set[str] = set()
    for col in df.columns[1:]:
        hint_cells = [str(df.iloc[row_idx].get(col, "")).strip() for row_idx in range(header_scan_rows)]
        hint_text = " ".join(hint_cells)
        if "비중" in hint_text:
            continue

        detected_year: str | None = None
        for cell in hint_cells:
            match = re.search(r"(20\d{2})", cell)
            if match:
                detected_year = match.group(1)
                break
        if detected_year is None or detected_year in seen_years:
            continue
        seen_years.add(detected_year)
        year_map[col] = detected_year
    return year_map


def to_step8_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in {"", "-", "nan", "NaN", "None"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    cleaned = (
        text.replace(",", "")
        .replace(" ", "")
        .replace("\u00A0", "")
        .replace("원", "")
        .replace("백만원", "")
        .replace("천원", "")
    )
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def is_step8_segment_revenue_table(
    note_title: str,
    df: Any,
    latest_report_year_hint: int | None = None,
) -> bool:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return False
    if len(df.columns) < 3:
        return False

    title_l = str(note_title or "").lower()
    if not any(token in title_l for token in ("매출", "수익", "revenue", "sales")):
        return False

    first_col = df.columns[0]
    first_labels = [str(x).strip() for x in df[first_col].tolist() if str(x).strip()]
    has_segment_hint_in_rows = any(
        ("부문" in label) or ("segment" in label.lower()) or ("division" in label.lower())
        for label in first_labels[:30]
    )
    has_segment_hint_in_title = any(token in title_l for token in ("부문", "segment", "division"))
    if not (has_segment_hint_in_rows or has_segment_hint_in_title):
        return False

    year_map = extract_step8_year_columns(df, latest_report_year_hint=latest_report_year_hint)
    if len(year_map) < 2:
        return False

    valid_rows = 0
    for _, row in df.iterrows():
        key = str(row.get(first_col, "")).strip()
        if not key or key.lower() == "nan":
            continue
        has_major_numeric = False
        for col_name in year_map:
            value = to_step8_number(row.get(col_name))
            if value is not None and abs(value) >= 100_000:
                has_major_numeric = True
                break
        if has_major_numeric:
            valid_rows += 1
        if valid_rows >= 2:
            return True
    return False


def is_step8_single_segment_notice(note_title: str, df: Any) -> bool:
    text_parts = [str(note_title or "")]
    if isinstance(df, pd.DataFrame) and not df.empty:
        first_col = df.columns[0]
        row_samples = [str(x).strip() for x in df[first_col].tolist()[:8] if str(x).strip()]
        text_parts.extend(row_samples)

    normalized = " ".join(text_parts).lower().replace(" ", "")
    markers = (
        "단일사업부문",
        "지배적단일사업부문",
        "부문별기재를생략",
        "singleoperatingsegment",
        "singlereportablesegment",
    )
    return any(marker in normalized for marker in markers)


def normalize_step8_row_label(label: str) -> str:
    text = str(label or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = (
        text.replace("(", "")
        .replace(")", "")
        .replace(":", "")
        .replace("-", "")
        .replace("_", "")
        .replace(".", "")
    )
    return text


def is_step8_total_row_label(note_type: str, row_label: str) -> bool:
    del note_type  # reserved for future note-type specific rules
    normalized = normalize_step8_row_label(row_label)
    return normalized in {"합계", "총계", "소계", "계"}


def is_step8_noise_row_label(note_type: str, row_label: str) -> bool:
    raw = str(row_label or "").strip()
    if not raw:
        return True
    normalized = normalize_step8_row_label(raw)
    if not normalized or normalized == "nan":
        return True

    if raw.startswith(("※", "*")):
        return True
    if any(token in raw for token in ("단위", "비고", "주석")):
        return True
    if raw.endswith(":"):
        return True

    if note_type == "segment_revenue":
        if re.fullmatch(r"[A-Z]{3,4}", raw):
            return True
        if "가격 변동" in raw or "가격변동" in raw:
            return True
    return False


def should_skip_step8_row(note_type: str, row_label: str) -> bool:
    return is_step8_total_row_label(note_type, row_label) or is_step8_noise_row_label(
        note_type,
        row_label,
    )


def run_step1_actions(
    settings: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    from src.corp_code_db import CorpCodeDB, CorpCodeDBError  # pylint: disable=import-outside-toplevel
    from src.dart_api import DartAPI, DartAPIError  # pylint: disable=import-outside-toplevel

    api_key = resolve_dart_api_key(settings)
    corp_db_path = str(settings["dart"]["corp_code_db_path"])

    if args.step1_init_corp_db:
        try:
            with CorpCodeDB(corp_db_path) as db:
                inserted = db.refresh(api_key)
            print(f"corp_code DB 갱신 완료: {inserted}건")
        except CorpCodeDBError as exc:
            raise ConfigError(f"corp_code DB 갱신 실패: {exc}") from exc
        return 0

    if (
        args.company_name
        and not args.step1_latest_report
        and not args.step2_fetch_statements
        and not args.step3_parse_xbrl
        and not args.step4_validate_caldef
        and not args.step8_run_pipeline
    ):
        try:
            dart = DartAPI(api_key=api_key, corp_db_path=corp_db_path)
            corp_code = dart.get_corp_code(args.company_name)
        except DartAPIError as exc:
            raise ConfigError(f"corp_code 조회 실패: {exc}") from exc
        if corp_code is None:
            print(f"조회 결과 없음: {args.company_name}")
            return 1
        print(f"{args.company_name} -> {corp_code}")
        return 0

    if (
        args.stock_code
        and not args.step1_latest_report
        and not args.step2_fetch_statements
        and not args.step3_parse_xbrl
        and not args.step4_validate_caldef
        and not args.step8_run_pipeline
    ):
        try:
            dart = DartAPI(api_key=api_key, corp_db_path=corp_db_path)
            corp_code = dart.get_corp_code_by_stock_code(args.stock_code)
        except DartAPIError as exc:
            raise ConfigError(f"stock_code 조회 실패: {exc}") from exc
        if corp_code is None:
            print(f"조회 결과 없음: {args.stock_code}")
            return 1
        print(f"{args.stock_code} -> {corp_code}")
        return 0

    if args.step1_latest_report:
        year = args.year
        if year is None:
            raise ConfigError("--step1-latest-report 사용 시 --year가 필요합니다.")

        dart = DartAPI(api_key=api_key, corp_db_path=corp_db_path)
        corp_code: str | None = None

        if args.company_name:
            corp_code = dart.get_corp_code(args.company_name)
        elif args.stock_code:
            corp_code = dart.get_corp_code_by_stock_code(args.stock_code)
        else:
            raise ConfigError(
                "--step1-latest-report 사용 시 --company-name 또는 --stock-code가 필요합니다."
            )

        if corp_code is None:
            raise ConfigError("corp_code 조회 결과가 없습니다.")

        latest = dart.get_latest_report(
            corp_code=corp_code,
            year=year,
            report_code=args.report_code,
        )
        if latest is None:
            print("조회된 공시가 없습니다.")
            return 1

        print(f"corp_code: {corp_code}")
        print(f"rcept_no: {latest.get('rcept_no', '')}")
        print(f"rcept_dt: {latest.get('rcept_dt', '')}")
        print(f"report_nm: {latest.get('report_nm', '')}")
        return 0

    return -1


def run_step2_actions(
    settings: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    if not args.step2_fetch_statements:
        return -1

    from src.dart_api import DartAPI, DartAPIError  # pylint: disable=import-outside-toplevel
    from src.financial_statements import (  # pylint: disable=import-outside-toplevel
        FinancialStatementError,
        FinancialStatementFetcher,
        StatementType,
        build_time_series,
    )

    api_key = resolve_dart_api_key(settings)
    corp_db_path = str(settings["dart"]["corp_code_db_path"])
    fs_div = str(args.fs_div or settings["pipeline"]["fs_div"]).strip().upper()
    years = parse_years_option(args, settings)

    try:
        dart = DartAPI(api_key=api_key, corp_db_path=corp_db_path)
    except DartAPIError as exc:
        raise ConfigError(f"DART 클라이언트 초기화 실패: {exc}") from exc

    corp_code = str(args.corp_code).strip() if args.corp_code else None
    if corp_code is None:
        try:
            if args.company_name:
                corp_code = dart.get_corp_code(args.company_name)
            elif args.stock_code:
                corp_code = dart.get_corp_code_by_stock_code(args.stock_code)
        except DartAPIError as exc:
            raise ConfigError(f"corp_code 조회 실패: {exc}") from exc

    if corp_code is None:
        raise ConfigError(
            "--step2-fetch-statements 사용 시 --corp-code 또는 --company-name/--stock-code가 필요합니다."
        )

    fetcher = FinancialStatementFetcher(api_key)

    try:
        multi_year = fetcher.fetch_multi_year(
            corp_code=corp_code,
            years=years,
            report_code=str(args.report_code),
            fs_div=fs_div,
        )
    except FinancialStatementError as exc:
        raise ConfigError(f"Step2 수집 실패: {exc}") from exc

    print(f"[Step2] corp_code={corp_code}, fs_div={fs_div}, years={','.join(sorted(multi_year.keys()))}")

    for year in sorted(multi_year.keys()):
        statements = multi_year[year]
        summary = ", ".join(
            f"{statement_type.value}:{len(data.df)}"
            for statement_type, data in sorted(statements.items(), key=lambda item: item[0].value)
        )
        print(f"- {year}: {summary}")

    for target in (StatementType.BS, StatementType.IS, StatementType.CF):
        ts_df = build_time_series(multi_year, target)
        if ts_df.empty:
            print(f"- timeseries {target.value}: empty")
            continue
        print(f"- timeseries {target.value}: rows={len(ts_df)}, cols={len(ts_df.columns)}")

    return 0


def run_step3_actions(
    settings: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    if not args.step3_parse_xbrl:
        return -1

    from src.dart_api import DartAPI, DartAPIError  # pylint: disable=import-outside-toplevel
    from src.xbrl_parser import XBRLNoteParser, XBRLParserError  # pylint: disable=import-outside-toplevel

    xbrl_dir: Path | None = None

    if args.xbrl_dir:
        xbrl_dir = Path(str(args.xbrl_dir))
    else:
        api_key = resolve_dart_api_key(settings)
        corp_db_path = str(settings["dart"]["corp_code_db_path"])
        raw_dir = Path(str(settings["paths"]["raw_dir"]))

        try:
            dart = DartAPI(api_key=api_key, corp_db_path=corp_db_path)
        except DartAPIError as exc:
            raise ConfigError(f"DART 클라이언트 초기화 실패: {exc}") from exc

        corp_code = str(args.corp_code).strip() if args.corp_code else None
        if corp_code is None:
            try:
                if args.company_name:
                    corp_code = dart.get_corp_code(args.company_name)
                elif args.stock_code:
                    corp_code = dart.get_corp_code_by_stock_code(args.stock_code)
            except DartAPIError as exc:
                raise ConfigError(f"corp_code 조회 실패: {exc}") from exc

        if corp_code is None:
            raise ConfigError(
                "--step3-parse-xbrl 사용 시 --xbrl-dir 또는 --corp-code/--company-name/--stock-code가 필요합니다."
            )

        if args.year:
            year = str(args.year).strip()
        else:
            year = max(parse_years_option(args, settings))

        try:
            latest = dart.get_latest_report(
                corp_code=corp_code,
                year=year,
                report_code=str(args.report_code),
            )
        except DartAPIError as exc:
            raise ConfigError(f"최신 공시 조회 실패: {exc}") from exc

        if latest is None:
            raise ConfigError("Step3 대상 공시를 찾지 못했습니다.")

        rcept_no = str(latest.get("rcept_no", "")).strip()
        if not rcept_no:
            raise ConfigError("공시 데이터에 rcept_no가 없습니다.")

        save_key = args.company_name or corp_code
        save_dir = raw_dir / save_key

        try:
            downloaded_files = dart.download_document(rcept_no, str(save_dir))
        except DartAPIError as exc:
            raise ConfigError(f"원문 다운로드 실패: {exc}") from exc

        xbrl_dir = XBRLNoteParser.detect_xbrl_dir_from_files(downloaded_files)
        if xbrl_dir is None:
            raise ConfigError(f"다운로드 결과에서 XBRL 디렉토리를 찾지 못했습니다: rcept_no={rcept_no}")

        print(f"[Step3] downloaded rcept_no={rcept_no}")
        print(f"[Step3] xbrl_dir={xbrl_dir}")

    try:
        parser = XBRLNoteParser(
            xbrl_dir=str(xbrl_dir),
            include_separate=bool(args.step3_include_separate),
            include_unknown_roles=bool(args.step3_include_unknown_roles),
        )
        notes = parser.parse()
    except XBRLParserError as exc:
        raise ConfigError(f"XBRL 파싱 실패: {exc}") from exc

    print(f"[Step3] parsed notes: {len(notes)}")

    for note in notes[:40]:
        print(
            f"- {note.role_code} {note.role_name} "
            f"(accounts={len(note.accounts)}, members={len(note.members)}, fs={note.fs_type})"
        )
    if len(notes) > 40:
        print(f"- ... ({len(notes) - 40}개 role 생략)")

    sga_accounts = parser.get_sga_accounts()
    print(f"[Step3] sga_accounts: {len(sga_accounts)}")
    for account_id, label in list(sga_accounts.items())[:20]:
        print(f"  - {account_id}: {label}")
    if len(sga_accounts) > 20:
        print(f"  - ... ({len(sga_accounts) - 20}개 생략)")

    segment_members = parser.get_segment_members()
    print(f"[Step3] segment_members(company): {len(segment_members)}")
    for member in segment_members[:20]:
        print(f"  - {member.get('account_id', '')}: {member.get('label_ko', '')}")
    if len(segment_members) > 20:
        print(f"  - ... ({len(segment_members) - 20}개 생략)")

    return 0


def run_step4_actions(
    settings: dict[str, Any],  # pylint: disable=unused-argument
    args: argparse.Namespace,
) -> int:
    if not args.step4_validate_caldef:
        return -1

    from src.cal_validator import CalValidation, CalValidationError  # pylint: disable=import-outside-toplevel
    from src.xbrl_def_parser import XBRLDefParser, XBRLDefParserError  # pylint: disable=import-outside-toplevel

    role_code = str(args.step4_role_code).strip()
    if not role_code:
        raise ConfigError("Step4 role_code가 비어 있습니다.")

    xbrl_dir, cal_file, def_file = resolve_step4_paths(args)
    print(f"[Step4] xbrl_dir={xbrl_dir}")
    print(f"[Step4] cal_file={cal_file.name}")
    print(f"[Step4] def_file={def_file.name}")
    print(f"[Step4] role_code={role_code}")

    try:
        cal = CalValidation(str(cal_file))
    except CalValidationError as exc:
        raise ConfigError(f"cal.xml 파싱 실패: {exc}") from exc

    cal_roles = cal.available_roles()
    print(f"[Step4] cal roles: {len(cal_roles)}")
    relations = cal.get_relations(role_code)
    print(f"[Step4] cal relations({role_code}): {len(relations)}")

    if relations:
        parent_count = len({rel.parent for rel in relations})
        child_count = len({rel.child for rel in relations})
        print(f"[Step4] cal nodes({role_code}): parents={parent_count}, children={child_count}")

    if args.step4_values_json:
        values_path = Path(str(args.step4_values_json))
        if not values_path.exists() or not values_path.is_file():
            raise ConfigError(f"유효한 values json 파일이 아닙니다: {values_path}")
        try:
            payload = json.loads(values_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"values json 로드 실패 ({values_path}): {exc}") from exc

        if isinstance(payload, dict) and role_code in payload and isinstance(payload[role_code], dict):
            values = payload[role_code]
        elif isinstance(payload, dict):
            values = payload
        else:
            raise ConfigError("values json은 dict 형식이어야 합니다.")

        errors = cal.validate(
            role_code=role_code,
            values=values,
            tolerance=float(args.step4_tolerance),
            require_all_children=bool(args.step4_require_all_children),
        )
        print(f"[Step4] cal validation errors: {len(errors)}")
        for err in errors[:20]:
            print(
                f"  - parent={err['parent']} actual={err['actual']:.6f} "
                f"expected={err['expected']:.6f} diff={err['diff']:.6f}"
            )
        if len(errors) > 20:
            print(f"  - ... ({len(errors) - 20}개 생략)")

    try:
        def_parser = XBRLDefParser(xbrl_dir=str(xbrl_dir), def_file=str(def_file))
        def_roles = def_parser.list_roles()
        structure = def_parser.parse_table_structure(role_code)
    except XBRLDefParserError as exc:
        raise ConfigError(f"def.xml 파싱 실패: {exc}") from exc

    print(f"[Step4] def roles: {len(def_roles)}")
    print(
        f"[Step4] def structure({role_code}): "
        f"axes={len(structure['axes'])}, line_items={len(structure['line_items'])}"
    )

    for axis in structure["axes"][:20]:
        print(
            f"  - axis={axis['axis_id']} members={len(axis['members'])} "
            f"domain={axis['domain_id']}"
        )
    if len(structure["axes"]) > 20:
        print(f"  - ... ({len(structure['axes']) - 20}개 axis 생략)")

    return 0


def run_step8_actions(
    settings: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    if not args.step8_run_pipeline:
        return -1
    step8_started_at = time.perf_counter()

    from src.account_normalizer import AccountNormalizer, AccountNormalizerError  # pylint: disable=import-outside-toplevel
    from src.cache_db import CacheDB, CacheDBError  # pylint: disable=import-outside-toplevel
    from src.excel_writer import ExcelWriter, ExcelWriterError  # pylint: disable=import-outside-toplevel
    from src.dart_api import DartAPI, DartAPIError  # pylint: disable=import-outside-toplevel
    from src.document_classifier import DocumentClassifier  # pylint: disable=import-outside-toplevel
    from src.financial_statements import (  # pylint: disable=import-outside-toplevel
        FinancialStatementError,
        FinancialStatementFetcher,
        StatementType,
        build_time_series,
    )
    from src.html_parser import HTMLParser  # pylint: disable=import-outside-toplevel
    from src.xbrl_parser import XBRLNoteParser, XBRLParserError  # pylint: disable=import-outside-toplevel

    api_key = resolve_dart_api_key(settings)
    corp_db_path = str(settings["dart"]["corp_code_db_path"])
    cache_db_path = str(settings["paths"]["cache_db_path"])
    fs_div = str(args.fs_div or settings["pipeline"]["fs_div"]).strip().upper()
    years = parse_years_option(args, settings)
    latest_year = max(years)

    raw_dir = Path(str(settings["paths"]["raw_dir"]))
    raw_dir.mkdir(parents=True, exist_ok=True)
    template_path = str(settings["paths"]["template_path"])
    taxonomy_path = Path("config/taxonomy.yaml")

    warnings: list[str] = []
    infos: list[str] = []
    normalizer_cache_policy = str(args.step8_normalizer_cache_policy).strip().lower()
    llm_client: Any | None = None
    llm_usage_payload: dict[str, Any] | None = None
    normalizer_usage: Any | None = None

    if args.step8_skip_trackc and args.step8_strict_trackc:
        raise ConfigError("--step8-skip-trackc와 --step8-strict-trackc는 함께 사용할 수 없습니다.")
    if args.step8_llm_timeout_seconds <= 0:
        raise ConfigError("--step8-llm-timeout-seconds는 0보다 커야 합니다.")
    if args.step8_llm_max_calls < 0:
        raise ConfigError("--step8-llm-max-calls는 0 이상이어야 합니다.")
    if args.step8_llm_min_unmapped < 1:
        raise ConfigError("--step8-llm-min-unmapped는 1 이상이어야 합니다.")
    if args.step8_llm_max_unmapped < 0:
        raise ConfigError("--step8-llm-max-unmapped는 0 이상이어야 합니다.")
    if (
        args.step8_llm_max_unmapped > 0
        and args.step8_llm_max_unmapped < args.step8_llm_min_unmapped
    ):
        raise ConfigError(
            "--step8-llm-max-unmapped는 0이거나 --step8-llm-min-unmapped 이상이어야 합니다."
        )

    if args.step8_enable_llm_normalize:
        from src.llm_client import (  # pylint: disable=import-outside-toplevel
            BudgetedLLMClient,
            LLMClientError,
            OpenClawLLM,
        )

        llm_settings = settings.get("llm", {})
        if not isinstance(llm_settings, dict):
            llm_settings = {}
        gateway_url = str(args.step8_llm_gateway_url or llm_settings.get("gateway_url", "")).strip()
        model = str(args.step8_llm_model or llm_settings.get("default_model", "")).strip()
        try:
            llm_base = OpenClawLLM(
                gateway_url=gateway_url,
                model=model,
                timeout_seconds=float(args.step8_llm_timeout_seconds),
            )
            llm_client = BudgetedLLMClient(
                base_client=llm_base,
                enabled=True,
                max_calls=int(args.step8_llm_max_calls),
            )
            infos.append(
                "Step6 LLM 정규화 활성화: "
                f"gateway={gateway_url} model={model} max_calls={int(args.step8_llm_max_calls)}"
            )
        except LLMClientError as exc:
            warnings.append(f"Step6 LLM 정규화 초기화 실패(비활성화): {exc}")

    try:
        dart = DartAPI(api_key=api_key, corp_db_path=corp_db_path)
    except DartAPIError as exc:
        raise ConfigError(f"DART 클라이언트 초기화 실패: {exc}") from exc

    try:
        corp_code = resolve_corp_code_for_args(dart, args)
    except DartAPIError as exc:
        raise ConfigError(f"corp_code 조회 실패: {exc}") from exc

    if corp_code is None:
        raise ConfigError(
            "--step8-run-pipeline 사용 시 --corp-code 또는 --company-name/--stock-code가 필요합니다."
        )

    file_token = to_safe_filename_token(args.company_name or corp_code)

    fetcher = FinancialStatementFetcher(api_key)
    try:
        multi_year = fetcher.fetch_multi_year(
            corp_code=corp_code,
            years=years,
            report_code=str(args.report_code),
            fs_div=fs_div,
        )
    except FinancialStatementError as exc:
        raise ConfigError(f"Track A 수집 실패: {exc}") from exc

    bs_ts = build_time_series(multi_year, StatementType.BS)
    is_ts = build_time_series(multi_year, StatementType.IS)
    cf_ts = build_time_series(multi_year, StatementType.CF)

    xbrl_dir: Path | None = None
    xbrl_note_count = 0
    sga_accounts: dict[str, str] = {}
    segment_members: list[dict[str, str]] = []
    downloaded_files: list[Path] = []
    latest_rcept_no = ""
    latest_report_year_hint: int | None = None
    trackc_mode = "pending"
    trackc_source = "none"

    # 원문 다운로드: Track C/Track B fallback 공통 입력
    if args.xbrl_dir:
        xbrl_dir = Path(str(args.xbrl_dir))
        if not xbrl_dir.exists() or not xbrl_dir.is_dir():
            warnings.append(f"지정된 xbrl_dir가 유효하지 않음: {xbrl_dir}")
            xbrl_dir = None
        else:
            trackc_source = "xbrl_dir(option)"

    try:
        latest = dart.get_latest_report(
            corp_code=corp_code,
            year=latest_year,
            report_code=str(args.report_code),
        )
        if latest is None:
            warnings.append("최신 공시를 찾지 못해 Track B/Track C 원문 기반 처리를 생략.")
        else:
            latest_rcept_no = str(latest.get("rcept_no", "")).strip()
            latest_report_nm = str(latest.get("report_nm", "")).strip()
            nm_year_match = re.search(r"(20\d{2})\.\d{1,2}", latest_report_nm)
            if nm_year_match:
                latest_report_year_hint = int(nm_year_match.group(1))
            else:
                rcept_dt = str(latest.get("rcept_dt", "")).strip()
                if len(rcept_dt) >= 4 and rcept_dt[:4].isdigit():
                    latest_report_year_hint = int(rcept_dt[:4])
            if not latest_rcept_no:
                warnings.append("최신 공시에 rcept_no가 없어 Track B/Track C 원문 기반 처리를 생략.")
            else:
                download_dir = raw_dir / file_token
                downloaded_files = dart.download_document(latest_rcept_no, str(download_dir))
                if xbrl_dir is None:
                    xbrl_dir = XBRLNoteParser.detect_xbrl_dir_from_files(downloaded_files)
                    if xbrl_dir is not None:
                        trackc_source = "document.xml"

                # document.xml 패키지에 pre.xml이 없으면 fnlttXbrl.xml을 추가 시도한다.
                if xbrl_dir is None and not args.step8_skip_trackc:
                    try:
                        xbrl_files = dart.download_fnltt_xbrl(
                            latest_rcept_no,
                            str(download_dir),
                            report_code=str(args.report_code),
                        )
                        xbrl_dir = XBRLNoteParser.detect_xbrl_dir_from_files(xbrl_files)
                        if xbrl_dir is not None:
                            trackc_source = "fnlttXbrl.xml"
                    except DartAPIError as exc:
                        infos.append(f"Track C 보강(fnlttXbrl) 실패: {exc}")
    except DartAPIError as exc:
        warnings.append(f"최신 공시 조회/원문 다운로드 실패: {exc}")

    if args.step8_skip_trackc:
        trackc_mode = "skipped(option)"
        infos.append("Track C를 사용자 옵션으로 건너뜀 (--step8-skip-trackc).")
    else:
        if args.xbrl_dir:
            # 사용자 지정 xbrl_dir는 위에서 검증 완료
            pass

        if xbrl_dir is not None:
            try:
                parser = XBRLNoteParser(
                    xbrl_dir=str(xbrl_dir),
                    include_separate=bool(args.step3_include_separate),
                    include_unknown_roles=bool(args.step3_include_unknown_roles),
                )
                notes = parser.parse()
                xbrl_note_count = len(notes)
                sga_accounts = parser.get_sga_accounts()
                segment_members = parser.get_segment_members()
                trackc_mode = "parsed"
            except XBRLParserError as exc:
                trackc_mode = "failed(parse_error)"
                if args.step8_strict_trackc:
                    raise ConfigError(f"Track C 파싱 실패 (--step8-strict-trackc): {exc}") from exc
                warnings.append(f"Track C 파싱 실패: {exc}")
        else:
            trackc_mode = "skipped(no_xbrl_dir)"
            message = "Track C: XBRL 디렉토리를 찾지 못해 생략."
            if args.step8_strict_trackc:
                raise ConfigError(f"{message} (--step8-strict-trackc)")
            infos.append(message)

    # Track B fallback + Step6 정규화/캐시
    fallback_sga: dict[str, dict[str, float]] = {}
    fallback_segment: dict[str, dict[str, float]] = {}
    fallback_tables = 0
    fallback_doc_paths: set[Path] = set()
    single_segment_sources: set[str] = set()
    segment_mode = "pending"

    def _extract_year_columns(df: Any) -> dict[Any, str]:
        return extract_step8_year_columns(df, latest_report_year_hint=latest_report_year_hint)

    def _is_segment_revenue_table(note_title: str, df: Any) -> bool:
        return is_step8_segment_revenue_table(
            note_title=note_title,
            df=df,
            latest_report_year_hint=latest_report_year_hint,
        )

    def _is_single_segment_notice(note_title: str, df: Any) -> bool:
        return is_step8_single_segment_notice(note_title, df)

    def _to_number(value: Any) -> float | None:
        return to_step8_number(value)

    def _should_skip_row(note_type: str, row_label: str) -> bool:
        return should_skip_step8_row(note_type, row_label)

    def _merge_agg(target: dict[str, dict[str, float]], source: dict[str, dict[str, float]]) -> None:
        for account, yearly in source.items():
            bucket = target.setdefault(account, {})
            for year, value in yearly.items():
                bucket[year] = float(bucket.get(year, 0.0)) + float(value)

    if downloaded_files:
        doc_files = [p for p in downloaded_files if p.suffix.lower() in {".html", ".htm", ".xml"}]
        if doc_files:
            classifier = DocumentClassifier(company_name=args.company_name)
            parser = HTMLParser()
            note_docs = classifier.find_notes_files(doc_files)
            consolidated_docs = [doc for doc in note_docs if doc.fs_type == "consolidated"]
            target_docs = consolidated_docs if consolidated_docs else note_docs

            if target_docs:
                if taxonomy_path.exists():
                    try:
                        with CacheDB(cache_db_path) as cache:
                            normalizer = AccountNormalizer(
                                taxonomy_path=str(taxonomy_path),
                                cache_db=cache,
                                llm_client=llm_client,
                                llm_min_unmapped_count=int(args.step8_llm_min_unmapped),
                                llm_max_unmapped_count=int(args.step8_llm_max_unmapped),
                            )

                            for doc in target_docs:
                                fallback_doc_paths.add(doc.path)
                                try:
                                    tables = parser.parse_notes(doc.path)
                                except Exception as exc:  # pylint: disable=broad-except
                                    warnings.append(f"Track B 파싱 실패({doc.path.name}): {exc}")
                                    continue

                                for table in tables:
                                    if table.note_type not in {"sga_detail", "segment_revenue"}:
                                        continue
                                    if table.df.empty:
                                        continue
                                    if (
                                        table.note_type == "segment_revenue"
                                        and not _is_segment_revenue_table(table.note_title, table.df)
                                    ):
                                        if _is_single_segment_notice(table.note_title, table.df):
                                            single_segment_sources.add(doc.path.name)
                                        continue
                                    fallback_tables += 1

                                    first_col = table.df.columns[0]
                                    raw_names = [
                                        name
                                        for x in table.df[first_col].tolist()
                                        for name in [str(x).strip()]
                                        if name and not _should_skip_row(table.note_type, name)
                                    ]
                                    if not raw_names:
                                        continue
                                    mapping = normalizer.normalize(
                                        note_type=table.note_type,
                                        account_names=raw_names,
                                        corp_code=corp_code,
                                        cache_policy=normalizer_cache_policy,
                                    )

                                    year_map = _extract_year_columns(table.df)
                                    if not year_map:
                                        continue

                                    local_agg: dict[str, dict[str, float]] = {}
                                    for _, row in table.df.iterrows():
                                        raw_name = str(row.get(first_col, "")).strip()
                                        if not raw_name:
                                            continue
                                        if _should_skip_row(table.note_type, raw_name):
                                            continue
                                        std_name = mapping.get(raw_name, raw_name)
                                        row_yearly: dict[str, float] = {}
                                        for col_name, year in year_map.items():
                                            value = _to_number(row.get(col_name))
                                            if value is None:
                                                continue
                                            if table.note_type == "segment_revenue" and abs(value) < 100_000:
                                                continue
                                            row_yearly[year] = row_yearly.get(year, 0.0) + value
                                        if not row_yearly:
                                            continue
                                        yearly_bucket = local_agg.setdefault(std_name, {})
                                        for year, value in row_yearly.items():
                                            yearly_bucket[year] = yearly_bucket.get(year, 0.0) + value

                                    if table.note_type == "sga_detail":
                                        _merge_agg(fallback_sga, local_agg)
                                    elif table.note_type == "segment_revenue":
                                        _merge_agg(fallback_segment, local_agg)

                            # notes에서 부문매출을 못 찾으면 사업의내용 문서에서 보강 시도
                            if not fallback_segment:
                                all_docs = classifier.classify_documents(doc_files)
                                business_docs = [doc for doc in all_docs if doc.doc_type == "business"]

                                for doc in business_docs:
                                    if doc.path in fallback_doc_paths:
                                        continue
                                    fallback_doc_paths.add(doc.path)
                                    try:
                                        tables = parser.parse_notes(doc.path)
                                    except Exception as exc:  # pylint: disable=broad-except
                                        warnings.append(f"Track B 파싱 실패({doc.path.name}): {exc}")
                                        continue

                                    for table in tables:
                                        if table.note_type != "segment_revenue":
                                            continue
                                        if table.df.empty:
                                            continue
                                        if not _is_segment_revenue_table(table.note_title, table.df):
                                            if _is_single_segment_notice(table.note_title, table.df):
                                                single_segment_sources.add(doc.path.name)
                                            continue
                                        fallback_tables += 1

                                        first_col = table.df.columns[0]
                                        raw_names = [
                                            name
                                            for x in table.df[first_col].tolist()
                                            for name in [str(x).strip()]
                                            if name and not _should_skip_row(table.note_type, name)
                                        ]
                                        if not raw_names:
                                            continue
                                        mapping = normalizer.normalize(
                                            note_type=table.note_type,
                                            account_names=raw_names,
                                            corp_code=corp_code,
                                            cache_policy=normalizer_cache_policy,
                                        )

                                        year_map = _extract_year_columns(table.df)
                                        if not year_map:
                                            continue

                                        local_agg: dict[str, dict[str, float]] = {}
                                        for _, row in table.df.iterrows():
                                            raw_name = str(row.get(first_col, "")).strip()
                                            if not raw_name:
                                                continue
                                            if _should_skip_row(table.note_type, raw_name):
                                                continue
                                            std_name = mapping.get(raw_name, raw_name)
                                            row_yearly: dict[str, float] = {}
                                            for col_name, year in year_map.items():
                                                value = _to_number(row.get(col_name))
                                                if value is None:
                                                    continue
                                                if abs(value) < 100_000:
                                                    continue
                                                row_yearly[year] = row_yearly.get(year, 0.0) + value
                                            if not row_yearly:
                                                continue
                                            yearly_bucket = local_agg.setdefault(std_name, {})
                                            for year, value in row_yearly.items():
                                                yearly_bucket[year] = yearly_bucket.get(year, 0.0) + value

                                        _merge_agg(fallback_segment, local_agg)

                            normalizer_usage = normalizer.usage()

                    except (CacheDBError, AccountNormalizerError) as exc:
                        warnings.append(f"Step6 정규화/캐시 초기화 실패: {exc}")
                else:
                    warnings.append(f"taxonomy 파일이 없어 Step6 정규화를 건너뜀: {taxonomy_path}")

    if not fallback_segment and single_segment_sources:
        infos.append(
            "Track B segment_revenue: 단일사업부문 공시로 부문별 매출 데이터가 없어 생략."
            f" (source={','.join(sorted(single_segment_sources))})"
        )
        segment_mode = "skipped(single_segment_notice)"
    elif fallback_segment:
        segment_mode = "parsed"
    elif fallback_doc_paths:
        segment_mode = "skipped(no_segment_data)"
    else:
        segment_mode = "skipped(no_docs)"

    if llm_client is not None and hasattr(llm_client, "usage"):
        usage = llm_client.usage()
        llm_usage_payload = {
            "enabled": bool(usage.enabled),
            "max_calls": int(usage.max_calls),
            "calls_used": int(usage.calls_used),
            "calls_blocked": int(usage.calls_blocked),
            "calls_failed": int(usage.calls_failed),
        }
        infos.append(
            "Step6 LLM usage: "
            f"enabled={usage.enabled} max_calls={usage.max_calls} "
            f"used={usage.calls_used} blocked={usage.calls_blocked} failed={usage.calls_failed} "
            f"cache_policy={normalizer_cache_policy} "
            f"unmapped_range={int(args.step8_llm_min_unmapped)}..{int(args.step8_llm_max_unmapped)}"
        )

    try:
        writer = ExcelWriter(
            template_path=template_path,
            create_if_missing=not bool(args.step8_strict_template),
        )
    except ExcelWriterError as exc:
        raise ConfigError(f"엑셀 작성기 초기화 실패: {exc}") from exc

    statement_summaries = []
    if not bs_ts.empty:
        statement_summaries.append(writer.write_balance_sheet(bs_ts))
    else:
        warnings.append("BS 시계열 데이터가 비어 있어 BS 시트 입력을 건너뜀.")

    if not is_ts.empty:
        statement_summaries.append(writer.write_income_statement(is_ts))
    else:
        warnings.append("IS 시계열 데이터가 비어 있어 IS 시트 입력을 건너뜀.")

    if not cf_ts.empty:
        statement_summaries.append(writer.write_cash_flow(cf_ts))
    else:
        warnings.append("CF 시계열 데이터가 비어 있어 CF 시트 입력을 건너뜀.")

    # Track B fallback에서 값이 있으면 우선 반영, 없으면 Track C 구조 기반 seed 반영
    if fallback_sga:
        statement_summaries.append(writer.write_sga_detail(fallback_sga))
    elif sga_accounts:
        sga_seed = {(label or account_id): {} for account_id, label in sga_accounts.items()}
        statement_summaries.append(writer.write_sga_detail(sga_seed))

    if fallback_segment:
        statement_summaries.append(writer.write_segment_revenue(fallback_segment))
    elif segment_members:
        segment_seed = {
            (member.get("label_ko") or member.get("account_id") or f"segment_{idx+1}"): {}
            for idx, member in enumerate(segment_members)
        }
        statement_summaries.append(writer.write_segment_revenue(segment_seed))

    if args.output_path:
        output_path = Path(str(args.output_path))
    else:
        output_path = Path("data") / f"{file_token}_{latest_year}_model.xlsx"

    try:
        saved = writer.save(str(output_path))
    except Exception as exc:  # pylint: disable=broad-except
        raise ConfigError(f"엑셀 저장 실패: {exc}") from exc

    normalizer_metrics: dict[str, Any] = {
        "normalize_calls": 0,
        "cache_read_attempts": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_writes": 0,
        "cache_hit_rate": None,
        "llm_calls": 0,
        "llm_target_names": 0,
    }
    if normalizer_usage is not None:
        normalizer_metrics["normalize_calls"] = int(getattr(normalizer_usage, "normalize_calls", 0))
        normalizer_metrics["cache_read_attempts"] = int(
            getattr(normalizer_usage, "cache_read_attempts", 0)
        )
        normalizer_metrics["cache_hits"] = int(getattr(normalizer_usage, "cache_hits", 0))
        normalizer_metrics["cache_misses"] = int(getattr(normalizer_usage, "cache_misses", 0))
        normalizer_metrics["cache_writes"] = int(getattr(normalizer_usage, "cache_writes", 0))
        normalizer_metrics["llm_calls"] = int(getattr(normalizer_usage, "llm_calls", 0))
        normalizer_metrics["llm_target_names"] = int(
            getattr(normalizer_usage, "llm_target_names", 0)
        )
    if normalizer_metrics["cache_read_attempts"] > 0:
        normalizer_metrics["cache_hit_rate"] = round(
            normalizer_metrics["cache_hits"] / normalizer_metrics["cache_read_attempts"],
            4,
        )

    step8_runtime_ms = int((time.perf_counter() - step8_started_at) * 1000)
    warning_types = summarize_step8_warning_types(warnings)
    metrics_payload: dict[str, Any] = {
        "runtime_ms": step8_runtime_ms,
        "normalizer": normalizer_metrics,
        "warning_types": warning_types,
    }
    if llm_usage_payload is not None:
        metrics_payload["llm"] = llm_usage_payload

    if args.step8_summary_path:
        summary_path = Path(str(args.step8_summary_path))
    else:
        summary_path = output_path.with_name(f"{output_path.stem}_summary.json")

    summary_saved: Path | None = None
    summary_payload = build_step8_summary_payload(
        corp_code=corp_code,
        company_name=args.company_name or "",
        fs_div=fs_div,
        years=sorted(multi_year.keys()),
        report_code=str(args.report_code),
        bs_rows=len(bs_ts),
        is_rows=len(is_ts),
        cf_rows=len(cf_ts),
        trackc_mode=trackc_mode,
        trackc_source=trackc_source,
        xbrl_dir=xbrl_dir,
        xbrl_note_count=xbrl_note_count,
        sga_accounts_count=len(sga_accounts),
        segment_members_count=len(segment_members),
        segment_mode=segment_mode,
        latest_rcept_no=latest_rcept_no,
        fallback_doc_count=len(fallback_doc_paths),
        fallback_tables=fallback_tables,
        fallback_sga_rows=len(fallback_sga),
        fallback_segment_rows=len(fallback_segment),
        single_segment_sources=single_segment_sources,
        statement_summaries=statement_summaries,
        infos=infos,
        warnings=warnings,
        output_excel=saved,
        metrics=metrics_payload,
    )
    try:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary_saved = summary_path
    except OSError as exc:
        warnings.append(f"Step8 요약 JSON 저장 실패: {exc}")

    print(
        f"[Step8] done corp_code={corp_code} fs_div={fs_div} "
        f"years={','.join(sorted(multi_year.keys()))}"
    )
    print(
        f"[Step8] TrackA rows: BS={len(bs_ts)} IS={len(is_ts)} CF={len(cf_ts)}"
    )
    print(
        f"[Step8] TrackC: mode={trackc_mode} source={trackc_source} xbrl_dir={xbrl_dir} notes={xbrl_note_count} "
        f"sga_accounts={len(sga_accounts)} segment_members={len(segment_members)}"
    )
    print(
        f"[Step8] TrackB fallback: rcept_no={latest_rcept_no or '-'} "
        f"mode={segment_mode} docs={len(fallback_doc_paths)} tables={fallback_tables} "
        f"sga_rows={len(fallback_sga)} segment_rows={len(fallback_segment)}"
    )
    print(
        f"[Step8] metrics: runtime_ms={step8_runtime_ms} "
        f"cache_hit_rate={normalizer_metrics['cache_hit_rate']} "
        f"warning_types={len(warning_types)}"
    )

    for summary in statement_summaries:
        print(
            f"[Step8] write {summary.sheet_name}: "
            f"cells={summary.written_cells} matched={summary.matched_accounts} "
            f"unmatched={len(summary.unmatched_accounts)}"
        )

    if infos:
        print(f"[Step8] infos={len(infos)}")
        for info in infos:
            print(f"  - {info}")

    if warnings:
        print(f"[Step8] warnings={len(warnings)}")
        for warning in warnings:
            print(f"  - {warning}")

    print(f"[Step8] output={saved}")
    if summary_saved is not None:
        print(f"[Step8] summary={summary_saved}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config)
    taxonomy_path = Path(args.taxonomy)
    dotenv_path = Path(".env")

    try:
        load_dotenv_file(dotenv_path)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if args.check_config:
        try:
            return run_config_check(config_path, taxonomy_path)
        except ConfigError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

    try:
        settings = load_yaml(config_path)
        validate_settings(settings, config_path)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        network_result = run_network_check_actions(settings, args)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if network_result != -1:
        return network_result

    try:
        step1_result = run_step1_actions(settings, args)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if step1_result != -1:
        return step1_result

    try:
        step2_result = run_step2_actions(settings, args)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if step2_result != -1:
        return step2_result

    try:
        step3_result = run_step3_actions(settings, args)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if step3_result != -1:
        return step3_result

    try:
        step4_result = run_step4_actions(settings, args)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if step4_result != -1:
        return step4_result

    try:
        step8_result = run_step8_actions(settings, args)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if step8_result != -1:
        return step8_result

    print("Step 0 스캐폴딩 완료 상태입니다.")
    print("설정 검증: python main.py --check-config")
    print("네트워크 점검: python main.py --check-network")
    print("네트워크 상세: python main.py --check-network --verbose")
    print("Step 1 예시: python main.py --company-name 삼성전자")
    print("Step 2 예시: python main.py --step2-fetch-statements --company-name 삼성전자 --years 2022,2023,2024")
    print("Step 3 예시: python main.py --step3-parse-xbrl --xbrl-dir data/raw/샘플/rcept_no")
    print("Step 4 예시: python main.py --step4-validate-caldef --xbrl-dir data/raw/샘플/rcept_no --step4-role-code D210000")
    print("Step 8 예시: python main.py --step8-run-pipeline --company-name 삼성전자 --years 2022,2023,2024")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
