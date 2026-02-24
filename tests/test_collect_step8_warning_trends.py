import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent / "collect_step8_warning_trends.py"
SPEC = importlib.util.spec_from_file_location("collect_step8_warning_trends", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CollectStep8WarningTrendsTests(unittest.TestCase):
    def test_build_trend_report_with_deltas(self) -> None:
        current_payload = {
            "generated_at": "2026-02-24T12:00:00+00:00",
            "totals": {
                "run_count": 9,
                "warning_count": 3,
                "runtime": {"avg_ms": 9000, "max_ms": 15000},
                "warning_types": {"empty_timeseries": 3},
                "track_c_modes": {"parsed": 9},
                "track_b_modes": {"parsed": 6, "skipped(single_segment_notice)": 3},
                "report_codes": {"11012": 3, "11013": 3, "11014": 3},
                "year_sets": {"2022,2023,2024": 9},
            },
        }
        previous_payload = {
            "generated_at": "2026-02-23T12:00:00+00:00",
            "totals": {
                "run_count": 9,
                "warning_count": 5,
                "runtime": {"avg_ms": 9300, "max_ms": 17000},
                "warning_types": {"empty_timeseries": 5},
                "track_c_modes": {"parsed": 9},
                "track_b_modes": {"parsed": 5, "skipped(single_segment_notice)": 4},
                "report_codes": {"11012": 3, "11013": 3, "11014": 3},
                "year_sets": {"2021,2022,2023": 9},
            },
        }
        report = MODULE.build_trend_report(
            current_payload=current_payload,
            current_source="/tmp/current.json",
            history_entries=[
                {
                    "payload": previous_payload,
                    "source": "/tmp/prev.json",
                    "run_id": 123,
                    "artifact_id": 456,
                    "fallback_generated_at": None,
                }
            ],
            recent_runs=5,
            fetch_summary={"enabled": False, "errors": [], "fetched_runs": 0, "considered_artifacts": 0},
            parse_failures=[],
        )
        self.assertEqual(report["point_count_total"], 2)
        self.assertEqual(report["point_count_used"], 2)
        self.assertEqual(report["latest"]["warning_count"], 3)
        self.assertEqual(report["previous"]["warning_count"], 5)
        self.assertEqual(report["deltas"]["warning_count"], -2)
        self.assertEqual(report["deltas"]["runtime_avg_ms"], -300.0)
        self.assertEqual(report["aggregates_recent"]["warning_types"]["empty_timeseries"], 8)

    def test_load_history_entries_tracks_parse_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            valid_path = root / "a_step8_warning_metrics.json"
            bad_path = root / "b_step8_warning_metrics.json"
            valid_path.write_text(
                json.dumps({"generated_at": "2026-02-24T00:00:00+00:00", "totals": {"run_count": 1}}),
                encoding="utf-8",
            )
            bad_path.write_text("{bad-json", encoding="utf-8")

            entries, failures = MODULE.load_history_entries_from_dir(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(len(failures), 1)
            self.assertIn(str(bad_path), failures[0])

    def test_render_markdown_contains_core_sections(self) -> None:
        report = {
            "generated_at": "2026-02-24T12:00:00+00:00",
            "recent_runs_limit": 5,
            "point_count_total": 1,
            "point_count_used": 1,
            "latest": {
                "warning_count": 3,
                "source": "/tmp/current.json",
                "run_count": 9,
                "runtime_avg_ms": 9000.0,
                "generated_at": "2026-02-24T12:00:00+00:00",
            },
            "previous": {},
            "deltas": {"warning_count": None, "run_count": None, "runtime_avg_ms": None},
            "aggregates_recent": {
                "warning_types": {"empty_timeseries": 3},
                "track_c_modes": {"parsed": 9},
                "track_b_modes": {"parsed": 6},
            },
            "points": [
                {
                    "generated_at": "2026-02-24T12:00:00+00:00",
                    "run_count": 9,
                    "warning_count": 3,
                    "runtime_avg_ms": 9000.0,
                    "source": "/tmp/current.json",
                }
            ],
            "fetch": {
                "enabled": True,
                "repo": "owner/repo",
                "artifact_name": "step8-warning-metrics",
                "considered_artifacts": 10,
                "fetched_runs": 4,
                "errors": [],
            },
            "parse_failures": [],
        }
        text = MODULE.render_markdown(report)
        self.assertIn("# Step8 Warning Metrics Trend Report", text)
        self.assertIn("## Latest vs Previous", text)
        self.assertIn("## Recent Points", text)
        self.assertIn("## Aggregated Warning Types (Recent)", text)


if __name__ == "__main__":
    unittest.main()
