"""HTML notes parser module (Track B fallback)."""

from __future__ import annotations

import io
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning


class HTMLParserError(RuntimeError):
    """Raised when html notes parsing fails."""


@dataclass
class NoteTable:
    note_type: str
    note_title: str
    df: pd.DataFrame
    source_path: Path
    unit_multiplier: float = 1.0


class HTMLParser:
    """
    Parse note tables from DART HTML documents.
    """

    TARGET_NOTES: dict[str, list[str]] = {
        "segment_revenue": ["영업부문", "부문별", "보고부문", "사업부문", "segment"],
        "sga_detail": ["판매비와관리비", "판관비", "selling general administrative"],
        "depreciation": ["유형자산", "감가상각"],
        "revenue_detail": ["수익의 분해", "매출 구성", "제품별 매출", "revenue disaggregation"],
        "employee": ["종업원", "임직원", "인건비", "employee"],
        "rnd": ["연구개발", "경상연구", "r&d"],
        "capex": ["투자활동", "유형자산의 취득", "설비투자", "capex"],
    }

    def parse_notes(self, html_path: str | Path) -> list[NoteTable]:
        path = Path(html_path)
        if not path.exists() or not path.is_file():
            raise HTMLParserError(f"유효한 HTML 파일이 아닙니다: {path}")

        html = self._read_html(path)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(html, "lxml")
        tables = soup.find_all("table")
        if not tables:
            return []

        result: list[NoteTable] = []
        for table_tag in tables:
            title = self._find_table_title(table_tag)
            note_type = self._classify_note_type(title)
            if note_type is None:
                continue

            try:
                parsed_df = pd.read_html(io.StringIO(str(table_tag)), flavor="lxml")[0]
            except ValueError:
                continue
            except Exception:
                continue

            unit_multiplier = self._detect_unit_multiplier(title)
            cleaned = self._clean_dataframe(parsed_df, unit_multiplier)
            if cleaned.empty:
                continue

            result.append(
                NoteTable(
                    note_type=note_type,
                    note_title=title,
                    df=cleaned,
                    source_path=path,
                    unit_multiplier=unit_multiplier,
                )
            )
        return result

    def _read_html(self, path: Path) -> str:
        for encoding in ("utf-8", "euc-kr", "cp949"):
            try:
                return path.read_text(encoding=encoding, errors="ignore")
            except OSError:
                continue
        raise HTMLParserError(f"HTML 파일 읽기 실패: {path}")

    def _find_table_title(self, table_tag: Tag) -> str:
        texts = []
        cursor = table_tag
        for _ in range(6):
            cursor = cursor.previous_sibling
            if cursor is None:
                break
            if isinstance(cursor, Tag):
                text = cursor.get_text(" ", strip=True)
            else:
                text = str(cursor).strip()
            text = re.sub(r"\s+", " ", text).strip()
            if text and len(text) <= 200:
                texts.append(text)
                if len(texts) >= 2:
                    break
        if not texts:
            return ""
        texts.reverse()
        return " | ".join(texts)

    def _classify_note_type(self, title: str) -> str | None:
        title_l = str(title or "").lower()

        # 부문 관련 표는 '매출/수익' 키워드가 함께 있을 때만 매출 표로 간주한다.
        segment_keywords = self.TARGET_NOTES.get("segment_revenue", [])
        has_segment_keyword = any(keyword.lower() in title_l for keyword in segment_keywords)
        has_revenue_keyword = any(keyword in title_l for keyword in ("매출", "수익", "revenue", "sales"))
        if has_segment_keyword and has_revenue_keyword:
            return "segment_revenue"

        for note_type, keywords in self.TARGET_NOTES.items():
            if note_type == "segment_revenue":
                continue
            for keyword in keywords:
                if keyword.lower() in title_l:
                    return note_type
        return None

    @staticmethod
    def _detect_unit_multiplier(text: str) -> float:
        lowered = str(text or "").lower()
        if "단위" not in lowered and "unit" not in lowered:
            return 1.0
        if "억원" in lowered:
            return 100_000_000.0
        if "백만원" in lowered:
            return 1_000_000.0
        if "천원" in lowered:
            return 1_000.0
        if "원" in lowered:
            return 1.0
        return 1.0

    def _clean_dataframe(self, df: pd.DataFrame, unit_multiplier: float) -> pd.DataFrame:
        if isinstance(df.columns, pd.MultiIndex):
            flat_cols = []
            for col_tuple in df.columns.to_flat_index():
                pieces = [str(x).strip() for x in col_tuple if str(x).strip() and str(x).strip().lower() != "nan"]
                flat_cols.append(" ".join(pieces) if pieces else "col")
            df.columns = flat_cols

        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")
        if df.empty:
            return df

        # normalize first column as account name
        first_col = df.columns[0]
        df[first_col] = df[first_col].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        df = df[df[first_col].str.len() > 0]
        if df.empty:
            return df

        # normalize numeric columns
        for col in df.columns[1:]:
            df[col] = df[col].apply(self._parse_numeric_like)
            if unit_multiplier != 1.0:
                df[col] = df[col].apply(
                    lambda x: x * unit_multiplier if isinstance(x, (int, float)) and pd.notna(x) else x
                )

        return df.reset_index(drop=True)

    @staticmethod
    def _parse_numeric_like(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if text in {"", "-", "nan", "None"}:
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
            .replace("%", "")
        )
        try:
            number = float(cleaned)
        except ValueError:
            return value
        return -number if negative else number
