"""Account normalization module."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from src.cache_db import CacheDB


class AccountNormalizerError(RuntimeError):
    """Raised when account normalization fails."""


class AccountNormalizer:
    """
    Normalize note account names into taxonomy standard accounts.
    """

    def __init__(
        self,
        taxonomy_path: str,
        cache_db: CacheDB,
        llm_client: Any | None = None,
    ):
        self.taxonomy_path = Path(taxonomy_path)
        if not self.taxonomy_path.exists():
            raise AccountNormalizerError(f"taxonomy 파일이 없습니다: {self.taxonomy_path}")
        self.taxonomy = self._load_taxonomy(self.taxonomy_path)
        self.cache = cache_db
        self.llm = llm_client

    def _load_taxonomy(self, path: Path) -> dict[str, Any]:
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise AccountNormalizerError(f"taxonomy 로드 실패: {path}") from exc
        if not isinstance(loaded, dict):
            raise AccountNormalizerError("taxonomy 최상위 구조는 dict여야 합니다.")
        return loaded

    @staticmethod
    def _normalize_name(name: str) -> str:
        text = str(name or "").strip()
        text = re.sub(r"\s+", "", text)
        text = text.replace("(", "").replace(")", "")
        text = text.replace("-", "").replace("_", "")
        return text

    def _make_cache_key(self, corp_code: str, note_type: str, account_names: list[str]) -> str:
        payload = f"{corp_code}:{note_type}:{json.dumps(sorted(account_names), ensure_ascii=False)}"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def normalize(
        self,
        note_type: str,
        account_names: list[str],
        corp_code: str,
        use_cache: bool = True,
    ) -> dict[str, str]:
        if not account_names:
            return {}

        taxonomy_entry = self.taxonomy.get(note_type, {})
        if not isinstance(taxonomy_entry, dict):
            taxonomy_entry = {}

        standard_accounts = [
            str(x).strip()
            for x in taxonomy_entry.get("standard_accounts", [])
            if str(x).strip()
        ]
        aliases_raw = taxonomy_entry.get("aliases", {})
        aliases: dict[str, str] = {}
        if isinstance(aliases_raw, dict):
            aliases = {str(k).strip(): str(v).strip() for k, v in aliases_raw.items()}

        cache_key = self._make_cache_key(corp_code, note_type, [str(n) for n in account_names])
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        normalized_aliases = {self._normalize_name(k): v for k, v in aliases.items()}
        normalized_standard = {self._normalize_name(x): x for x in standard_accounts}

        mapping: dict[str, str] = {}
        unmapped: list[str] = []

        for name in account_names:
            raw = str(name).strip()
            if not raw:
                continue

            raw_norm = self._normalize_name(raw)

            if raw in aliases:
                mapping[raw] = aliases[raw]
                continue
            if raw_norm in normalized_aliases:
                mapping[raw] = normalized_aliases[raw_norm]
                continue

            if raw in standard_accounts:
                mapping[raw] = raw
                continue
            if raw_norm in normalized_standard:
                mapping[raw] = normalized_standard[raw_norm]
                continue

            heuristic = self._heuristic_match(raw, standard_accounts)
            if heuristic:
                mapping[raw] = heuristic
                continue

            unmapped.append(raw)

        if unmapped and self.llm is not None and standard_accounts:
            llm_mapping = self._llm_normalize(note_type, unmapped, standard_accounts)
            mapping.update(llm_mapping)

        for name in unmapped:
            if name in mapping:
                continue
            mapping[name] = self._default_fallback(note_type, standard_accounts, name)

        if use_cache:
            self.cache.set(
                cache_key=cache_key,
                mapping=mapping,
                corp_code=corp_code,
                note_type=note_type,
            )
        return mapping

    def _heuristic_match(self, raw: str, standard_accounts: list[str]) -> str | None:
        if not standard_accounts:
            return None
        raw_n = self._normalize_name(raw)
        for standard in standard_accounts:
            st_n = self._normalize_name(standard)
            if st_n and (st_n in raw_n or raw_n in st_n):
                return standard
        return None

    def _default_fallback(self, note_type: str, standard_accounts: list[str], raw: str) -> str:
        if note_type == "sga_detail":
            if "기타판관비" in standard_accounts:
                return "기타판관비"
            return standard_accounts[-1] if standard_accounts else raw
        if note_type == "revenue_detail":
            if "기타매출" in standard_accounts:
                return "기타매출"
            return standard_accounts[-1] if standard_accounts else raw
        if note_type == "segment_revenue":
            return raw
        return standard_accounts[-1] if standard_accounts else raw

    def _llm_normalize(
        self,
        note_type: str,
        names: list[str],
        standard_accounts: list[str],
    ) -> dict[str, str]:
        prompt = (
            "한국 기업 재무주석 계정명을 표준 계정으로 매핑하세요.\n"
            f"note_type: {note_type}\n"
            f"원본 계정명: {json.dumps(names, ensure_ascii=False)}\n"
            f"표준 계정명: {json.dumps(standard_accounts, ensure_ascii=False)}\n"
            "출력은 JSON dict만 반환하세요. 예: {\"원본\": \"표준\"}"
        )
        try:
            raw = self.llm.chat(prompt)
        except Exception as exc:  # pylint: disable=broad-except
            raise AccountNormalizerError(f"LLM 호출 실패: {exc}") from exc

        parsed = self._parse_json_like(raw)
        result: dict[str, str] = {}
        for name in names:
            mapped = parsed.get(name)
            if mapped is None:
                continue
            mapped_s = str(mapped).strip()
            if mapped_s:
                result[name] = mapped_s
        return result

    @staticmethod
    def _parse_json_like(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if "\n" in raw:
                raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw[:-3]
        raw = raw.strip()
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not match:
                return {}
            try:
                loaded = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        if not isinstance(loaded, dict):
            return {}
        return loaded
