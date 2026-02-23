import tempfile
import unittest
from pathlib import Path

from src.document_classifier import DocumentClassifier


class DocumentClassifierTests(unittest.TestCase):
    def _write_text(self, base: Path, name: str, content: str) -> Path:
        path = base / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_audit_document_with_explicit_notes_is_classified_as_notes(self) -> None:
        classifier = DocumentClassifier()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = self._write_text(
                root,
                "audit_with_notes.xml",
                """
                <html>
                  <title>감사보고서</title>
                  <body>재무제표에 대한 주석 1. 일반사항</body>
                </html>
                """,
            )
            doc = classifier.classify_documents([path])[0]
            self.assertEqual(doc.doc_type, "notes")

    def test_business_document_is_classified_as_business(self) -> None:
        classifier = DocumentClassifier()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = self._write_text(
                root,
                "business.html",
                """
                <html>
                  <title>사업의 내용</title>
                  <body>사업의 내용 및 시장위험</body>
                </html>
                """,
            )
            doc = classifier.classify_documents([path])[0]
            self.assertEqual(doc.doc_type, "business")

    def test_find_notes_files_falls_back_to_table_density(self) -> None:
        classifier = DocumentClassifier()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            low_path = self._write_text(
                root,
                "low_tables.html",
                "<html><body><table></table><table></table></body></html>",
            )
            high_path = self._write_text(
                root,
                "high_tables.html",
                "<html><body><table></table><table></table><table></table><table></table></body></html>",
            )
            docs = classifier.find_notes_files([low_path, high_path])
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0].path.name, "high_tables.html")
            self.assertEqual(docs[0].doc_type, "notes")
            self.assertTrue(docs[0].reason.startswith("table_density:"))


if __name__ == "__main__":
    unittest.main()
