import unittest

from requests.exceptions import ConnectionError, HTTPError, SSLError, Timeout
from requests.models import Response

from src.financial_statements import FinancialStatementFetcher


class FinancialStatementNetworkTests(unittest.TestCase):
    def test_format_request_error_classification(self) -> None:
        self.assertEqual(
            FinancialStatementFetcher._format_request_error(Timeout("slow")),
            "요청 시간 초과",
        )
        self.assertEqual(
            FinancialStatementFetcher._format_request_error(SSLError("tls")),
            "TLS/SSL 연결 실패",
        )
        self.assertEqual(
            FinancialStatementFetcher._format_request_error(
                ConnectionError("NameResolutionError: failed to resolve")
            ),
            "DNS 해석 실패",
        )
        self.assertEqual(
            FinancialStatementFetcher._format_request_error(ConnectionError("network down")),
            "네트워크 연결 실패",
        )

        response = Response()
        response.status_code = 503
        self.assertEqual(
            FinancialStatementFetcher._format_request_error(HTTPError(response=response)),
            "HTTP 503",
        )


if __name__ == "__main__":
    unittest.main()
