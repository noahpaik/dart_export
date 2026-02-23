"""Corp code SQLite management module."""

from __future__ import annotations

import io
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests


CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
REQUEST_TIMEOUT_SECONDS = 30


class CorpCodeDBError(RuntimeError):
    """Raised when corp code DB operation fails."""


@dataclass(frozen=True)
class CorpRecord:
    corp_code: str
    corp_name: str
    stock_code: str
    modify_date: str
    is_listed: int


class CorpCodeDB:
    """
    DART 고유번호(corp_code) 로컬 DB.

    corpCode.xml (zip) 을 다운로드해 SQLite에 적재한다.
    """

    def __init__(self, db_path: str = "data/corp_code.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corp (
                corp_code TEXT PRIMARY KEY,
                corp_name TEXT NOT NULL,
                stock_code TEXT,
                modify_date TEXT,
                is_listed INTEGER DEFAULT 0
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_corp_name ON corp(corp_name)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stock_code ON corp(stock_code)"
        )
        self.conn.commit()

    def is_empty(self) -> bool:
        row = self.conn.execute("SELECT COUNT(*) FROM corp").fetchone()
        if row is None:
            return True
        return int(row[0]) == 0

    def build_from_dart(self, api_key: str) -> int:
        """
        DART API에서 corpCode.xml zip을 다운로드하여 DB에 적재한다.

        Returns:
            적재된 레코드 수
        """
        if not api_key or api_key == "YOUR_DART_API_KEY":
            raise CorpCodeDBError("유효한 DART API 키가 필요합니다.")

        try:
            response = requests.get(
                CORP_CODE_URL,
                params={"crtfc_key": api_key},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CorpCodeDBError(
                f"corpCode.xml 다운로드 실패: {self._format_request_error(exc)}"
            ) from exc

        records = self._parse_corp_zip(response.content)
        if not records:
            raise CorpCodeDBError("corpCode.xml 파싱 결과가 비어 있습니다.")

        self.conn.executemany(
            "INSERT OR REPLACE INTO corp VALUES (?, ?, ?, ?, ?)",
            [
                (
                    rec.corp_code,
                    rec.corp_name,
                    rec.stock_code,
                    rec.modify_date,
                    rec.is_listed,
                )
                for rec in records
            ],
        )
        self.conn.commit()
        return len(records)

    def _parse_corp_zip(self, zip_bytes: bytes) -> list[CorpRecord]:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
                names = archive.namelist()
                if not names:
                    raise CorpCodeDBError("다운로드된 zip 파일이 비어 있습니다.")
                xml_bytes = archive.read(names[0])
        except zipfile.BadZipFile as exc:
            raise CorpCodeDBError("corpCode.xml 응답이 zip 형식이 아닙니다.") from exc
        except KeyError as exc:
            raise CorpCodeDBError("zip 내부 XML 파일을 찾을 수 없습니다.") from exc

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise CorpCodeDBError(f"XML 파싱 실패: {exc}") from exc

        records: list[CorpRecord] = []
        for item in root.iter("list"):
            corp_code = (item.findtext("corp_code") or "").strip()
            corp_name = (item.findtext("corp_name") or "").strip()
            stock_code = (item.findtext("stock_code") or "").strip()
            modify_date = (item.findtext("modify_date") or "").strip()

            if not corp_code or not corp_name:
                continue

            is_listed = 1 if stock_code and stock_code.isdigit() and len(stock_code) == 6 else 0
            records.append(
                CorpRecord(
                    corp_code=corp_code,
                    corp_name=corp_name,
                    stock_code=stock_code,
                    modify_date=modify_date,
                    is_listed=is_listed,
                )
            )
        return records

    @staticmethod
    def _format_request_error(exc: requests.RequestException) -> str:
        if isinstance(exc, requests.Timeout):
            return "요청 시간 초과"
        if isinstance(exc, requests.ConnectionError):
            return "네트워크 연결 실패"
        if isinstance(exc, requests.HTTPError):
            status = exc.response.status_code if exc.response is not None else "unknown"
            return f"HTTP {status}"
        return exc.__class__.__name__

    def search(self, name: str) -> str | None:
        """
        회사명으로 corp_code 검색.
        정확 일치 -> 부분 일치(상장 우선) 순으로 조회한다.
        """
        keyword = (name or "").strip()
        if not keyword:
            return None

        row = self.conn.execute(
            "SELECT corp_code FROM corp WHERE corp_name = ?",
            (keyword,),
        ).fetchone()
        if row:
            return str(row[0])

        rows = self.conn.execute(
            """
            SELECT corp_code
            FROM corp
            WHERE corp_name LIKE ?
            ORDER BY is_listed DESC, LENGTH(corp_name) ASC, corp_name ASC
            LIMIT 1
            """,
            (f"%{keyword}%",),
        ).fetchone()
        if rows:
            return str(rows[0])
        return None

    def search_by_stock_code(self, stock_code: str) -> str | None:
        normalized = (stock_code or "").strip()
        if not normalized:
            return None
        row = self.conn.execute(
            "SELECT corp_code FROM corp WHERE stock_code = ?",
            (normalized,),
        ).fetchone()
        if row:
            return str(row[0])
        return None

    def get_listed_corps(self) -> list[dict[str, str]]:
        rows = self.conn.execute(
            """
            SELECT corp_code, corp_name, stock_code
            FROM corp
            WHERE is_listed = 1
            ORDER BY corp_name ASC
            """
        ).fetchall()
        return [
            {
                "corp_code": str(row[0]),
                "corp_name": str(row[1]),
                "stock_code": str(row[2]),
            }
            for row in rows
        ]

    def refresh(self, api_key: str) -> int:
        self.conn.execute("DELETE FROM corp")
        self.conn.commit()
        return self.build_from_dart(api_key)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CorpCodeDB":
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        self.close()
