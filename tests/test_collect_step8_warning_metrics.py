import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent / "collect_step8_warning_metrics.py"
SPEC = importlib.util.spec_from_file_location("collect_step8_warning_metrics", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CollectStep8WarningMetricsTests(unittest.TestCase):
    def test_build_metrics_report_aggregates_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            a_dir = root / "a"
            b_dir = root / "b"
            a_dir.mkdir(parents=True, exist_ok=True)
            b_dir.mkdir(parents=True, exist_ok=True)

            summary_a = {
                "company_name": "삼성전자",
                "corp_code": "00126380",
                "years": ["2024"],
                "report_code": "11011",
                "track_c": {"mode": "parsed"},
                "track_b_fallback": {"mode": "parsed"},
                "warnings": ["Track B 파싱 실패(a.xml): err"],
                "metrics": {
                    "runtime_ms": 1000,
                    "warning_types": {"track_b_parse": 1},
                    "normalizer": {"cache_hit_rate": 0.5},
                },
            }
            summary_b = {
                "company_name": "SK하이닉스",
                "corp_code": "00164779",
                "years": ["2023", "2024"],
                "report_code": "11012",
                "track_c": {"mode": "parsed"},
                "track_b_fallback": {"mode": "skipped(single_segment_notice)"},
                "warnings": [],
                "metrics": {
                    "runtime_ms": 2000,
                    "warning_types": {"track_c_parse": 2},
                    "normalizer": {"cache_hit_rate": None},
                },
            }

            (a_dir / "samsung_11011_step8_summary.json").write_text(
                json.dumps(summary_a, ensure_ascii=False),
                encoding="utf-8",
            )
            (b_dir / "sk_11012_step8_summary.json").write_text(
                json.dumps(summary_b, ensure_ascii=False),
                encoding="utf-8",
            )

            report = MODULE.build_metrics_report(root)
            totals = report["totals"]

            self.assertEqual(totals["run_count"], 2)
            self.assertEqual(totals["warning_count"], 1)
            self.assertEqual(totals["runtime"]["sum_ms"], 3000)
            self.assertEqual(totals["warning_types"]["track_b_parse"], 1)
            self.assertEqual(totals["warning_types"]["track_c_parse"], 2)
            self.assertEqual(totals["report_codes"]["11011"], 1)
            self.assertEqual(totals["report_codes"]["11012"], 1)
            self.assertEqual(totals["year_sets"]["2024"], 1)
            self.assertEqual(totals["year_sets"]["2023,2024"], 1)

    def test_render_markdown_contains_sections(self) -> None:
        report = {
            "generated_at": "2026-02-24T00:00:00+00:00",
            "summary_root": "/tmp/x",
            "totals": {
                "run_count": 1,
                "warning_count": 0,
                "runtime": {"count": 1, "avg_ms": 10, "min_ms": 10, "max_ms": 10},
                "warning_types": {"other": 1},
                "track_c_modes": {"parsed": 1},
                "track_b_modes": {"parsed": 1},
                "report_codes": {"11011": 1},
                "year_sets": {"2024": 1},
                "parse_failures": [],
            },
        }
        text = MODULE.render_markdown(report)
        self.assertIn("# Step8 Warning Metrics Report", text)
        self.assertIn("## Warning Types", text)
        self.assertIn("`other`: 1", text)


if __name__ == "__main__":
    unittest.main()
