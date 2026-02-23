#!/usr/bin/env python3
"""Online Track C strict gate.

This script tries multiple real companies and requires at least N strict Track C passes.
It is intended for manual/dispatch CI runs with DART_API_KEY configured.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


DEFAULT_CANDIDATES = [
    "삼성전자",
    "SK하이닉스",
    "현대자동차",
    "LG전자",
    "NAVER",
    "POSCO홀딩스",
]


def to_safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value).strip("_") or "sample"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run online strict Track C gate.")
    parser.add_argument(
        "--candidates",
        default=",".join(DEFAULT_CANDIDATES),
        help="Comma-separated company names to try.",
    )
    parser.add_argument(
        "--years",
        default="2024",
        help="Years argument passed to Step8 (default: 2024).",
    )
    parser.add_argument(
        "--min-success",
        type=int,
        default=1,
        help="Minimum number of strict passes required (default: 1).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=4,
        help="Maximum number of companies to try (default: 4).",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/trackc_strict_online_gate",
        help="Directory for temporary Step8 outputs.",
    )
    return parser.parse_args(argv)


def run_strict_step8(company_name: str, years: str, output_dir: Path) -> tuple[int, str]:
    token = to_safe_token(company_name)
    out_xlsx = output_dir / f"{token}_strict.xlsx"
    out_summary = output_dir / f"{token}_strict_summary.json"

    cmd = [
        sys.executable,
        "main.py",
        "--step8-run-pipeline",
        "--company-name",
        company_name,
        "--years",
        years,
        "--step8-strict-trackc",
        "--output-path",
        str(out_xlsx),
        "--step8-summary-path",
        str(out_summary),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")

    if proc.returncode == 0 and out_summary.exists():
        try:
            payload = json.loads(out_summary.read_text(encoding="utf-8"))
            mode = str(payload.get("track_c", {}).get("mode", ""))
            if mode != "parsed":
                return 1, f"unexpected track_c.mode={mode} (expected parsed)"
        except Exception as exc:  # pylint: disable=broad-except
            return 1, f"summary parse failed: {exc}"

    return proc.returncode, output


def classify_failure(output: str) -> str:
    if "Track C: XBRL 디렉토리를 찾지 못해 생략." in output:
        return "no_xbrl_dir"
    if "Track C 파싱 실패 (--step8-strict-trackc)" in output:
        return "parse_error"
    if "최신 공시 조회/원문 다운로드 실패" in output:
        return "network_or_download_error"
    if "Track A 수집 실패" in output:
        return "tracka_error"
    return "unknown_error"


def extract_error_snippet(output: str) -> str:
    for line in (output or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("[ERROR]"):
            return stripped
    for line in reversed((output or "").splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return "no_output"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates = [x.strip() for x in str(args.candidates).split(",") if x.strip()]
    if not candidates:
        print("[online-trackc] no candidates")
        return 2

    attempts = candidates[: max(1, args.max_attempts)]
    successes: list[str] = []
    failures: list[tuple[str, str]] = []

    print(
        f"[online-trackc] start years={args.years} min_success={args.min_success} "
        f"attempts={len(attempts)}"
    )
    for idx, company in enumerate(attempts, start=1):
        print(f"[online-trackc] try {idx}/{len(attempts)} company={company}")
        rc, output = run_strict_step8(company, args.years, output_dir)
        if rc == 0:
            successes.append(company)
            print(f"[online-trackc] PASS company={company}")
            if len(successes) >= args.min_success:
                break
            continue

        reason = classify_failure(output)
        snippet = extract_error_snippet(output)
        failures.append((company, reason))
        print(f"[online-trackc] FAIL company={company} reason={reason} detail={snippet}")

    print(f"[online-trackc] result pass={len(successes)} fail={len(failures)}")
    if successes:
        print(f"[online-trackc] pass_list={','.join(successes)}")
    if failures:
        print(
            "[online-trackc] fail_list="
            + ",".join(f"{company}:{reason}" for company, reason in failures)
        )

    return 0 if len(successes) >= args.min_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
