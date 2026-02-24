import tempfile
import unittest
from pathlib import Path

import pandas as pd

from main import is_step8_segment_revenue_table, is_step8_single_segment_notice
from src.html_parser import HTMLParser


class HTMLParserTests(unittest.TestCase):
    def _write_text(self, base: Path, name: str, content: str) -> Path:
        path = base / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_parse_notes_handles_merged_multi_header_segment_table(self) -> None:
        parser = HTMLParser()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = self._write_text(
                root,
                "segment_table.xml",
                """
                <html><body>
                  <p>영업부문 매출 (단위: 백만원)</p>
                  <table border="1">
                    <tr><th rowspan="2">부문</th><th colspan="2">매출액</th></tr>
                    <tr><th>2024</th><th>2023</th></tr>
                    <tr><td>완성차</td><td>1,234</td><td>1,100</td></tr>
                    <tr><td>금융</td><td>567</td><td>500</td></tr>
                  </table>
                </body></html>
                """,
            )
            notes = parser.parse_notes(path)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].note_type, "segment_revenue")

            df = notes[0].df
            self.assertTrue(any("2024" in str(col) for col in df.columns))
            self.assertTrue(any("2023" in str(col) for col in df.columns))

            col_2024 = next(col for col in df.columns if "2024" in str(col))
            self.assertAlmostEqual(float(df.loc[0, col_2024]), 1_234_000_000.0)

    def test_parse_notes_skips_segment_without_revenue_keyword(self) -> None:
        parser = HTMLParser()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = self._write_text(
                root,
                "segment_no_revenue.html",
                """
                <html><body>
                  <p>영업부문 현황</p>
                  <table border="1">
                    <tr><th>부문</th><th>2024</th><th>2023</th></tr>
                    <tr><td>완성차</td><td>1,234</td><td>1,100</td></tr>
                    <tr><td>금융</td><td>567</td><td>500</td></tr>
                  </table>
                </body></html>
                """,
            )
            notes = parser.parse_notes(path)
            self.assertEqual(notes, [])

    def test_segment_revenue_filter_accepts_multiheader_promoted_rows(self) -> None:
        df = pd.DataFrame(
            {
                0: ["구분", "완성차부문", "금융부문"],
                1: ["2024", 250_000, 180_000],
                2: ["2023", 220_000, 160_000],
            }
        )
        self.assertTrue(
            is_step8_segment_revenue_table(
                note_title="영업부문 매출 정보",
                df=df,
                latest_report_year_hint=2024,
            )
        )

    def test_segment_revenue_filter_rejects_text_comment_table(self) -> None:
        df = pd.DataFrame(
            {
                "구분": ["설명", "비고"],
                "당기": ["전년 대비 감소", "수익성 개선 노력"],
                "전기": ["계절성 영향", "비교 불가"],
            }
        )
        self.assertFalse(
            is_step8_segment_revenue_table(
                note_title="영업부문 매출 주석",
                df=df,
                latest_report_year_hint=2024,
            )
        )

    def test_single_segment_notice_detection(self) -> None:
        df = pd.DataFrame(
            {
                "문구": [
                    "당사는 지배적 단일 사업부문으로 부문별 기재를 생략합니다.",
                    "기타 정보",
                ]
            }
        )
        self.assertTrue(
            is_step8_single_segment_notice(
                note_title="(1) 사업부문별 연결재무정보",
                df=df,
            )
        )


if __name__ == "__main__":
    unittest.main()
