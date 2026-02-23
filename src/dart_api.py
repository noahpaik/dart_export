"""DART OpenAPI client module."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.corp_code_db import CorpCodeDB, CorpCodeDBError


class DartAPIError(RuntimeError):
    """Raised when DART API request or parsing fails."""


class DartAPI:
    BASE_URL = "https://opendart.fss.or.kr/api"
    CONNECT_TIMEOUT_SECONDS = 10
    REQUEST_TIMEOUT_SECONDS = 30
    RETRY_TOTAL = 4
    RETRY_CONNECT = 4
    RETRY_READ = 2
    RETRY_STATUS = 3
    RETRY_BACKOFF_FACTOR = 0.7
    RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

    def __init__(self, api_key: str, corp_db_path: str = "data/corp_code.db"):
        if not api_key or api_key == "YOUR_DART_API_KEY":
            raise DartAPIError("유효한 DART API 키가 필요합니다.")
        self.api_key = api_key
        self.corp_db_path = corp_db_path
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

    def get_reports(
        self,
        corp_code: str,
        year: str,
        report_code: str = "11011",
        page_count: int = 100,
    ) -> list[dict[str, Any]]:
        """
        회사/연도 기준 정기공시 목록에서 지정 보고서 코드를 필터링해 반환한다.

        report_code:
          11011=사업보고서, 11012=반기보고서, 11013=1분기보고서, 11014=3분기보고서
        """
        normalized_year = self._normalize_year(year)
        normalized_corp_code = self._normalize_corp_code(corp_code)

        payload = self._request_json(
            "/list.json",
            {
                "corp_code": normalized_corp_code,
                "bgn_de": f"{normalized_year}0101",
                "end_de": f"{normalized_year}1231",
                "pblntf_ty": "A",
                "page_count": page_count,
            },
        )

        reports = payload.get("list", [])
        if not isinstance(reports, list):
            return []

        return [
            report
            for report in reports
            if isinstance(report, dict)
            and self._matches_report_code(str(report.get("report_nm", "")), report_code)
        ]

    def get_latest_report(
        self,
        corp_code: str,
        year: str,
        report_code: str = "11011",
    ) -> dict[str, Any] | None:
        """
        최신 공시 1건을 반환한다.
        """
        reports = self.get_reports(corp_code, year, report_code=report_code)
        if not reports:
            return None
        reports_sorted = sorted(
            reports,
            key=lambda row: (str(row.get("rcept_dt", "")), str(row.get("rcept_no", ""))),
            reverse=True,
        )
        return reports_sorted[0]

    def get_corp_code(self, company_name: str) -> str | None:
        """
        회사명으로 corp_code를 조회한다.
        corp DB가 비어 있으면 자동 초기화한다.
        """
        with CorpCodeDB(self.corp_db_path) as db:
            if db.is_empty():
                try:
                    db.build_from_dart(self.api_key)
                except CorpCodeDBError as exc:
                    raise DartAPIError(f"corp_code DB 초기화 실패: {exc}") from exc
            return db.search(company_name)

    def get_corp_code_by_stock_code(self, stock_code: str) -> str | None:
        with CorpCodeDB(self.corp_db_path) as db:
            if db.is_empty():
                try:
                    db.build_from_dart(self.api_key)
                except CorpCodeDBError as exc:
                    raise DartAPIError(f"corp_code DB 초기화 실패: {exc}") from exc
            return db.search_by_stock_code(stock_code)

    def download_document(self, rcept_no: str, save_dir: str) -> list[Path]:
        """
        공시 원문 zip을 다운로드해 압축 해제하고 HTML/XBRL 파일 목록을 반환한다.
        """
        normalized_rcept_no = self._normalize_rcept_no(rcept_no)
        target_dir = Path(save_dir) / normalized_rcept_no
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            response = self.session.get(
                f"{self.BASE_URL}/document.xml",
                params={"crtfc_key": self.api_key, "rcept_no": normalized_rcept_no},
                timeout=(self.CONNECT_TIMEOUT_SECONDS, self.REQUEST_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DartAPIError(
                f"원문 다운로드 실패: {self._format_request_error(exc)}"
            ) from exc

        return self._extract_zip_response(
            response.content,
            target_dir=target_dir,
            badzip_message="원문 응답이 zip 형식이 아닙니다.",
        )

    def download_fnltt_xbrl(
        self,
        rcept_no: str,
        save_dir: str,
        report_code: str | None = None,
    ) -> list[Path]:
        """
        재무제표 원본(XBRL) zip을 다운로드해 압축 해제하고 파일 목록을 반환한다.

        OpenDART 환경 차이를 고려해
        - rcept_no 단독 파라미터
        - rcept_no + reprt_code
        두 형태를 순차 시도한다.
        """
        normalized_rcept_no = self._normalize_rcept_no(rcept_no)
        target_dir = Path(save_dir) / f"{normalized_rcept_no}_fnltt_xbrl"
        target_dir.mkdir(parents=True, exist_ok=True)

        param_candidates: list[dict[str, str]] = [{"rcept_no": normalized_rcept_no}]
        code = str(report_code or "").strip()
        if code:
            param_candidates.append(
                {"rcept_no": normalized_rcept_no, "reprt_code": code}
            )

        last_error: DartAPIError | None = None
        for params in param_candidates:
            try:
                response = self.session.get(
                    f"{self.BASE_URL}/fnlttXbrl.xml",
                    params={"crtfc_key": self.api_key, **params},
                    timeout=(self.CONNECT_TIMEOUT_SECONDS, self.REQUEST_TIMEOUT_SECONDS),
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                last_error = DartAPIError(
                    f"XBRL 원본 다운로드 실패: {self._format_request_error(exc)}"
                )
                continue

            try:
                return self._extract_zip_response(
                    response.content,
                    target_dir=target_dir,
                    badzip_message="XBRL 원본 응답이 zip 형식이 아닙니다.",
                )
            except DartAPIError as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise DartAPIError("XBRL 원본 다운로드 실패: 알 수 없는 오류")

    @staticmethod
    def _extract_zip_response(
        content: bytes,
        target_dir: Path,
        badzip_message: str,
    ) -> list[Path]:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                archive.extractall(target_dir)
        except zipfile.BadZipFile as exc:
            raise DartAPIError(badzip_message) from exc

        html_files = list(target_dir.rglob("*.html")) + list(target_dir.rglob("*.htm"))
        xbrl_files = list(target_dir.rglob("*.xml")) + list(target_dir.rglob("*.xbrl"))
        return sorted({*html_files, *xbrl_files})

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
            raise DartAPIError(
                f"DART API 호출 실패 ({path}): {self._format_request_error(exc)}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise DartAPIError(f"DART API 응답 JSON 파싱 실패 ({path})") from exc

        status = payload.get("status")
        if status and status != "000":
            message = payload.get("message") or payload.get("msg") or "Unknown error"
            raise DartAPIError(f"DART API 에러 ({path}): {status} - {message}")
        return payload

    @staticmethod
    def _normalize_year(year: str) -> str:
        value = (year or "").strip()
        if len(value) != 4 or not value.isdigit():
            raise DartAPIError(f"잘못된 year 값: {year}")
        return value

    @staticmethod
    def _normalize_corp_code(corp_code: str) -> str:
        value = (corp_code or "").strip()
        if len(value) != 8 or not value.isdigit():
            raise DartAPIError(f"잘못된 corp_code 값: {corp_code}")
        return value

    @staticmethod
    def _normalize_rcept_no(rcept_no: str) -> str:
        value = (rcept_no or "").strip()
        if len(value) != 14 or not value.isdigit():
            raise DartAPIError(f"잘못된 rcept_no 값: {rcept_no}")
        return value

    @staticmethod
    def _matches_report_code(report_nm: str, report_code: str) -> bool:
        """
        DART list.json의 report_nm 문자열로 정기보고서 코드를 판별한다.
        """
        name = (report_nm or "").strip()
        code = (report_code or "").strip()

        if not name:
            return False
        if not code:
            return True

        if code == "11011":
            return "사업보고서" in name
        if code == "11012":
            return "반기보고서" in name
        if code in {"11013", "11014"}:
            if "분기보고서" not in name:
                return False
            month_match = re.search(r"\((?:\d{4}\.)?(\d{1,2})\)", name)
            if month_match:
                month = int(month_match.group(1))
                return (code == "11013" and month == 3) or (code == "11014" and month == 9)
            # 괄호 내 월 정보가 없으면 이름 기반으로 보수적으로 판별한다.
            if code == "11013":
                return "1분기" in name
            return "3분기" in name

        # 알 수 없는 코드가 들어오면 기존 동작(문자열 포함)으로 폴백.
        return code in name

    @staticmethod
    def _format_request_error(exc: requests.RequestException) -> str:
        _, message = DartAPI._classify_request_error(exc)
        return message

    @staticmethod
    def _classify_request_error(exc: requests.RequestException) -> tuple[str, str]:
        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout", "요청 시간 초과"
        if isinstance(exc, requests.exceptions.SSLError):
            return "ssl", "TLS/SSL 연결 실패"
        if isinstance(exc, requests.exceptions.ConnectionError):
            if DartAPI._is_dns_resolution_error(exc):
                return "dns", "DNS 해석 실패"
            return "connection", "네트워크 연결 실패"
        if isinstance(exc, requests.exceptions.HTTPError):
            status = exc.response.status_code if exc.response is not None else "unknown"
            return "http", f"HTTP {status}"
        return "unknown", exc.__class__.__name__

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
