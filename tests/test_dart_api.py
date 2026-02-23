import unittest
import zipfile
from io import BytesIO
from pathlib import Path
import tempfile

from requests.exceptions import ConnectionError, HTTPError, SSLError, Timeout
from requests.models import Response

from src.dart_api import DartAPI, DartAPIError


class DartAPITests(unittest.TestCase):
    def test_matches_report_code(self) -> None:
        self.assertTrue(DartAPI._matches_report_code("사업보고서 (2024.12)", "11011"))
        self.assertTrue(DartAPI._matches_report_code("반기보고서 (2024.06)", "11012"))
        self.assertTrue(DartAPI._matches_report_code("분기보고서 (2024.03)", "11013"))
        self.assertFalse(DartAPI._matches_report_code("분기보고서 (2024.03)", "11014"))
        self.assertTrue(DartAPI._matches_report_code("분기보고서 (2024.09)", "11014"))
        self.assertTrue(DartAPI._matches_report_code("1분기보고서", "11013"))
        self.assertTrue(DartAPI._matches_report_code("문서코드 ABC-999", "999"))

    def test_normalize_validates_inputs(self) -> None:
        self.assertEqual(DartAPI._normalize_year("2024"), "2024")
        self.assertEqual(DartAPI._normalize_corp_code("00126380"), "00126380")
        self.assertEqual(DartAPI._normalize_rcept_no("20240312000736"), "20240312000736")

        with self.assertRaises(DartAPIError):
            DartAPI._normalize_year("24")
        with self.assertRaises(DartAPIError):
            DartAPI._normalize_corp_code("126380")
        with self.assertRaises(DartAPIError):
            DartAPI._normalize_rcept_no("20240312")

    def test_classify_request_error(self) -> None:
        self.assertEqual(
            DartAPI._classify_request_error(Timeout("slow"))[0],
            "timeout",
        )
        self.assertEqual(
            DartAPI._classify_request_error(SSLError("tls"))[0],
            "ssl",
        )
        self.assertEqual(
            DartAPI._classify_request_error(
                ConnectionError("NameResolutionError: failed to resolve")
            )[0],
            "dns",
        )
        self.assertEqual(
            DartAPI._classify_request_error(ConnectionError("network down"))[0],
            "connection",
        )

        response = Response()
        response.status_code = 503
        http_exc = HTTPError(response=response)
        self.assertEqual(DartAPI._classify_request_error(http_exc), ("http", "HTTP 503"))

    def test_get_latest_report_sorts_by_date_then_rcept_no(self) -> None:
        api = DartAPI.__new__(DartAPI)
        api.get_reports = lambda _corp, _year, report_code="11011": [  # type: ignore[attr-defined]
            {"rcept_dt": "20240312", "rcept_no": "20240312000730"},
            {"rcept_dt": "20240312", "rcept_no": "20240312000736"},
            {"rcept_dt": "20240220", "rcept_no": "20240220000001"},
        ]

        latest = DartAPI.get_latest_report(api, "00126380", "2024", "11011")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["rcept_no"], "20240312000736")

    def test_extract_zip_response(self) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, mode="w") as archive:
            archive.writestr("sample_pre.xml", "<xml/>")
            archive.writestr("doc.html", "<html></html>")

        with tempfile.TemporaryDirectory() as tmp_dir:
            files = DartAPI._extract_zip_response(
                content=buffer.getvalue(),
                target_dir=Path(tmp_dir),
                badzip_message="bad zip",
            )
            names = {path.name for path in files}
            self.assertIn("sample_pre.xml", names)
            self.assertIn("doc.html", names)

    def test_extract_zip_response_raises_on_bad_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(DartAPIError):
                DartAPI._extract_zip_response(
                    content=b"not-a-zip",
                    target_dir=Path(tmp_dir),
                    badzip_message="bad zip",
                )


if __name__ == "__main__":
    unittest.main()
