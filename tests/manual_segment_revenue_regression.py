#!/usr/bin/env python3
"""Manual regression check for Track B segment_revenue extraction rules.

This script validates known sample outcomes from locally cached raw filings.
It is intended for manual execution (not default CI) because it depends on
`data/raw/<company>/<rcept_no>` artifacts.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.document_classifier import DocumentClassifier
from src.html_parser import HTMLParser


@dataclass
class CompanyExpectation:
    min_accepted: int = 0
    min_single_segment_notice: int = 0


DEFAULT_EXPECTATIONS: dict[str, CompanyExpectation] = {
    "삼성전자": CompanyExpectation(min_accepted=1, min_single_segment_notice=0),
    "SK하이닉스": CompanyExpectation(min_accepted=0, min_single_segment_notice=1),
    "현대자동차": CompanyExpectation(min_accepted=1, min_single_segment_notice=0),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual segment_revenue regression check.")
    parser.add_argument(
        "--raw-root",
        default="data/raw",
        help="Root directory of downloaded filings (default: data/raw).",
    )
    parser.add_argument(
        "--companies",
        default="삼성전자,SK하이닉스,현대자동차",
        help="Comma-separated company names to validate.",
    )
    parser.add_argument(
        "--latest-year-hint",
        type=int,
        default=2024,
        help="Latest report year hint used for symbolic year parsing.",
    )
    return parser.parse_args(argv)


def _extract_year_columns(df: pd.DataFrame, latest_report_year_hint: int | None) -> dict[Any, str]:
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


def _to_number(value: Any) -> float | None:
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


def _is_segment_revenue_table(note_title: str, df: pd.DataFrame, latest_report_year_hint: int | None) -> bool:
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

    year_map = _extract_year_columns(df, latest_report_year_hint=latest_report_year_hint)
    if len(year_map) < 2:
        return False

    valid_rows = 0
    for _, row in df.iterrows():
        key = str(row.get(first_col, "")).strip()
        if not key or key.lower() == "nan":
            continue
        has_major_numeric = False
        for col_name in year_map:
            value = _to_number(row.get(col_name))
            if value is not None and abs(value) >= 100_000:
                has_major_numeric = True
                break
        if has_major_numeric:
            valid_rows += 1
        if valid_rows >= 2:
            return True
    return False


def _is_single_segment_notice(note_title: str, df: pd.DataFrame) -> bool:
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


def _collect_company_result(
    *,
    company: str,
    raw_root: Path,
    parser: HTMLParser,
    latest_report_year_hint: int | None,
) -> tuple[int, int]:
    classifier = DocumentClassifier(company_name=company)
    company_dir = raw_root / company
    if not company_dir.exists():
        raise RuntimeError(f"raw 디렉토리가 없습니다: {company_dir}")

    rcept_dirs = sorted([p for p in company_dir.iterdir() if p.is_dir() and p.name.isdigit()])
    if not rcept_dirs:
        raise RuntimeError(f"rcept 하위 디렉토리가 없습니다: {company_dir}")
    rcept_dir = rcept_dirs[-1]

    doc_files = [
        p
        for p in rcept_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".html", ".htm", ".xml"}
    ]
    notes = classifier.find_notes_files(doc_files)
    consolidated = [doc for doc in notes if doc.fs_type == "consolidated"]
    target_docs = consolidated if consolidated else notes
    searched_paths = {doc.path for doc in target_docs}

    accepted_segment_tables = 0
    single_segment_notices = 0

    def scan_docs(docs: list[Any]) -> None:
        nonlocal accepted_segment_tables, single_segment_notices
        for doc in docs:
            try:
                tables = parser.parse_notes(doc.path)
            except Exception:
                continue
            for table in tables:
                if table.note_type != "segment_revenue":
                    continue
                if _is_segment_revenue_table(
                    table.note_title,
                    table.df,
                    latest_report_year_hint=latest_report_year_hint,
                ):
                    accepted_segment_tables += 1
                elif _is_single_segment_notice(table.note_title, table.df):
                    single_segment_notices += 1

    scan_docs(target_docs)
    if accepted_segment_tables == 0:
        all_docs = classifier.classify_documents(doc_files)
        business_docs = [
            doc for doc in all_docs if doc.doc_type == "business" and doc.path not in searched_paths
        ]
        scan_docs(business_docs)

    return accepted_segment_tables, single_segment_notices


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    raw_root = Path(str(args.raw_root))
    companies = [x.strip() for x in str(args.companies).split(",") if x.strip()]

    parser = HTMLParser()

    failures: list[str] = []
    for company in companies:
        expected = DEFAULT_EXPECTATIONS.get(company)
        if expected is None:
            failures.append(f"{company}: 기대치가 정의되지 않았습니다.")
            continue

        try:
            accepted, notices = _collect_company_result(
                company=company,
                raw_root=raw_root,
                parser=parser,
                latest_report_year_hint=args.latest_year_hint,
            )
        except Exception as exc:  # pylint: disable=broad-except
            failures.append(f"{company}: 실행 실패 ({exc})")
            continue

        print(
            f"[segment-regression] company={company} "
            f"accepted={accepted} single_segment_notices={notices}"
        )

        if accepted < expected.min_accepted:
            failures.append(
                f"{company}: accepted={accepted} < expected_min={expected.min_accepted}"
            )
        if notices < expected.min_single_segment_notice:
            failures.append(
                f"{company}: single_segment_notices={notices} "
                f"< expected_min={expected.min_single_segment_notice}"
            )

    if failures:
        print("[segment-regression] FAIL")
        for item in failures:
            print(f"- {item}")
        return 1

    print("[segment-regression] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
