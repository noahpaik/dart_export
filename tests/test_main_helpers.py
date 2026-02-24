import argparse
import unittest
from pathlib import Path

from main import (
    STEP8_SUMMARY_SCHEMA_VERSION,
    ConfigError,
    build_step8_summary_payload,
    is_step8_noise_row_label,
    is_step8_total_row_label,
    parse_args,
    parse_years_option,
    summarize_step8_warning_types,
    should_skip_step8_row,
    to_safe_filename_token,
)
from src.excel_writer import WriteSummary


class MainHelperTests(unittest.TestCase):
    def test_parse_years_option_prefers_years(self) -> None:
        settings = {"pipeline": {"years": ["2020", "2021"]}}
        args = argparse.Namespace(years="2022, 2023,2024", year=None)
        self.assertEqual(parse_years_option(args, settings), ["2022", "2023", "2024"])

    def test_parse_years_option_uses_single_year(self) -> None:
        settings = {"pipeline": {"years": ["2020", "2021"]}}
        args = argparse.Namespace(years=None, year="2025")
        self.assertEqual(parse_years_option(args, settings), ["2025"])

    def test_parse_years_option_rejects_invalid_year(self) -> None:
        settings = {"pipeline": {"years": ["2020", "2021"]}}
        args = argparse.Namespace(years="202A", year=None)
        with self.assertRaises(ConfigError):
            parse_years_option(args, settings)

    def test_to_safe_filename_token_normalizes_whitespace_and_symbols(self) -> None:
        self.assertEqual(to_safe_filename_token("  삼성전자 / 2024  "), "삼성전자_2024")
        self.assertEqual(to_safe_filename_token(""), "output")

    def test_build_step8_summary_payload_schema(self) -> None:
        payload = build_step8_summary_payload(
            corp_code="00126380",
            company_name="삼성전자",
            fs_div="CFS",
            years=["2024", "2022", "2023"],
            report_code="11011",
            bs_rows=69,
            is_rows=19,
            cf_rows=36,
            trackc_mode="skipped(no_xbrl_dir)",
            trackc_source="document.xml",
            xbrl_dir=None,
            xbrl_note_count=0,
            sga_accounts_count=0,
            segment_members_count=0,
            segment_mode="parsed",
            latest_rcept_no="20240312000736",
            fallback_doc_count=1,
            fallback_tables=3,
            fallback_sga_rows=8,
            fallback_segment_rows=5,
            single_segment_sources={"doc_b.xml", "doc_a.xml"},
            statement_summaries=[
                WriteSummary("BS", 143, 69, []),
                WriteSummary("판관비", 16, 8, [("기타", "기타")]),
            ],
            infos=["Track C skip"],
            warnings=["Track B 파싱 실패(sample.xml): parse fail"],
            output_excel=Path("data/삼성전자_2024_model.xlsx"),
            metrics={
                "runtime_ms": 1234,
                "warning_types": {"track_b_parse": 1},
                "normalizer": {
                    "normalize_calls": 4,
                    "cache_read_attempts": 2,
                    "cache_hits": 1,
                    "cache_misses": 1,
                    "cache_writes": 1,
                    "cache_hit_rate": 0.5,
                    "llm_calls": 0,
                    "llm_target_names": 0,
                },
            },
        )

        self.assertEqual(payload["schema_version"], STEP8_SUMMARY_SCHEMA_VERSION)
        self.assertEqual(
            set(payload.keys()),
            {
                "schema_version",
                "corp_code",
                "company_name",
                "fs_div",
                "years",
                "report_code",
                "track_a",
                "track_c",
                "track_b_fallback",
                "writes",
                "metrics",
                "infos",
                "warnings",
                "output_excel",
            },
        )
        self.assertEqual(payload["years"], ["2022", "2023", "2024"])
        self.assertEqual(payload["track_c"]["source"], "document.xml")
        self.assertEqual(payload["writes"][1]["unmatched_accounts"], 1)
        self.assertEqual(payload["metrics"]["runtime_ms"], 1234)
        self.assertEqual(
            payload["track_b_fallback"]["single_segment_sources"],
            ["doc_a.xml", "doc_b.xml"],
        )

    def test_total_row_detection(self) -> None:
        self.assertTrue(is_step8_total_row_label("sga_detail", "합 계"))
        self.assertTrue(is_step8_total_row_label("segment_revenue", "소계"))
        self.assertFalse(is_step8_total_row_label("segment_revenue", "기타부문"))

    def test_noise_row_detection(self) -> None:
        self.assertTrue(is_step8_noise_row_label("sga_detail", "(단위 : 백만원)"))
        self.assertTrue(is_step8_noise_row_label("sga_detail", "판매비:"))
        self.assertFalse(is_step8_noise_row_label("sga_detail", "급여"))

    def test_should_skip_row_segment_currency_noise(self) -> None:
        self.assertTrue(should_skip_step8_row("segment_revenue", "USD"))
        self.assertTrue(should_skip_step8_row("segment_revenue", "합 계"))
        self.assertFalse(should_skip_step8_row("segment_revenue", "DX 부문"))

    def test_parse_args_step8_llm_normalizer_flags(self) -> None:
        args = parse_args(
            [
                "--step8-run-pipeline",
                "--step8-enable-llm-normalize",
                "--step8-normalizer-cache-policy",
                "read_only",
                "--step8-llm-max-calls",
                "7",
                "--step8-llm-min-unmapped",
                "3",
                "--step8-llm-max-unmapped",
                "9",
            ]
        )
        self.assertTrue(args.step8_enable_llm_normalize)
        self.assertEqual(args.step8_normalizer_cache_policy, "read_only")
        self.assertEqual(args.step8_llm_max_calls, 7)
        self.assertEqual(args.step8_llm_min_unmapped, 3)
        self.assertEqual(args.step8_llm_max_unmapped, 9)

    def test_summarize_step8_warning_types(self) -> None:
        counts = summarize_step8_warning_types(
            [
                "Track B 파싱 실패(a.xml): err",
                "Track B 파싱 실패(b.xml): err",
                "BS 시계열 데이터가 비어 있어 BS 시트 입력을 건너뜀.",
                "알 수 없는 경고",
            ]
        )
        self.assertEqual(
            counts,
            {
                "empty_timeseries": 1,
                "other": 1,
                "track_b_parse": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
