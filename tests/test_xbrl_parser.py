import unittest
from pathlib import Path

from src.xbrl_parser import XBRLNoteParser


class XBRLParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_dir = Path("tests/fixtures/trackc_strict_sample")

    def test_parse_fixture_roles(self) -> None:
        parser = XBRLNoteParser(str(self.fixture_dir))
        notes = parser.parse()
        role_codes = sorted(note.role_code for note in notes)
        self.assertEqual(role_codes, ["D834300", "D871100"])

    def test_extract_sga_and_segment_members_from_fixture(self) -> None:
        parser = XBRLNoteParser(str(self.fixture_dir))
        sga = parser.get_sga_accounts()
        members = parser.get_segment_members()

        self.assertIn("dart_SellingGeneralAdministrativeExpenses", sga)
        member_ids = {member["account_id"] for member in members}
        self.assertEqual(
            member_ids,
            {"entity_SemiconductorMember", "entity_DXMember"},
        )


if __name__ == "__main__":
    unittest.main()
