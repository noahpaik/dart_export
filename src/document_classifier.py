"""DART document classifier module (Track B fallback)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class DocumentClassifierError(RuntimeError):
    """Raised when document classification fails."""


@dataclass
class DartDocument:
    path: Path
    doc_type: str
    fs_type: str | None
    confidence: float
    reason: str = ""


class DocumentClassifier:
    """
    Classify DART extracted HTML files.
    """

    SUPPORTED_EXTENSIONS = {".html", ".htm", ".xml"}

    NOTES_SCORE_THRESHOLD = 0.6
    FS_BODY_SCORE_THRESHOLD = 0.7
    TABLE_DENSITY_MIN_TABLES = 3

    NOTES_KEYWORDS: dict[str, float] = {
        "재무제표에 대한 주석": 1.0,
        "재무제표 주석": 1.0,
        "재무제표주석": 1.0,
        "주석": 0.6,
        "중요한 회계정책": 0.8,
        "유의적인 회계정책": 0.8,
        "영업부문": 0.5,
        "수익의 분해": 0.5,
        "판매비와관리비": 0.5,
        "판관비": 0.5,
    }

    EXCLUDE_KEYWORDS: dict[str, str] = {
        "감사보고서": "audit",
        "내부회계관리": "audit",
        "사업의 내용": "business",
        "이사의 경영진단": "business",
        "목 차": "toc",
        "주주총회": "toc",
    }

    FS_BODY_KEYWORDS = (
        "재무상태표",
        "손익계산서",
        "포괄손익계산서",
        "현금흐름표",
        "자본변동표",
    )

    COMPANY_PROFILE_OVERRIDES: dict[str, str] = {
        "삼성전자": "manufacturing",
        "SK하이닉스": "manufacturing",
        "현대자동차": "manufacturing",
        "LG전자": "manufacturing",
        "POSCO홀딩스": "manufacturing",
        "KB금융": "finance",
        "신한금융지주": "finance",
        "하나금융지주": "finance",
        "우리금융지주": "finance",
        "메리츠금융지주": "finance",
        "삼성생명": "finance",
        "삼성화재": "finance",
    }
    FINANCE_NAME_MARKERS = ("금융", "은행", "카드", "캐피탈", "보험", "증권", "리츠")
    MANUFACTURING_NAME_MARKERS = ("전자", "하이닉스", "자동차", "중공업", "철강", "화학")

    NOTES_SCORE_THRESHOLD_BY_PROFILE: dict[str, float] = {
        "general": NOTES_SCORE_THRESHOLD,
        "manufacturing": NOTES_SCORE_THRESHOLD,
        "finance": 0.55,
    }
    FS_BODY_SCORE_THRESHOLD_BY_PROFILE: dict[str, float] = {
        "general": FS_BODY_SCORE_THRESHOLD,
        "manufacturing": FS_BODY_SCORE_THRESHOLD,
        "finance": 0.6,
    }
    EXTRA_NOTES_KEYWORDS_BY_PROFILE: dict[str, dict[str, float]] = {
        "general": {},
        "manufacturing": {
            "매출실적": 0.2,
            "수주상황": 0.2,
        },
        "finance": {
            "순이자수익": 0.35,
            "순수수료수익": 0.35,
            "보험영업수익": 0.35,
            "대손충당금": 0.3,
            "신용손실충당금": 0.3,
            "자본적정성": 0.2,
        },
    }

    def __init__(self, company_name: str | None = None):
        self.company_name = (company_name or "").strip()
        self.profile = self._resolve_profile(self.company_name)

    def classify_documents(self, html_files: list[Path]) -> list[DartDocument]:
        docs = []
        for path in html_files:
            if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                continue
            docs.append(self._classify_single(path))
        return docs

    def find_notes_files(self, html_files: list[Path]) -> list[DartDocument]:
        docs = self.classify_documents(html_files)
        notes = [doc for doc in docs if doc.doc_type == "notes"]
        notes.sort(key=lambda x: x.confidence, reverse=True)

        if notes:
            return notes
        return self._fallback_by_table_density(html_files)

    def _classify_single(self, html_path: Path) -> DartDocument:
        content = self._read_html_text(html_path)
        if not content:
            return DartDocument(path=html_path, doc_type="other", fs_type=None, confidence=0.0, reason="empty")

        title = self._extract_title(content)
        text = self._extract_text(content[:15000])
        search_text = f"{title} {text}"
        text_head = search_text[:6000]
        score = 0.0
        matched: list[str] = []
        score += self._score_keywords(text_head, self.NOTES_KEYWORDS, matched)
        score += self._score_keywords(
            text_head,
            self.EXTRA_NOTES_KEYWORDS_BY_PROFILE.get(self.profile, {}),
            matched,
        )
        notes_threshold = self.NOTES_SCORE_THRESHOLD_BY_PROFILE.get(self.profile, self.NOTES_SCORE_THRESHOLD)
        fs_body_threshold = self.FS_BODY_SCORE_THRESHOLD_BY_PROFILE.get(self.profile, self.FS_BODY_SCORE_THRESHOLD)

        has_note_numbering = bool(
            re.search(r"(?:^|[\s>])\d+\.\s*(일반사항|회계정책|종속기업|현금)", text_head)
        )
        is_fs_body = any(keyword in text_head for keyword in self.FS_BODY_KEYWORDS)
        fs_type = self._detect_fs_type(text_head)

        for keyword, doc_type in self.EXCLUDE_KEYWORDS.items():
            if keyword not in title and keyword not in text_head:
                continue

            # 감사보고서는 재무제표 주석을 포함하는 경우가 있어 notes 판정을 허용한다.
            if doc_type == "audit":
                has_explicit_notes = any(
                    marker in text_head for marker in ("재무제표에 대한 주석", "재무제표 주석", "재무제표주석")
                )
                if has_explicit_notes or has_note_numbering or score >= 1.0:
                    break

            return DartDocument(
                path=html_path,
                doc_type=doc_type,
                fs_type=None,
                confidence=0.8,
                reason=f"exclude:{keyword}",
            )

        if is_fs_body and score < fs_body_threshold and not has_note_numbering:
            return DartDocument(
                path=html_path,
                doc_type="financial_statements",
                fs_type=fs_type,
                confidence=0.7,
                reason="fs_body",
            )

        if score >= notes_threshold or has_note_numbering:
            reason = f"keywords:{','.join(matched[:3])}" if matched else "note_numbering"
            if self.profile != "general":
                reason = f"profile:{self.profile};{reason}"
            return DartDocument(
                path=html_path,
                doc_type="notes",
                fs_type=fs_type,
                confidence=min(1.0, 0.4 + score / 3),
                reason=reason,
            )

        return DartDocument(
            path=html_path,
            doc_type="other",
            fs_type=fs_type,
            confidence=0.3,
            reason="low_score",
        )

    def _fallback_by_table_density(self, html_files: list[Path]) -> list[DartDocument]:
        candidates: list[tuple[Path, int]] = []
        for path in html_files:
            if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                continue
            content = self._read_html_text(path)
            if not content:
                continue
            candidates.append((path, content.lower().count("<table")))

        if not candidates:
            return []
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_path, top_tables = candidates[0]
        if top_tables < self.TABLE_DENSITY_MIN_TABLES:
            return []
        return [
            DartDocument(
                path=top_path,
                doc_type="notes",
                fs_type=None,
                confidence=0.4,
                reason=f"table_density:{top_tables}",
            )
        ]

    @staticmethod
    def _read_html_text(path: Path) -> str:
        for encoding in ("utf-8", "euc-kr", "cp949"):
            try:
                return path.read_text(encoding=encoding, errors="ignore")
            except OSError:
                continue
        return ""

    @staticmethod
    def _extract_title(html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return re.sub(r"\s+", " ", match.group(1)).strip()

    @staticmethod
    def _extract_text(html: str) -> str:
        text = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style.*?>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _detect_fs_type(text: str) -> str | None:
        head = text[:4000]
        if "연결" in head:
            return "consolidated"
        if "별도" in head or "개별" in head:
            return "separate"
        return None

    @classmethod
    def _resolve_profile(cls, company_name: str) -> str:
        normalized = re.sub(r"\s+", "", str(company_name or ""))
        if not normalized:
            return "general"
        if normalized in cls.COMPANY_PROFILE_OVERRIDES:
            return cls.COMPANY_PROFILE_OVERRIDES[normalized]
        if any(marker in normalized for marker in cls.FINANCE_NAME_MARKERS):
            return "finance"
        if any(marker in normalized for marker in cls.MANUFACTURING_NAME_MARKERS):
            return "manufacturing"
        return "general"

    @staticmethod
    def _score_keywords(text: str, keywords: dict[str, float], matched: list[str]) -> float:
        score = 0.0
        for keyword, weight in keywords.items():
            if keyword in text:
                score += weight
                matched.append(keyword)
        return score
