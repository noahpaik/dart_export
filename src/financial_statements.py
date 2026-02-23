"""Financial statement extraction module (Track A)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class FinancialStatementError(RuntimeError):
    """Raised when financial statement fetch or transform fails."""


class StatementType(Enum):
    """DART sj_div statement types."""

    BS = "BS"    # Balance Sheet
    IS = "IS"    # Income Statement
    CIS = "CIS"  # Comprehensive Income Statement
    CF = "CF"    # Cash Flow Statement
    SCE = "SCE"  # Statement of Changes in Equity


@dataclass
class FinancialStatement:
    statement_type: StatementType
    corp_code: str
    year: str
    fs_div: str
    df: pd.DataFrame
    raw_data: list[dict[str, Any]]


class FinancialStatementFetcher:
    """
    Track A fetcher for DART structured financial statements.

    Endpoint:
      - /fnlttSinglAcntAll.json
    """

    BASE_URL = "https://opendart.fss.or.kr/api"
    CONNECT_TIMEOUT_SECONDS = 10
    REQUEST_TIMEOUT_SECONDS = 30
    RETRY_TOTAL = 4
    RETRY_CONNECT = 4
    RETRY_READ = 2
    RETRY_STATUS = 3
    RETRY_BACKOFF_FACTOR = 0.7
    RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

    def __init__(self, api_key: str):
        normalized = (api_key or "").strip()
        if not normalized or normalized == "YOUR_DART_API_KEY":
            raise FinancialStatementError("유효한 DART API 키가 필요합니다.")
        self.api_key = normalized
        self.session = requests.Session()
        self._configure_session_retries()

    def _configure_session_retries(self) -> None:
        retry_policy = Retry(
            total=self.RETRY_TOTAL,
            connect=self.RETRY_CONNECT,
            read=self.RETRY_READ,
            status=self.RETRY_STATUS,
            backoff_factor=self.RETRY_BACKOFF_FACTOR,
            status_forcelist=self.RETRY_STATUS_FORCELIST,
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry_policy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_all_statements(
        self,
        corp_code: str,
        year: str,
        report_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict[StatementType, FinancialStatement]:
        """
        단일 연도 기준으로 재무제표(BS/IS/CIS/CF/SCE)를 모두 조회한다.
        """
        normalized_corp_code = self._normalize_corp_code(corp_code)
        normalized_year = self._normalize_year(year)
        normalized_report_code = self._normalize_report_code(report_code)
        normalized_fs_div = self._normalize_fs_div(fs_div)

        payload = self._request_json(
            "/fnlttSinglAcntAll.json",
            {
                "corp_code": normalized_corp_code,
                "bsns_year": normalized_year,
                "reprt_code": normalized_report_code,
                "fs_div": normalized_fs_div,
            },
        )

        raw_rows = payload.get("list", [])
        if not isinstance(raw_rows, list) or not raw_rows:
            raise FinancialStatementError(
                f"재무제표 데이터가 없습니다: corp_code={normalized_corp_code}, year={normalized_year}"
            )

        result: dict[StatementType, FinancialStatement] = {}
        for statement_type in StatementType:
            filtered = [
                row for row in raw_rows
                if isinstance(row, dict) and row.get("sj_div") == statement_type.value
            ]
            if not filtered:
                continue

            df = self._to_dataframe(filtered)
            result[statement_type] = FinancialStatement(
                statement_type=statement_type,
                corp_code=normalized_corp_code,
                year=normalized_year,
                fs_div=normalized_fs_div,
                df=df,
                raw_data=filtered,
            )

        if not result:
            raise FinancialStatementError(
                f"유효한 sj_div 데이터가 없습니다: corp_code={normalized_corp_code}, year={normalized_year}"
            )

        return result

    def fetch_multi_year(
        self,
        corp_code: str,
        years: list[str],
        report_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict[str, dict[StatementType, FinancialStatement]]:
        """
        다개년 재무제표를 수집한다.
        실패한 연도는 건너뛰고 가능한 연도만 반환한다.
        """
        if not years:
            raise FinancialStatementError("years는 최소 1개 이상 필요합니다.")

        collected: dict[str, dict[StatementType, FinancialStatement]] = {}
        errors: dict[str, str] = {}

        for year in years:
            normalized_year = self._normalize_year(year)
            try:
                collected[normalized_year] = self.fetch_all_statements(
                    corp_code=corp_code,
                    year=normalized_year,
                    report_code=report_code,
                    fs_div=fs_div,
                )
            except FinancialStatementError as exc:
                errors[normalized_year] = str(exc)

        if not collected:
            details = ", ".join(f"{k}: {v}" for k, v in sorted(errors.items()))
            raise FinancialStatementError(f"다개년 수집 실패: {details}")

        return collected

    def _to_dataframe(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        has_thstrm_add = any("thstrm_add_amount" in row for row in rows)
        has_frmtrm_add = any("frmtrm_add_amount" in row for row in rows)
        has_bfefrmtrm_add = any("bfefrmtrm_add_amount" in row for row in rows)

        for row in rows:
            record: dict[str, Any] = {
                "접수번호": row.get("rcept_no", ""),
                "재무제표구분": row.get("sj_div", ""),
                "재무제표명": row.get("sj_nm", ""),
                "계정ID": row.get("account_id", ""),
                "계정명": row.get("account_nm", ""),
                "계정상세": row.get("account_detail", ""),
                "당기명": row.get("thstrm_nm", ""),
                "전기명": row.get("frmtrm_nm", ""),
                "전전기명": row.get("bfefrmtrm_nm", ""),
                "당기": self._parse_amount(row.get("thstrm_amount")),
                "전기": self._parse_amount(row.get("frmtrm_amount")),
                "전전기": self._parse_amount(row.get("bfefrmtrm_amount")),
                "표시순서": self._parse_order(row.get("ord")),
            }
            if has_thstrm_add:
                record["당기누적"] = self._parse_amount(row.get("thstrm_add_amount"))
            if has_frmtrm_add:
                record["전기누적"] = self._parse_amount(row.get("frmtrm_add_amount"))
            if has_bfefrmtrm_add:
                record["전전기누적"] = self._parse_amount(row.get("bfefrmtrm_add_amount"))
            records.append(record)

        df = pd.DataFrame(records)
        if df.empty:
            return df

        df = df.sort_values(["표시순서", "계정ID", "계정명"], na_position="last")
        df = df.drop_duplicates(subset=["계정ID"], keep="first").reset_index(drop=True)
        return df

    def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {"crtfc_key": self.api_key, **params}
        try:
            response = self.session.get(
                f"{self.BASE_URL}{path}",
                params=query,
                timeout=(self.CONNECT_TIMEOUT_SECONDS, self.REQUEST_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FinancialStatementError(
                f"DART API 호출 실패 ({path}): {self._format_request_error(exc)}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise FinancialStatementError(f"DART 응답 JSON 파싱 실패 ({path})") from exc

        status = payload.get("status")
        if status and status != "000":
            message = payload.get("message") or payload.get("msg") or "Unknown error"
            raise FinancialStatementError(f"DART API 에러 ({path}): {status} - {message}")
        return payload

    @staticmethod
    def _parse_order(value: Any) -> int:
        if value is None:
            return 0
        text = str(value).strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0

    @staticmethod
    def _parse_amount(value: Any) -> float | None:
        if value is None:
            return None
        text = str(value).strip()
        if text in {"", "-", "N/A", "nan", "None"}:
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
        )

        try:
            number = float(cleaned)
        except ValueError:
            return None
        return -number if negative else number

    @staticmethod
    def _normalize_year(year: str) -> str:
        value = (year or "").strip()
        if len(value) != 4 or not value.isdigit():
            raise FinancialStatementError(f"잘못된 year 값: {year}")
        return value

    @staticmethod
    def _normalize_corp_code(corp_code: str) -> str:
        value = (corp_code or "").strip()
        if len(value) != 8 or not value.isdigit():
            raise FinancialStatementError(f"잘못된 corp_code 값: {corp_code}")
        return value

    @staticmethod
    def _normalize_report_code(report_code: str) -> str:
        value = (report_code or "").strip()
        valid_codes = {"11011", "11012", "11013", "11014"}
        if value not in valid_codes:
            raise FinancialStatementError(
                f"잘못된 report_code 값: {report_code} (허용: {', '.join(sorted(valid_codes))})"
            )
        return value

    @staticmethod
    def _normalize_fs_div(fs_div: str) -> str:
        value = (fs_div or "").strip().upper()
        if value not in {"CFS", "OFS"}:
            raise FinancialStatementError(f"잘못된 fs_div 값: {fs_div} (허용: CFS/OFS)")
        return value

    @staticmethod
    def _format_request_error(exc: requests.RequestException) -> str:
        if isinstance(exc, requests.exceptions.Timeout):
            return "요청 시간 초과"
        if isinstance(exc, requests.exceptions.SSLError):
            return "TLS/SSL 연결 실패"
        if isinstance(exc, requests.exceptions.ConnectionError):
            if FinancialStatementFetcher._is_dns_resolution_error(exc):
                return "DNS 해석 실패"
            return "네트워크 연결 실패"
        if isinstance(exc, requests.exceptions.HTTPError):
            status = exc.response.status_code if exc.response is not None else "unknown"
            return f"HTTP {status}"
        return exc.__class__.__name__

    @staticmethod
    def _is_dns_resolution_error(exc: requests.RequestException) -> bool:
        message = str(exc).lower()
        markers = (
            "nameresolutionerror",
            "failed to resolve",
            "temporary failure in name resolution",
            "name or service not known",
            "nodename nor servname provided",
        )
        return any(marker in message for marker in markers)


def build_time_series(
    multi_year_data: dict[str, dict[StatementType, FinancialStatement]],
    target_type: StatementType,
) -> pd.DataFrame:
    """
    다개년 재무제표(dict)를 특정 재무제표 타입 기준 시계열 DataFrame으로 병합한다.

    Output columns:
      [계정ID, 계정명, <year1>, <year2>, ...]
    """
    if not multi_year_data:
        return pd.DataFrame(columns=["계정ID", "계정명"])

    merged: pd.DataFrame | None = None

    for year in sorted(multi_year_data.keys()):
        statements = multi_year_data.get(year, {})
        financial_statement = statements.get(target_type)
        if financial_statement is None or financial_statement.df.empty:
            continue

        year_frame = financial_statement.df.loc[:, ["계정ID", "계정명", "당기"]].copy()
        year_frame = year_frame.rename(columns={"당기": year})
        year_frame = year_frame.drop_duplicates(subset=["계정ID"], keep="first")

        if merged is None:
            merged = year_frame
        else:
            merged = merged.merge(
                year_frame[["계정ID", year]],
                on="계정ID",
                how="outer",
            )

    if merged is None:
        return pd.DataFrame(columns=["계정ID", "계정명"])

    year_columns = [col for col in merged.columns if col not in {"계정ID", "계정명"}]
    if year_columns:
        merged = merged[["계정ID", "계정명", *sorted(year_columns)]]
    return merged.reset_index(drop=True)
