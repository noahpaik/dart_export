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
            "runs": [
                {"report_code": "11012", "warning_count": 1, "runtime_ms": 8800},
                {"report_code": "11013", "warning_count": 1, "runtime_ms": 9000},
                {"report_code": "11014", "warning_count": 1, "runtime_ms": 9200},
            ],
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
            "runs": [
                {"report_code": "11012", "warning_count": 2, "runtime_ms": 9100},
                {"report_code": "11013", "warning_count": 2, "runtime_ms": 9350},
                {"report_code": "11014", "warning_count": 1, "runtime_ms": 9450},
            ],
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
        self.assertEqual(report["deltas"]["warning_count_by_report_code"]["11012"], -1)
        self.assertEqual(report["deltas"]["runtime_avg_ms_by_report_code"]["11012"], -300.0)

    def test_build_trend_report_prefers_profile_matched_previous(self) -> None:
        current_payload = {
            "generated_at": "2026-02-25T12:00:00+00:00",
            "totals": {
                "run_count": 3,
                "warning_count": 1,
                "runtime": {"avg_ms": 7000, "max_ms": 9000},
                "warning_types": {"empty_timeseries": 1},
                "track_c_modes": {"parsed": 3},
                "track_b_modes": {"parsed": 2, "skipped(single_segment_notice)": 1},
                "report_codes": {"11011": 3},
                "year_sets": {"2022,2023,2024": 3},
            },
            "runs": [
                {"report_code": "11011", "warning_count": 1, "runtime_ms": 7000},
            ],
        }
        previous_mismatch_payload = {
            "generated_at": "2026-02-24T12:00:00+00:00",
            "totals": {
                "run_count": 9,
                "warning_count": 2,
                "runtime": {"avg_ms": 6000, "max_ms": 10000},
                "warning_types": {"empty_timeseries": 2},
                "track_c_modes": {"parsed": 9},
                "track_b_modes": {"parsed": 6, "skipped(single_segment_notice)": 3},
                "report_codes": {"11012": 3, "11013": 3, "11014": 3},
                "year_sets": {"2022,2023,2024": 9},
            },
            "runs": [
                {"report_code": "11012", "warning_count": 1, "runtime_ms": 6000},
                {"report_code": "11013", "warning_count": 1, "runtime_ms": 6000},
                {"report_code": "11014", "warning_count": 0, "runtime_ms": 6000},
            ],
        }
        previous_match_payload = {
            "generated_at": "2026-02-23T12:00:00+00:00",
            "totals": {
                "run_count": 3,
                "warning_count": 3,
                "runtime": {"avg_ms": 9000, "max_ms": 12000},
                "warning_types": {"empty_timeseries": 3},
                "track_c_modes": {"parsed": 3},
                "track_b_modes": {"parsed": 2, "skipped(single_segment_notice)": 1},
                "report_codes": {"11011": 3},
                "year_sets": {"2022,2023,2024": 3},
            },
            "runs": [
                {"report_code": "11011", "warning_count": 3, "runtime_ms": 9000},
            ],
        }
        report = MODULE.build_trend_report(
            current_payload=current_payload,
            current_source="/tmp/current.json",
            history_entries=[
                {
                    "payload": previous_mismatch_payload,
                    "source": "/tmp/prev_mismatch.json",
                    "run_id": 11,
                    "artifact_id": 111,
                    "fallback_generated_at": None,
                },
                {
                    "payload": previous_match_payload,
                    "source": "/tmp/prev_match.json",
                    "run_id": 12,
                    "artifact_id": 112,
                    "fallback_generated_at": None,
                },
            ],
            recent_runs=5,
            fetch_summary={"enabled": False, "errors": [], "fetched_runs": 0, "considered_artifacts": 0},
            parse_failures=[],
        )
        self.assertEqual(report["previous_selection_mode"], "profile_match")
        self.assertEqual(report["previous"]["source"], "/tmp/prev_match.json")
        self.assertEqual(report["deltas"]["warning_count"], -2)

    def test_evaluate_quality_gate_fail_when_delta_exceeds_threshold(self) -> None:
        report = {
            "previous": {"warning_count": 1},
            "deltas": {"warning_count": 4, "runtime_avg_ms": 1200.0},
        }
        gate = MODULE.evaluate_quality_gate(
            report,
            max_warning_delta=2,
            max_runtime_avg_ms_delta=1500.0,
            require_previous=False,
        )
        self.assertEqual(gate["status"], "fail")
        self.assertTrue(any("warning_count_delta=4 > 2" in item for item in gate["violations"]))

    def test_evaluate_quality_gate_skipped_without_previous(self) -> None:
        report = {"previous": {}, "deltas": {"warning_count": None, "runtime_avg_ms": None}}
        gate = MODULE.evaluate_quality_gate(
            report,
            max_warning_delta=2,
            max_runtime_avg_ms_delta=1000.0,
            require_previous=False,
        )
        self.assertEqual(gate["status"], "skipped")
        self.assertEqual(gate["violations"], [])

    def test_evaluate_quality_gate_skipped_when_min_run_count_not_met(self) -> None:
        report = {
            "latest": {"run_count": 2},
            "previous": {"run_count": 2},
            "deltas": {"warning_count": 10, "runtime_avg_ms": 9999.0},
        }
        gate = MODULE.evaluate_quality_gate(
            report,
            max_warning_delta=2,
            max_runtime_avg_ms_delta=1000.0,
            require_previous=False,
            min_run_count=3,
        )
        self.assertEqual(gate["status"], "skipped")
        self.assertEqual(gate["violations"], [])
        self.assertTrue(any("min_run_count_not_met" in item for item in gate["skip_reasons"]))

    def test_evaluate_quality_gate_report_code_thresholds(self) -> None:
        report = {
            "latest": {"report_codes": {"11011": 3}},
            "previous": {"report_codes": {"11011": 3}},
            "deltas": {
                "warning_count": 1,
                "runtime_avg_ms": 1000.0,
                "warning_count_by_report_code": {"11011": 3},
                "runtime_avg_ms_by_report_code": {"11011": 5200.0},
            },
        }
        gate = MODULE.evaluate_quality_gate(
            report,
            max_warning_delta=None,
            max_runtime_avg_ms_delta=None,
            require_previous=False,
            max_warning_delta_by_report_code={"11011": 2},
            max_runtime_avg_ms_delta_by_report_code={"11011": 5000.0},
        )
        self.assertEqual(gate["status"], "fail")
        self.assertTrue(any("report_code=11011 warning_count_delta=3 > 2" in item for item in gate["violations"]))
        self.assertTrue(any("report_code=11011 runtime_avg_ms_delta=5200.0 > 5000.0" in item for item in gate["violations"]))

    def test_evaluate_quality_gate_report_code_thresholds_with_min_samples(self) -> None:
        report = {
            "latest": {"report_codes": {"11011": 1}},
            "previous": {"report_codes": {"11011": 1}},
            "deltas": {
                "warning_count_by_report_code": {"11011": 3},
                "runtime_avg_ms_by_report_code": {"11011": 5200.0},
            },
        }
        gate = MODULE.evaluate_quality_gate(
            report,
            max_warning_delta=None,
            max_runtime_avg_ms_delta=None,
            require_previous=False,
            min_run_count_by_report_code=2,
            max_warning_delta_by_report_code={"11011": 2},
            max_runtime_avg_ms_delta_by_report_code={"11011": 5000.0},
        )
        self.assertEqual(gate["status"], "pass")
        self.assertEqual(gate["violations"], [])

    def test_evaluate_quality_gate_warning_type_threshold_and_ignore(self) -> None:
        report = {
            "previous": {"warning_count": 1},
            "deltas": {
                "warning_count": 1,
                "runtime_avg_ms": 100.0,
                "warning_types": {
                    "empty_timeseries": 3,
                    "single_segment_notice": 10,
                },
            },
        }
        gate = MODULE.evaluate_quality_gate(
            report,
            max_warning_delta=None,
            max_runtime_avg_ms_delta=None,
            require_previous=False,
            max_warning_type_delta={
                "empty_timeseries": 2,
                "single_segment_notice": 1,
            },
            ignore_warning_types={"single_segment_notice"},
        )
        self.assertEqual(gate["status"], "fail")
        self.assertTrue(any("warning_type=empty_timeseries delta=3 > 2" in item for item in gate["violations"]))
        self.assertFalse(any("single_segment_notice" in item for item in gate["violations"]))

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
            "deltas": {
                "warning_count": None,
                "run_count": None,
                "runtime_avg_ms": None,
                "warning_count_by_report_code": {},
                "runtime_avg_ms_by_report_code": {},
                "warning_types": {},
            },
            "aggregates_recent": {
                "warning_types": {"empty_timeseries": 3},
                "track_c_modes": {"parsed": 9},
                "track_b_modes": {"parsed": 6},
                "warning_count_by_report_code": {"11011": 1},
                "report_codes": {"11011": 3},
                "year_sets": {"2024": 3},
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
            "quality_gate": {
                "enabled": True,
                "status": "pass",
                "thresholds": {
                    "max_warning_delta": 3,
                    "max_runtime_avg_ms_delta": 5000,
                    "require_previous": False,
                    "min_run_count": None,
                    "min_run_count_by_report_code": None,
                    "max_warning_delta_by_report_code": {},
                    "max_runtime_avg_ms_delta_by_report_code": {},
                    "max_warning_type_delta": {},
                    "ignore_warning_types": [],
                },
                "violations": [],
                "skip_reasons": [],
            },
            "quality_gate_config": {
                "path": "config/step8_warning_quality_gate.yaml",
                "loaded": True,
            },
            "parse_failures": [],
            "previous_selection_mode": "none",
        }
        text = MODULE.render_markdown(report)
        self.assertIn("# Step8 Warning Metrics Trend Report", text)
        self.assertIn("## Latest vs Previous", text)
        self.assertIn("## Quality Gate", text)
        self.assertIn("## Recent Points", text)
        self.assertIn("## Delta Warning Count by Report Code", text)
        self.assertIn("## Aggregated Warning Types (Recent)", text)


if __name__ == "__main__":
    unittest.main()
