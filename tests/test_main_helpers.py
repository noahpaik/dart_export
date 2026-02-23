import argparse
import unittest
from pathlib import Path

from main import (
    STEP8_SUMMARY_SCHEMA_VERSION,
    ConfigError,
    build_step8_summary_payload,
    parse_years_option,
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
            warnings=[],
            output_excel=Path("data/삼성전자_2024_model.xlsx"),
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
                "infos",
                "warnings",
                "output_excel",
            },
        )
        self.assertEqual(payload["years"], ["2022", "2023", "2024"])
        self.assertEqual(payload["track_c"]["source"], "document.xml")
        self.assertEqual(payload["writes"][1]["unmatched_accounts"], 1)
        self.assertEqual(
            payload["track_b_fallback"]["single_segment_sources"],
            ["doc_a.xml", "doc_b.xml"],
        )


if __name__ == "__main__":
    unittest.main()
