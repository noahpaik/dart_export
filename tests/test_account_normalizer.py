import json
import re
import tempfile
import unittest
from pathlib import Path

from src.account_normalizer import AccountNormalizer, AccountNormalizerError
from src.cache_db import CacheDB


class FakeLLMClient:
    def __init__(self) -> None:
        self.calls = 0
        self.last_names: list[str] = []

    def chat(self, prompt: str) -> str:
        self.calls += 1
        self.last_names = self._extract_json_array(prompt, "원본 계정명:")
        standards = self._extract_json_array(prompt, "표준 계정명:")
        target = "기타부문" if "기타부문" in standards else (standards[0] if standards else "기타")
        return json.dumps({name: target for name in self.last_names}, ensure_ascii=False)

    @staticmethod
    def _extract_json_array(prompt: str, key: str) -> list[str]:
        match = re.search(rf"{re.escape(key)}\s*(\[[^\n]+\])", str(prompt))
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [str(x) for x in payload if str(x).strip()]


class AccountNormalizerTests(unittest.TestCase):
    def _build_normalizer(
        self,
        cache_db_path: Path,
        llm_client: object | None = None,
        llm_min_unmapped_count: int = 2,
        llm_max_unmapped_count: int = 12,
    ) -> AccountNormalizer:
        taxonomy_path = Path(__file__).resolve().parents[1] / "config" / "taxonomy.yaml"
        cache = CacheDB(str(cache_db_path))
        self.addCleanup(cache.close)
        return AccountNormalizer(
            taxonomy_path=str(taxonomy_path),
            cache_db=cache,
            llm_client=llm_client,
            llm_min_unmapped_count=llm_min_unmapped_count,
            llm_max_unmapped_count=llm_max_unmapped_count,
        )

    def test_sga_detail_aliases_map_to_standard_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            mapping = normalizer.normalize(
                note_type="sga_detail",
                account_names=["급 여", "운반보관비", "판매수수료", "합 계"],
                corp_code="00126380",
                use_cache=False,
            )
            self.assertEqual(mapping["급 여"], "급여")
            self.assertEqual(mapping["운반보관비"], "운반비")
            self.assertEqual(mapping["판매수수료"], "지급수수료")
            self.assertEqual(mapping["합 계"], "총계")

    def test_sga_detail_unknown_falls_back_to_default_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            mapping = normalizer.normalize(
                note_type="sga_detail",
                account_names=["정체불명비용항목"],
                corp_code="00126380",
                use_cache=False,
            )
            self.assertEqual(mapping["정체불명비용항목"], "기타판관비")

    def test_segment_revenue_aliases_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            mapping = normalizer.normalize(
                note_type="segment_revenue",
                account_names=["기 타", "합 계", "DX부문", "HARMAN"],
                corp_code="00126380",
                use_cache=False,
            )
            self.assertEqual(mapping["기 타"], "기타부문")
            self.assertEqual(mapping["합 계"], "합계")
            self.assertEqual(mapping["DX부문"], "DX 부문")
            self.assertEqual(mapping["HARMAN"], "Harman")

    def test_segment_revenue_unknown_keeps_raw_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            mapping = normalizer.normalize(
                note_type="segment_revenue",
                account_names=["AI사업부문"],
                corp_code="00126380",
                use_cache=False,
            )
            self.assertEqual(mapping["AI사업부문"], "AI사업부문")

    def test_cache_policy_read_only_does_not_write_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            mapping = normalizer.normalize(
                note_type="sga_detail",
                account_names=["정체불명비용항목"],
                corp_code="00126380",
                cache_policy="read_only",
            )
            self.assertEqual(mapping["정체불명비용항목"], "기타판관비")
            self.assertEqual(normalizer.cache.stats()["total_rows"], 0)

    def test_cache_policy_bypass_ignores_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            corp_code = "00126380"
            names = ["AI사업부문"]
            cache_key = normalizer._make_cache_key(corp_code, "segment_revenue", names)
            normalizer.cache.set(
                cache_key=cache_key,
                mapping={"AI사업부문": "합계"},
                corp_code=corp_code,
                note_type="segment_revenue",
            )

            mapping = normalizer.normalize(
                note_type="segment_revenue",
                account_names=names,
                corp_code=corp_code,
                cache_policy="bypass",
            )
            self.assertEqual(mapping["AI사업부문"], "AI사업부문")

    def test_cache_policy_read_only_reads_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            corp_code = "00126380"
            names = ["AI사업부문"]
            cache_key = normalizer._make_cache_key(corp_code, "segment_revenue", names)
            normalizer.cache.set(
                cache_key=cache_key,
                mapping={"AI사업부문": "합계"},
                corp_code=corp_code,
                note_type="segment_revenue",
            )

            mapping = normalizer.normalize(
                note_type="segment_revenue",
                account_names=names,
                corp_code=corp_code,
                cache_policy="read_only",
            )
            self.assertEqual(mapping["AI사업부문"], "합계")

    def test_invalid_cache_policy_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            normalizer = self._build_normalizer(Path(tmp_dir) / "cache.db")
            with self.assertRaises(AccountNormalizerError):
                normalizer.normalize(
                    note_type="sga_detail",
                    account_names=["급여"],
                    corp_code="00126380",
                    cache_policy="invalid",
                )

    def test_llm_called_only_after_unmapped_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            llm = FakeLLMClient()
            normalizer = self._build_normalizer(
                Path(tmp_dir) / "cache.db",
                llm_client=llm,
                llm_min_unmapped_count=2,
                llm_max_unmapped_count=12,
            )

            mapping_1 = normalizer.normalize(
                note_type="segment_revenue",
                account_names=["신규A"],
                corp_code="00126380",
                use_cache=False,
            )
            self.assertEqual(llm.calls, 0)
            self.assertEqual(mapping_1["신규A"], "신규A")

            mapping_2 = normalizer.normalize(
                note_type="segment_revenue",
                account_names=["신규A", "신규B"],
                corp_code="00126380",
                use_cache=False,
            )
            self.assertEqual(llm.calls, 1)
            self.assertEqual(mapping_2["신규A"], "기타부문")
            self.assertEqual(mapping_2["신규B"], "기타부문")

    def test_llm_max_unmapped_limit_applies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            llm = FakeLLMClient()
            normalizer = self._build_normalizer(
                Path(tmp_dir) / "cache.db",
                llm_client=llm,
                llm_min_unmapped_count=1,
                llm_max_unmapped_count=2,
            )
            mapping = normalizer.normalize(
                note_type="segment_revenue",
                account_names=["신규A", "신규B", "신규C"],
                corp_code="00126380",
                use_cache=False,
            )
            self.assertEqual(llm.calls, 1)
            self.assertEqual(len(llm.last_names), 2)
            self.assertEqual(mapping["신규A"], "기타부문")
            self.assertEqual(mapping["신규B"], "기타부문")
            self.assertEqual(mapping["신규C"], "신규C")

    def test_usage_tracks_cache_and_llm_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            llm = FakeLLMClient()
            normalizer = self._build_normalizer(
                Path(tmp_dir) / "cache.db",
                llm_client=llm,
                llm_min_unmapped_count=2,
                llm_max_unmapped_count=12,
            )
            corp_code = "00126380"
            names = ["신규A", "신규B"]

            normalizer.normalize(
                note_type="segment_revenue",
                account_names=names,
                corp_code=corp_code,
                cache_policy="read_write",
            )
            normalizer.normalize(
                note_type="segment_revenue",
                account_names=names,
                corp_code=corp_code,
                cache_policy="read_write",
            )

            usage = normalizer.usage()
            self.assertEqual(usage.normalize_calls, 2)
            self.assertEqual(usage.cache_read_attempts, 2)
            self.assertEqual(usage.cache_hits, 1)
            self.assertEqual(usage.cache_misses, 1)
            self.assertEqual(usage.cache_writes, 1)
            self.assertEqual(usage.llm_calls, 1)
            self.assertEqual(usage.llm_target_names, 2)


if __name__ == "__main__":
    unittest.main()
